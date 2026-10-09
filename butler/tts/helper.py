"""TTS 队列便捷函数：其他模块直接调用，不用关心单例。"""
from __future__ import annotations

from butler.logging_setup import get_logger
from butler.tts.singleton import get_queue

logger = get_logger("butler.tts.helper")


def _current_trace_id() -> str:
    """从 contextvar 取当前 trace_id（agent.py 里设置的）。"""
    try:
        from butler.core.agent import _current_trace_id
        return _current_trace_id.get() or ""
    except Exception:
        return ""


def enqueue_tts(text: str, *, priority: int = 3, device_id: str | None = None,
                room: str = "", member: str = "", override_quiet: bool = True,
                trace_id: str = "") -> bool:
    """往 TTS 队列塞一条消息。队列未初始化时 fallback 返回 False。"""
    q = get_queue()
    if q is None:
        logger.warning("TTS queue not initialized, enqueue skipped: %.30s", text)
        return False
    tid = trace_id or _current_trace_id()
    r = q.enqueue(text, priority=priority, device_id=device_id,
                  room=room, member=member, override_quiet=override_quiet,
                  trace_id=tid)
    if r.accepted:
        logger.info("TTS enqueued P%d trace=%s: %.30s", priority, tid, text)
    else:
        logger.info("TTS enqueue rejected (%s): %.30s", r.reason, text)
    return r.accepted
