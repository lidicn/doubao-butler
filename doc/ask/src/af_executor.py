"""节点执行器 —— 求值段 / 边优先级 / 挂起 / 中断（KICKOFF §4.5，IR §5-§7）。

执行契约：
1. 激活/恢复 → 拉快照 → 初始化求值上下文
2. 按序执行节点，按**边优先级从高到低**匹配下一条边（不允许按定义顺序 fallback）
3. 遇 `wait`/`ask` → 注册计时器 → 挂起，结束求值段
4. 遇 `pass`/终态 → 终止

其它硬约束：
- `do` 单次调用，不重试不降级；失败走 `on_error`，无 `on_error` 则 `failed`
- 实体漂移（引用不存在的实体）→ `on_error` **软失效** + 漂移告警，**不直接 failed**
- `atomic=true` 的 `do` 必须执行完再响应中断
- 取消**不回滚**已执行动作（IR §13-1），清理必须显式写在 `on_cancel` 分支
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .af_adapters import AdapterRegistry, CallResult
from .af_audit import ACTION_FAILED, ENTITY_DRIFT, EVENT_EMITTED, AuditEvent, AuditLog
from .af_bus import ACCEPTED
from .af_conf import ConfidenceStore
from .af_instance import (
    ACTIVE,
    CANCELLED,
    DONE,
    FAILED,
    SUSPENDED,
    Instance,
    InstanceManager,
)
from .af_ir import Automation, Node, evaluate
from .af_ir.expr import ExprError
from .af_state import StateProvider, UnknownEntity, make_resolver
from .af_time import TimeSource, SystemTimeSource, parse_duration

__all__ = [
    "AskSession",
    "NodeExecutor",
    "MAX_STEPS_PER_SEGMENT",
    "classify_answer",
    "EMIT_TIMER_KIND",
]

#: 求值段最大步数——静态图死循环的最后一道防线（静态扫描应提前拦住）
MAX_STEPS_PER_SEGMENT = 1000

#: v0.3.0：`emit` 延迟发布所用的实例定时器 kind（与 `wait`/`ask` 的 timeout 区分）
EMIT_TIMER_KIND = "emit"

_YES_WORDS = ("是", "好", "要", "开", "嗯", "yes", "y", "ok", "okay", "确认", "开吧", "来吧")
_NO_WORDS = ("不", "否", "别", "不用", "不要", "算了", "no", "n", "cancel")


def classify_answer(text: str) -> str:
    """把人类应答归类为 yes / no / default（IR §5.2）。"""
    lowered = str(text).strip().lower()
    if not lowered:
        return "default"
    if any(w in lowered for w in _YES_WORDS):
        return "yes"
    if any(w in lowered for w in _NO_WORDS):
        return "no"
    return "default"


@dataclass
class AskSession:
    """一个挂起中的 `ask` 会话。按 room 维度匹配，创建时间优先，一次应答仅生效一次。"""

    instance_id: str
    node_id: str
    room: str | None
    created_at: float
    prompt: str = ""


@dataclass
class NodeExecutor:
    """7 节点 / 6 边的求值引擎。"""

    instances: InstanceManager
    adapters: AdapterRegistry
    clock: TimeSource = field(default_factory=SystemTimeSource)
    audit: AuditLog = field(default_factory=AuditLog)
    states: StateProvider | None = None
    conf: ConfidenceStore | None = None
    #: v0.3.0 发布侧：自定义事件经总线发布；未注入总线时只审计（便于单测隔离）
    bus: Any | None = None
    #: v0.4.0 订阅侧：发布成功后的回调（Runtime 注入 `scheduler.handle_event`），
    #: 让 emit 出去的事件能真正驱动订阅它的自动化（发布 → 订阅闭环）
    on_emit: Any | None = None

    def __post_init__(self) -> None:
        self.pending_asks: dict[str, AskSession] = {}
        self.node_visits: list[str] = []

    # ─────────────────────────────────────────────────────────────────
    # 主循环
    # ─────────────────────────────────────────────────────────────────
    def run(self, instance: Instance) -> Instance:
        """执行一个求值段：从 `current_node` 走到挂起或终态。"""
        auto = instance.automation
        steps = 0
        while True:
            steps += 1
            if steps > MAX_STEPS_PER_SEGMENT:
                self._fail(instance, "求值段超过最大步数，疑似静态图死循环")
                return instance

            node_id = instance.ctx.current_node
            if not node_id:
                self._terminate(instance)
                return instance

            node = auto.node(node_id)
            instance.trace(node_id, note="enter")
            self.node_visits.append(f"{auto.id}:{node_id}")

            # v0.3.0 发布侧：`emit` 是节点字段，进入节点时先广播（"发出事件后继续"）
            if node.emit is not None and not self._emit(instance, node):
                return instance  # 已转入延迟发布（挂起）

            if node.kind == "pass":
                self._terminate(instance)
                return instance

            if node.kind in ("ask", "wait"):
                self._suspend(instance, node)
                return instance

            kinds = self._execute(instance, node)
            if kinds is None:  # 已终止（失败且无兜底边）
                return instance

            edge = auto.pick_edge(node.id, kinds)
            if edge is None:
                self._terminate(instance)
                return instance

            instance.ctx.current_node = edge.to

            # atomic do 执行完后才响应中断
            if instance.ctx.context.pop("cancel_pending", None) is not None:
                self.cancel(instance, reason=instance.ctx.context.get("cancel_reason", "atomic 结束后取消"))
                return instance

    # ─────────────────────────────────────────────────────────────────
    # 唤醒：应答 / 超时 / 取消 都走这里，统一按边优先级选边
    # ─────────────────────────────────────────────────────────────────
    def resume(self, instance: Instance, kind: str) -> Instance:
        """用给定的边类型唤醒挂起实例（yes / no / default / on_timeout / on_cancel）。"""
        if instance.is_terminal:
            return instance
        auto = instance.automation
        node = auto.node(instance.ctx.current_node)
        self.pending_asks.pop(instance.instance_id, None)

        if instance.state == SUSPENDED:
            self.instances.resume(instance)  # 恢复时重新取快照

        edge = auto.pick_edge(node.id, {kind, "default"})
        if edge is None:
            self._terminate(instance)
            return instance
        instance.ctx.current_node = edge.to
        return self.run(instance)

    def answer(self, room: str | None, text: str) -> Instance | None:
        """人类应答。默认按 room 维度匹配；同房间多实例按创建时间优先；一次应答仅生效一次。"""
        candidates = [s for s in self.pending_asks.values() if s.room == room]
        if not candidates:
            return None
        session = min(candidates, key=lambda s: s.created_at)
        instance = self.instances.get(session.instance_id)
        return self.resume(instance, classify_answer(text))

    def timeout(self, instance: Instance) -> Instance:
        """`ask` 计时到点（无人应答）→ `on_timeout` 兜底。"""
        return self.resume(instance, "on_timeout")

    def resume_then(self, instance: Instance) -> Instance:
        """`wait` 计时到点 → 走 `then` 正常继续（语义拍板 A：wait 是"等一会儿继续"，不是超时）。"""
        return self.resume(instance, "then")

    def cancel(self, instance: Instance, reason: str = "") -> Instance:
        """中断。优先级最高：抢占一切正常流程，走 `on_cancel` 分支。"""
        if instance.is_terminal:
            return instance
        auto = instance.automation
        node = auto.node(instance.ctx.current_node)

        # atomic do：必须执行完再跳转
        if node.kind == "do" and node.atomic and instance.state == ACTIVE:
            instance.ctx.context["cancel_pending"] = True
            instance.ctx.context["cancel_reason"] = reason
            return instance

        instance.ctx.context["pending_terminal"] = CANCELLED
        instance.ctx.context["cancel_reason"] = reason
        return self.resume(instance, "on_cancel")

    # ─────────────────────────────────────────────────────────────────
    # 节点执行
    # ─────────────────────────────────────────────────────────────────
    def _execute(self, instance: Instance, node: Node) -> Iterable[str] | None:
        """执行单个节点，返回可用边类型集合；返回 None 表示实例已终止。"""
        self._reject_reserved(node)

        if node.kind == "on":
            return {"then"}

        if node.kind == "if":
            try:
                value = evaluate(node.expr or {}, self._resolver(instance))
            except (UnknownEntity, ExprError, KeyError, ValueError) as exc:
                return self._soft_fail(instance, node, exc)
            return {"then"} if value else {"no", "default"}

        if node.kind == "set":
            value = self._value_of(instance, node)
            self.instances.set_var(instance, node.var, value)  # type: ignore[arg-type]
            return {"then"}

        if node.kind == "do":
            return self._do(instance, node)

        raise ValueError(f"未知节点类型：{node.kind}")

    def _do(self, instance: Instance, node: Node) -> Iterable[str] | None:
        """`do`：**单次调用**，不重试不降级。"""
        try:
            adapter = self.adapters.get(node.adapter or "")
        except Exception as exc:  # 适配器未注册
            return self._soft_fail(instance, node, exc)

        # G4 canary：conf 处于 auto 带且节点标了 canary → 走灰度保护
        # ⚠️ dry-run 适配器（G1 默认不写真机）不改状态，会**永远**被判定为漂移，必须跳过
        canary = node.canary
        dry_run = bool(getattr(adapter, "dry_run", False))
        use_canary = (
            canary
            and not dry_run
            and self.conf is not None
            and self.conf.band(instance.automation.id) == "auto"
            and self.states is not None
        )
        if use_canary:
            from .af_canary import CanaryGuard

            guard = CanaryGuard(
                self.states,
                auto_rollback=bool(canary.get("auto_rollback", True)) if isinstance(canary, dict) else True,
            )
            wrapped = guard.perform(adapter, node.action or "", dict(node.params))
            if wrapped.has_drift():
                rolled = guard.check_and_rollback(adapter, wrapped)
                self.audit.add(
                    AuditEvent(
                        type=ENTITY_DRIFT,
                        at=self.clock.now(),
                        message=f"canary 检测到漂移 {node.action}，已自动回滚（{len(rolled)} 次反向下发）",
                        automation_id=instance.automation.id,
                        instance_id=instance.instance_id,
                        node_id=node.id,
                        data={"params": dict(node.params)},
                    )
                )
                return self._soft_fail(instance, node, RuntimeError(f"canary 漂移，已回滚 {node.action}"))
            result = wrapped.result
        else:
            result: CallResult = adapter.call(node.action or "", dict(node.params))

        # 结果只进 vars，不回写快照（IR §7.2）
        if node.result_var:
            instance.ctx.vars[node.result_var] = {
                "success": result.success,
                "data": result.data,
                "error": result.error,
            }

        if result.success:
            return {"then"}

        self.audit.add(
            AuditEvent(
                type=ACTION_FAILED,
                at=self.clock.now(),
                message=f"动作 {node.adapter}.{node.action} 失败：{result.error}",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=node.id,
                data={"params": dict(node.params)},
            )
        )
        kinds = ["on_error", "default"]
        if instance.automation.pick_edge(node.id, kinds) is None:
            self._fail(instance, f"{node.action} 失败且无 on_error/default 兜底：{result.error}")
            return None
        return set(kinds)

    def _soft_fail(self, instance: Instance, node: Node, exc: BaseException) -> Iterable[str] | None:
        """实体漂移 / 表达式错误 → 软失效：走 `on_error`，无则 failed（**不直接 failed**）。"""
        entity_id = exc.args[0] if isinstance(exc, UnknownEntity) and exc.args else ""
        self.audit.add(
            AuditEvent(
                type=ENTITY_DRIFT,
                at=self.clock.now(),
                message=f"节点 {node.id} 求值失败（实体漂移或类型错误）：{exc}",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=node.id,
                entity_id=str(entity_id),
            )
        )
        kinds = ["on_error", "default"]
        if instance.automation.pick_edge(node.id, kinds) is None:
            self._fail(instance, f"节点 {node.id} 软失效且无 on_error/default 兜底：{exc}")
            return None
        return set(kinds)

    # ─────────────────────────────────────────────────────────────────
    # 挂起
    # ─────────────────────────────────────────────────────────────────
    def _suspend(self, instance: Instance, node: Node) -> None:
        duration = None
        if node.kind == "wait":
            duration = parse_duration(node.duration)  # type: ignore[arg-type]
        elif node.timeout:
            duration = parse_duration(node.timeout)

        self.instances.suspend(instance, node.id, duration, kind=node.kind)

        if node.kind == "ask":
            self.pending_asks[instance.instance_id] = AskSession(
                instance_id=instance.instance_id,
                node_id=node.id,
                room=node.room,
                created_at=self.clock.monotonic(),
                prompt=node.prompt,
            )

    # ─────────────────────────────────────────────────────────────────
    # 终止
    # ─────────────────────────────────────────────────────────────────
    def _terminate(self, instance: Instance) -> None:
        """走到终点。若之前请求过取消，则落到 cancelled（取消不回滚，只标记状态）。"""
        pending = instance.ctx.context.pop("pending_terminal", None)
        if pending == CANCELLED:
            if instance.state == SUSPENDED:
                self.instances.resume(instance)
            self.instances.cancel(instance, instance.ctx.context.get("cancel_reason", ""))
        else:
            if instance.state == SUSPENDED:
                self.instances.resume(instance)
            self.instances.done(instance)

    def _fail(self, instance: Instance, reason: str) -> None:
        if instance.state == SUSPENDED:
            self.instances.resume(instance)
        self.instances.fail(instance, reason)

    # ─────────────────────────────────────────────────────────────────
    # 工具
    # ─────────────────────────────────────────────────────────────────
    def _resolver(self, instance: Instance):
        snapshot = instance.snapshot
        if snapshot is None:  # 防御：正常流程下 spawn/resume 都会带快照
            snapshot = self.instances.states.snapshot(sorted(instance.automation.reads()))
        return make_resolver(snapshot, instance.ctx.vars, instance.ctx.context)

    def _value_of(self, instance: Instance, node: Node) -> Any:
        """`set` 节点的取值：优先 `from`（变量引用），否则 `value` 字面量。"""
        if node.from_:
            return self._resolver(instance)(node.from_, None)
        return node.value

    @staticmethod
    def _reject_reserved(node: Node) -> None:
        """保留位：读到即报未实现，不静默忽略（v0.3.0 起只剩 `fn`）。"""
        if node.reserved:
            keys = ", ".join(sorted(node.reserved))
            raise NotImplementedError(f"未实现节点保留字段（{keys}）：{node.id}")

    # ─────────────────────────────────────────────────────────────────
    # v0.3.0 跨自动化事件 · 发布侧（IR §4.3）
    # ─────────────────────────────────────────────────────────────────
    def _emit(self, instance: Instance, node: Node) -> bool:
        """发布 `emit` 声明的自定义事件。

        返回 False 表示实例已挂起（带 `delay` 的延迟发布），调用方应结束求值段。
        `delay` 走实例定时器 + `TimeSource`，因此时间旅行可测（禁止 `time.sleep`）。
        """
        emit = node.emit
        if emit is None:
            return True

        if emit.delay:
            delay_s = parse_duration(emit.delay)
            if delay_s > 0:
                # 待发布信息必须可 JSON 序列化（IR §3 红线）
                instance.ctx.context["pending_emit"] = {
                    "node": node.id,
                    "event": emit.event,
                    "data": dict(emit.data),
                }
                self.instances.suspend(instance, node.id, delay_s, kind=EMIT_TIMER_KIND)
                return False

        self._publish_emit(instance, node, emit.event, dict(emit.data))
        return True

    def emit_due(self, instance: Instance) -> Instance:
        """`emit` 的延迟到点：发布事件后按 `then` 继续流转（**不是** `on_timeout`）。"""
        if instance.is_terminal:
            return instance
        auto = instance.automation
        node = auto.node(instance.ctx.current_node)
        pending = instance.ctx.context.pop("pending_emit", None)
        if pending is not None:
            self._publish_emit(
                instance, node, str(pending.get("event", "")), dict(pending.get("data") or {})
            )
        if instance.state == SUSPENDED:
            self.instances.resume(instance)
        edge = auto.pick_edge(node.id, {"then", "default"})
        if edge is None:
            self._terminate(instance)
            return instance
        instance.ctx.current_node = edge.to
        return self.run(instance)

    def _publish_emit(self, instance: Instance, node: Node, event: str, data: dict) -> None:
        """实际发布 + 审计。

        ⚠️ 被限流（`throttled`）或熔断（`breaker_open`）**不视为失败**——
        那是系统保护，不是自动化逻辑错误，因此**不进 `on_error`**，只落审计。
        """
        result = "no_bus"
        if self.bus is not None:
            result = self.bus.publish_custom(event, data)
            # v0.4.0：发布成功后交给调度器，驱动 `on event` 订阅者（emit → on event 闭环）
            if result == ACCEPTED and self.on_emit is not None and self.bus.emitted:
                self.on_emit(self.bus.emitted[-1])
        self.audit.add(
            AuditEvent(
                type=EVENT_EMITTED,
                at=self.clock.now(),
                message=f"发布事件 {event}（结果：{result}）",
                automation_id=instance.automation.id,
                instance_id=instance.instance_id,
                node_id=node.id,
                data={"event": event, "result": result, "keys": sorted(data)},
            )
        )
