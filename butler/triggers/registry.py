"""触发层统一注册中心（v1.8 P0-5）。

统一管理所有触发源的注册、状态、健康检查，解决"定时任务静默失败"问题。

触发源类型：
- scheduler：APScheduler 定时任务
- rule：Trigger 规则层（事件匹配）
- mqtt：MQTT 事件订阅
- polling：后台轮询任务

每个触发源有：
- 唯一 ID、类型、可读名称
- 状态（running/paused/error/unknown）
- 最后执行时间、最后执行结果、最后耗时
- 成功/失败计数
- 预期执行周期（用于健康检查：超过预期周期未执行标红）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from butler.logging_setup import get_logger

logger = get_logger("butler.triggers.registry")


@dataclass
class TriggerSource:
    """触发源注册信息。"""
    id: str                          # 唯一 ID，如 "sched:anomaly_detector"
    type: str                        # "scheduler" / "rule" / "mqtt" / "polling"
    name: str                        # 可读名称
    status: str = "unknown"          # "running" / "paused" / "error" / "unknown"
    enabled: bool = True
    last_run_at: float = 0.0         # 最后执行时间（epoch）
    last_result: str = ""            # 最后执行结果（"success" / "error: xxx"）
    last_duration_ms: int = 0
    success_count: int = 0
    fail_count: int = 0
    expected_interval_sec: int = 0   # 预期执行周期（秒），0=不检查
    description: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        now = time.time()
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "status": self.status,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at,
            "last_run_ago_sec": int(now - self.last_run_at) if self.last_run_at > 0 else None,
            "last_result": self.last_result,
            "last_duration_ms": self.last_duration_ms,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "expected_interval_sec": self.expected_interval_sec,
            "description": self.description,
            "is_stale": self._is_stale(),
            "extra": self.extra,
        }

    def _is_stale(self) -> bool:
        """判断是否过期（超过预期周期 2 倍未执行）。"""
        if self.expected_interval_sec <= 0:
            return False
        if self.last_run_at <= 0:
            return True  # 从未执行过
        elapsed = time.time() - self.last_run_at
        return elapsed > self.expected_interval_sec * 2


class TriggerRegistry:
    """触发源统一注册中心。"""

    def __init__(self, auditor=None):
        self._sources: dict[str, TriggerSource] = {}
        self._listeners: list[Callable] = []  # 状态变更监听器
        # v1.8 P0-6 触发执行审计器（延迟初始化，避免 import 循环）
        self._auditor = auditor
        self._auditor_inited = False

    @property
    def auditor(self):
        """延迟初始化审计器（首次访问时创建，避免启动时依赖 SQLite）。"""
        if self._auditor is None and not self._auditor_inited:
            try:
                from butler.triggers.audit import TriggerAuditor
                self._auditor = TriggerAuditor()
            except Exception as e:
                logger.debug("trigger auditor lazy init failed: %s", e)
            self._auditor_inited = True
        return self._auditor

    def register(self, source_id: str, source_type: str, name: str,
                 expected_interval_sec: int = 0, description: str = "",
                 enabled: bool = True, extra: dict | None = None) -> TriggerSource:
        """注册触发源。"""
        source = TriggerSource(
            id=source_id,
            type=source_type,
            name=name,
            status="running" if enabled else "paused",
            enabled=enabled,
            expected_interval_sec=expected_interval_sec,
            description=description,
            extra=extra or {},
        )
        self._sources[source_id] = source
        logger.info("trigger source registered: %s (%s, every %ss)",
                    source_id, source_type, expected_interval_sec or "manual")
        return source

    def unregister(self, source_id: str) -> bool:
        """注销触发源。"""
        if source_id in self._sources:
            del self._sources[source_id]
            logger.info("trigger source unregistered: %s", source_id)
            return True
        return False

    def get(self, source_id: str) -> Optional[TriggerSource]:
        """获取触发源。"""
        return self._sources.get(source_id)

    def list_all(self) -> list[TriggerSource]:
        """列出所有触发源。"""
        return list(self._sources.values())

    def list_by_type(self, source_type: str) -> list[TriggerSource]:
        """按类型列出触发源。"""
        return [s for s in self._sources.values() if s.type == source_type]

    def mark_start(self, source_id: str) -> None:
        """标记触发源开始执行。"""
        source = self._sources.get(source_id)
        if source:
            source.status = "running"
            source.last_run_at = time.time()

    def mark_success(self, source_id: str, duration_ms: int = 0) -> None:
        """标记触发源执行成功。"""
        source = self._sources.get(source_id)
        if source:
            source.status = "running"
            source.last_result = "success"
            source.last_duration_ms = duration_ms
            source.success_count += 1
            # v1.8 P0-6 自动记录审计
            if self.auditor:
                try:
                    self.auditor.record(
                        source_id=source_id, source_type=source.type,
                        name=source.name, result="success",
                        duration_ms=duration_ms,
                    )
                except Exception as e:
                    logger.debug("audit record success failed: %s", e)

    def mark_error(self, source_id: str, error: str, duration_ms: int = 0) -> None:
        """标记触发源执行失败。"""
        source = self._sources.get(source_id)
        if source:
            source.status = "error"
            source.last_result = f"error: {error[:200]}"
            source.last_duration_ms = duration_ms
            source.fail_count += 1
            logger.warning("trigger source error: %s - %s", source_id, error[:100])
            # v1.8 P0-6 自动记录审计
            if self.auditor:
                try:
                    self.auditor.record(
                        source_id=source_id, source_type=source.type,
                        name=source.name, result="error",
                        duration_ms=duration_ms, error=error,
                    )
                except Exception as e:
                    logger.debug("audit record error failed: %s", e)

    def pause(self, source_id: str) -> bool:
        """暂停触发源。"""
        source = self._sources.get(source_id)
        if source:
            source.status = "paused"
            source.enabled = False
            logger.info("trigger source paused: %s", source_id)
            return True
        return False

    def resume(self, source_id: str) -> bool:
        """恢复触发源。"""
        source = self._sources.get(source_id)
        if source:
            source.status = "running"
            source.enabled = True
            logger.info("trigger source resumed: %s", source_id)
            return True
        return False

    def get_health_report(self) -> dict:
        """获取健康报告（用于 API 和 WebUI）。"""
        sources = self.list_all()
        stale = [s for s in sources if s._is_stale()]
        errors = [s for s in sources if s.status == "error"]
        paused = [s for s in sources if s.status == "paused"]
        running = [s for s in sources if s.status == "running"]

        return {
            "total": len(sources),
            "running": len(running),
            "paused": len(paused),
            "error": len(errors),
            "stale": len(stale),
            "stale_sources": [s.to_dict() for s in stale],
            "error_sources": [s.to_dict() for s in errors],
            "sources": [s.to_dict() for s in sources],
        }

    def _watch_future(self, source_id: str, result) -> None:
        """把投出去那枚 Future 的结局接回台账（P1-4：投递成功⛔ 等于协程跑成功）。

        mark_success 记的是**投递**这一格——「每轮一行」的心跳口径，⛔ 在这里改口径；
        协程里炸的异常原本没人 .result()，于是状态永远停在 running。
        """
        if not (hasattr(result, "add_done_callback") and hasattr(result, "result")):
            return                       # 同步 job：返回值不是 Future，投递即结果，口径不变

        def _done(fut):
            try:
                fut.result()
            except BaseException as e:   # 含 CancelledError：取消也是「没跑成」
                logger.exception("SCHED_JOB_FAILED %s：投递成功但任务里炸了（此前已记 dispatch success）",
                                 source_id)
                try:
                    self.mark_error(source_id, "%s: %s" % (type(e).__name__, e))
                except Exception:
                    logger.debug("mark_error 二次失败 source=%s", source_id, exc_info=True)

        try:
            result.add_done_callback(_done)
        except Exception as e:
            logger.debug("attach future done-callback 失败 source=%s err=%s", source_id, e)

    def wrap_scheduler_job(self, source_id: str, func: Callable) -> Callable:
        """包装 APScheduler job 函数，自动记录执行状态。

        用法：
            original_func = lambda: asyncio.run_coroutine_threadsafe(coro, loop)
            wrapped = registry.wrap_scheduler_job("sched:anomaly_detector", original_func)
            sched.add_job(wrapped, "interval", minutes=15, id="anomaly_detector")
        """
        def wrapper(*args, **kwargs):
            self.mark_start(source_id)
            start = time.time()
            try:
                result = func(*args, **kwargs)
                duration_ms = int((time.time() - start) * 1000)
                self.mark_success(source_id, duration_ms)
                # P1-4：投递成功之后把 Future 挂上 done-callback，协程里的异常才会落到台账
                self._watch_future(source_id, result)
                return result
            except Exception as e:
                duration_ms = int((time.time() - start) * 1000)
                self.mark_error(source_id, str(e), duration_ms)
                raise
        return wrapper
