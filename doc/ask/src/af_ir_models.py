"""AutoForge IR 数据模型。

铁律（IR_AND_RUNTIME §1）：**Graph 是唯一真相**。
- 序列化的唯一真相是 `schema/ir.schema.json`（JSON Schema）
- 本模块的 dataclass 只是它的 **Python 投影**，方便运行时操作
- 增删字段必须**先改 schema 再改这里**，反过来即违反铁律

已实现（原 P1 预留）：`emit`（v0.3.0 跨自动化事件·**发布侧**，节点字段形态，见 `EmitDecl`）、
`persist`（实例持久化与崩溃恢复，见 `af_persist`）。

仍为保留位：`fn` —— 允许出现在 IR 中但 Runtime 读到即报未实现（属 v1.0.0 评估范围）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import jsonschema
from jsonschema import Draft202012Validator

__all__ = [
    "IR_VERSION",
    "NODE_KINDS",
    "EDGE_KINDS",
    "EDGE_PRIORITY",
    "VAR_TYPES",
    "IRValidationError",
    "EmitDecl",
    "Trigger",
    "Node",
    "Edge",
    "VarDecl",
    "Automation",
    "Graph",
    "load_automation",
    "load_graph",
    "validate_automation",
]

IR_VERSION = "0.2.1"

#: 7 种节点（fn 为远期预留，G1 不实现）
NODE_KINDS = ("on", "if", "do", "ask", "wait", "set", "pass")

#: 6 种边（`on_error` 是 v0.2 新增的第 6 种）
EDGE_KINDS = ("then", "yes", "no", "default", "on_timeout", "on_cancel", "on_error")

#: 边优先级（冲突消解，强制）：中断 > 失败 > 超时 > 明确应答/正常流转 > 兜底
#: 注意 `yes`/`no` 与 `then` 同层；`default` 永远最低
EDGE_PRIORITY: tuple[str, ...] = (
    "on_cancel",
    "on_error",
    "on_timeout",
    "yes",
    "no",
    "then",
    "default",
)

VAR_TYPES = ("numeric", "boolean", "string", "enum")

SCHEMA_PATH = Path(__file__).parent / "schema" / "ir.schema.json"

# 运行时读到即报未实现的保留字段（`emit` 已于 v0.3.0 实现，`persist` 于 P1 实现）
_RESERVED_NODE_KEYS = ("fn",)


class IRValidationError(Exception):
    """IR 未通过 JSON Schema 校验。"""

    def __init__(self, message: str, errors: Sequence[Any] = ()):
        super().__init__(message)
        self.errors = list(errors)


# ─────────────────────────────────────────────────────────────────────
# 组件
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Trigger:
    """`on` 节点的触发源。

    类型：
        state —— 实体状态跃变（`to`/`from`），可叠加 `for` 做持续条件
        sun   —— 太阳历事件（sunrise/sunset，可带 offset）
        time  —— 定时（`at` = "HH:MM"）
        group —— 多源组合（and/or），避免图膨胀
    """

    type: str
    entity_id: str | None = None
    from_: str | None = None
    to: str | None = None
    event: str | None = None
    offset: str | None = None
    at: str | None = None
    op: str | None = None
    sources: tuple["Trigger", ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Trigger":
        return cls(
            type=data["type"],
            entity_id=data.get("entity_id"),
            from_=data.get("from"),
            to=data.get("to"),
            event=data.get("event"),
            offset=data.get("offset"),
            at=data.get("at"),
            op=data.get("op"),
            sources=tuple(cls.from_dict(s) for s in data.get("sources", ())),
        )

    def entity_ids(self) -> set[str]:
        """该触发源引用的全部实体（含 group 递归）。

        v0.4.0：`event` 类型不是实体，但在总线中的主体为 `event.<name>`，
        一并返回——这样跨自动化依赖矩阵与 `EMIT_SELF_LOOP` 环检测能覆盖**事件边**。
        """
        out: set[str] = set()
        if self.type == "event" and self.event:
            from ..af_bus import EVENT_ENTITY_PREFIX  # 局部导入：避免 af_ir 模块级依赖 af_bus

            out.add(f"{EVENT_ENTITY_PREFIX}{self.event}")
        if self.entity_id:
            out.add(self.entity_id)
        for sub in self.sources:
            out |= sub.entity_ids()
        return out

    def leaf_triggers(self) -> Iterator["Trigger"]:
        """展开 group，yield 出所有叶子触发源。"""
        if self.type == "group":
            for sub in self.sources:
                yield from sub.leaf_triggers()
        else:
            yield self


@dataclass(frozen=True)
class EmitDecl:
    """跨自动化事件·**发布侧**声明（v0.3.0，IR §4.3）。

    只传消息，**禁止跨自动化读写对方私有变量**——`data` 只接受可 JSON 序列化的值。
    `delay` 非空时走实例定时器（与 `wait` 同口径、经 `TimeSource`），保证时间旅行可测。
    """

    event: str
    data: dict[str, Any] = field(default_factory=dict)
    delay: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EmitDecl":
        return cls(
            event=data["event"],
            data=dict(data.get("data") or {}),
            delay=data.get("delay"),
        )


@dataclass(frozen=True)
class Node:
    """7 种节点之一。字段按 kind 取用，未用到的为 None。"""

    id: str
    kind: str
    name: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # on
    trigger: Trigger | None = None
    for_: str | None = None
    debounce: str | None = None
    # if
    expr: dict[str, Any] | None = None
    # do
    adapter: str | None = None
    action: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    atomic: bool = False
    result_var: str | None = None
    requires_confirm: bool = False
    canary: dict[str, Any] | None = None
    # ask
    prompt: str = ""
    session: str = "room"
    room: str | None = None
    timeout: str | None = None
    # wait
    duration: str | None = None
    # set
    var: str | None = None
    value: Any = None
    from_: str | None = None
    # emit（v0.3.0 跨自动化事件·发布侧）
    emit: EmitDecl | None = None
    # 保留位
    reserved: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Node":
        return cls(
            id=data["id"],
            kind=data["kind"],
            name=data.get("name", ""),
            raw=dict(data),
            trigger=Trigger.from_dict(data["trigger"]) if data.get("trigger") else None,
            for_=data.get("for"),
            debounce=data.get("debounce"),
            expr=data.get("expr"),
            adapter=data.get("adapter"),
            action=data.get("action"),
            params=dict(data.get("params") or {}),
            atomic=bool(data.get("atomic", False)),
            result_var=data.get("result_var"),
            requires_confirm=bool(data.get("requires_confirm", False)),
            canary=data.get("canary"),
            prompt=data.get("prompt", ""),
            session=data.get("session", "room"),
            room=data.get("room"),
            timeout=data.get("timeout"),
            duration=data.get("duration"),
            var=data.get("var"),
            value=data.get("value"),
            from_=data.get("from"),
            emit=EmitDecl.from_dict(data["emit"]) if data.get("emit") else None,
            reserved={k: data[k] for k in _RESERVED_NODE_KEYS if k in data},
        )

    @property
    def address(self) -> str:
        """可寻址形式：`kind:node_id`（跨 automation 时用 `automation_id:node_id`）。"""
        return f"{self.kind}:{self.id}"

    @property
    def is_suspending(self) -> bool:
        """是否挂起点（ask/wait）。"""
        return self.kind in ("ask", "wait")

    def target_entities(self) -> set[str]:
        """`do` 节点写入的实体（`params.entity_id`，支持 str 与 list）。"""
        if self.kind != "do":
            return set()
        raw = self.params.get("entity_id")
        if raw is None:
            return set()
        if isinstance(raw, str):
            return {raw}
        if isinstance(raw, (list, tuple)):
            return {str(x) for x in raw}
        return set()


@dataclass(frozen=True)
class Edge:
    """6 种边之一。同节点同优先级边不可重复定义（扫描器检查）。"""

    from_: str
    to: str
    kind: str
    id: str = ""
    label: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Edge":
        return cls(
            from_=data["from"],
            to=data["to"],
            kind=data["kind"],
            id=data.get("id", ""),
            label=data.get("label", ""),
        )

    @property
    def priority(self) -> int:
        """数值越小优先级越高（见 EDGE_PRIORITY）。"""
        return EDGE_PRIORITY.index(self.kind)


@dataclass(frozen=True)
class VarDecl:
    """实例变量声明：类型必须显式，禁止隐式转换（IR §3.1 / §14-10）。"""

    type: str
    value: Any = None
    enum: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VarDecl":
        return cls(
            type=data["type"],
            value=data.get("value"),
            enum=tuple(data.get("enum", ())),
        )


@dataclass
class Automation:
    """静态模板（Graph 描述的规则）。无状态、可版本化。"""

    id: str
    name: str
    version: int
    mode: str
    nodes: dict[str, Node]
    edges: tuple[Edge, ...]
    snapshot: bool = True
    confidence: float | None = None
    persist: bool = False
    enabled: bool = True
    vars: dict[str, VarDecl] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # ── 构造 ──────────────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Automation":
        validate_automation(data)
        nodes = {n["id"]: Node.from_dict(n) for n in data["nodes"]}
        _check_unique_ids(data.get("id", "?"), data["nodes"], nodes)
        edges = tuple(Edge.from_dict(e) for e in data.get("edges", ()))
        raw = dict(data)
        raw["enabled"] = bool(data.get("enabled", True))  # 显式落位，保证 round-trip 一致
        auto = cls(
            id=data["id"],
            name=data["name"],
            version=int(data.get("version", 1)),
            mode=data.get("mode", "single"),
            nodes=nodes,
            edges=edges,
            snapshot=bool(data.get("snapshot", True)),
            confidence=data.get("confidence"),
            persist=bool(data.get("persist", False)),
            enabled=bool(data.get("enabled", True)),
            vars={k: VarDecl.from_dict(v) for k, v in (data.get("vars") or {}).items()},
            meta=dict(data.get("meta") or {}),
            raw=raw,
        )
        _check_edges(auto)
        return auto

    # ── 图查询 ────────────────────────────────────────────────────────
    def node(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def entry_nodes(self) -> list[Node]:
        """入口节点 = 所有 `on` 节点。"""
        return [n for n in self.nodes.values() if n.kind == "on"]

    def outgoing(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.from_ == node_id]

    def edge_of(self, node_id: str, kind: str) -> Edge | None:
        for e in self.outgoing(node_id):
            if e.kind == kind:
                return e
        return None

    def pick_edge(self, node_id: str, kinds: Iterable[str]) -> Edge | None:
        """在给定可用边类型中，按 EDGE_PRIORITY 取优先级最高的一条。

        **不允许按定义顺序 fallback**——永远按优先级从高到低匹配。
        """
        wanted = set(kinds)
        candidates = [e for e in self.outgoing(node_id) if e.kind in wanted]
        if not candidates:
            return None
        return min(candidates, key=lambda e: (e.priority, e.to))

    # ── 实体依赖（跨自动化环检测用）────────────────────────────────────
    def reads(self) -> set[str]:
        """读到的实体：触发源 + 表达式里的 `entity.*`（用于快照预取）。"""
        from .expr import collect_entity_refs  # 局部导入避免循环

        out: set[str] = set()
        for node in self.nodes.values():
            if node.trigger is not None:
                out |= node.trigger.entity_ids()
            if node.expr is not None:
                out |= collect_entity_refs(node.expr)
        return out

    def trigger_entities(self) -> set[str]:
        """**触发源**引用的实体——跨自动化依赖矩阵只看这个。

        为什么不用 `reads()`：条件里读同一个实体（如"灯是关的"）不会造成隐式循环，
        只有"状态变了会重新触发对方"才会。用 reads() 会把 case01 这类正常自动化误判成环。
        """
        out: set[str] = set()
        for node in self.entry_nodes():
            if node.trigger is not None:
                out |= node.trigger.entity_ids()
        return out

    def writes(self) -> set[str]:
        """写到的实体：所有 `do` 节点的目标。"""
        out: set[str] = set()
        for node in self.nodes.values():
            out |= node.target_entities()
        return out

    def emitted_events(self) -> set[str]:
        """`emit` 发布的事件名（v0.3.0；供自触发环检测与事件风暴上限使用）。"""
        return {n.emit.event for n in self.nodes.values() if n.emit is not None}

    def emit_nodes(self) -> list[Node]:
        """所有携带 `emit` 的节点。"""
        return [n for n in self.nodes.values() if n.emit is not None]

    # ── v1.2.0 后置条件断言 ────────────────────────────────────────────
    def expects(self) -> tuple[Mapping[str, Any], ...]:
        """IR 顶层 `expect`：自动化自己声明的「跑完之后应该是什么」。"""
        return tuple(self.raw.get("expect") or ())

    def expect_entities(self) -> set[str]:
        """`expect` 里实体形态断言引用的实体（供静态扫描校验可达性）。"""
        out: set[str] = set()
        for item in self.expects():
            entity_id = item.get("entity_id")
            if entity_id:
                out.add(str(entity_id))
        return out

    def address_of(self, node_id: str) -> str:
        return f"{self.id}:{node_id}"


@dataclass
class Graph:
    """多个 Automation 的集合——跨自动化分析（依赖环、并发配额）的入口。"""

    automations: list[Automation] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_id: dict[str, Automation] = {a.id: a for a in self.automations}

    def get(self, automation_id: str) -> Automation:
        return self._by_id[automation_id]

    def __iter__(self) -> Iterator[Automation]:
        return iter(self.automations)

    def __len__(self) -> int:
        return len(self.automations)


# ─────────────────────────────────────────────────────────────────────
# 校验与加载
# ─────────────────────────────────────────────────────────────────────


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_automation(data: Mapping[str, Any]) -> None:
    """按 JSON Schema 校验单条 Automation，失败抛 IRValidationError。"""
    validator = Draft202012Validator(_schema())
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors[:10]
        )
        raise IRValidationError(f"IR 校验失败：{detail}", errors)


def load_automation(data: Mapping[str, Any]) -> Automation:
    return Automation.from_dict(data)


def load_graph(source: str | Path | Mapping[str, Any]) -> Graph:
    """从路径或 dict 加载 Graph。

    支持两种形态：
    - 单条 automation：`{"id": ..., "nodes": [...]}`
    - 多条容器：`{"automations": [ ... ]}`（跨自动化分析用，如依赖环检测）
    """
    if isinstance(source, (str, Path)):
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        data = dict(source)

    if "automations" in data:
        return Graph([Automation.from_dict(a) for a in data["automations"]])
    return Graph([Automation.from_dict(data)])


# ─────────────────────────────────────────────────────────────────────
# 内部结构自检（schema 之外的语义约束）
# ─────────────────────────────────────────────────────────────────────


def _check_unique_ids(auto_id: str, raw_nodes: Any, nodes: dict[str, Node]) -> None:
    if len(raw_nodes) != len(nodes):
        raise IRValidationError(f"automation `{auto_id}` 存在重复 node id")


def _check_edges(auto: Automation) -> None:
    for e in auto.edges:
        if e.from_ not in auto.nodes:
            raise IRValidationError(f"automation `{auto.id}` 的边 {e.from_}→{e.to} 起点不存在")
        if e.to not in auto.nodes:
            raise IRValidationError(f"automation `{auto.id}` 的边 {e.from_}→{e.to} 终点不存在")
