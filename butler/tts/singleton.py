"""TTS 队列单例：全局唯一的队列实例，供所有调用方使用。"""
from __future__ import annotations

import asyncio

from butler.logging_setup import get_logger
from butler.tts.adapter import TTSManagerSpeaker
from butler.tts.queue import TTSQueue, TTSQueueConfig
from butler.tts.manager import TTSManager

logger = get_logger("butler.tts.queue_singleton")

_queue: TTSQueue | None = None
_manager: TTSManager | None = None

_worker_tasks: set[asyncio.Task] = set()   # 长生命周期 worker 的强引用（第五轮 P0-7）


def _on_worker_done(task: asyncio.Task) -> None:
    """worker 退出：先从注册表摘掉；异常必须出声（旧码＝异常留在 task 对象里，没人 await＝⛔ 响）。"""
    _worker_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("TTSQueue worker died: %s: %s", type(exc).__name__, exc)


def _audit_expiry_to_guard(item, now: float) -> None:
    """DCD 裁定③：队列的过期审计写进风控那张 `push_audit`（晚绑定）。

    为什么每次现取、⛔ 构造期注入：`app.py:450` 才挂 `rt.push_guard`，
    队列在 `init_queue` 时就建好了 ⇒ 构造期拿到的必然是 None（与裁定②那三道门同一份口径）。
    """
    try:
        from butler.runtime import get_runtime
        pg = getattr(get_runtime(), "push_guard", None)
        if pg is None:
            return
        from butler.tts.queue import level_name_for
        created = float(getattr(item, "created_at", 0.0) or 0.0)
        expires = float(getattr(item, "expires_at", 0.0) or 0.0)
        pg.record_ttl_drop(
            text=str(getattr(item, "text", "") or ""),
            priority=level_name_for(int(getattr(item, "priority", 3))),
            age_s=(now - created) if created else 0.0,
            ttl_s=(expires - created) if (expires and created) else 0.0,
            job_id=str(getattr(item, "job_id", "") or ""),
            trace_id=str(getattr(item, "trace_id", "") or ""),
        )
    except Exception as e:
        logger.warning("TTS TTL 审计投递失败（不阻塞）: %s: %s", type(e).__name__, e)


def init_queue(manager: TTSManager, config: TTSQueueConfig | None = None,
           on_expired=None) -> TTSQueue:
    """初始化全局 TTS 队列。在应用启动时调用一次。"""
    global _queue, _manager
    if _queue is not None:
        logger.warning("TTSQueue already initialized, skipping")
        return _queue
    _manager = manager
    speaker = TTSManagerSpeaker(manager)
    # DCD 裁定③：默认就把审计腿接上——现网只在这里建一次队列，
    # ⛔ 靠调用方记得传参（那正是 `PRIORITY_TTL` 当年没人读的原因）。
    _queue = TTSQueue(speaker=speaker, config=config,
                    on_expired=on_expired or _audit_expiry_to_guard)
    logger.info("TTSQueue initialized (overload_threshold=%d, quiet=%s-%s)",
                (config or TTSQueueConfig()).overload_threshold,
                (config or TTSQueueConfig()).quiet_start,
                (config or TTSQueueConfig()).quiet_end)
    # 启动后台 worker（主事件循环已在跑）。task 必须存进 _worker_tasks：CPython 对事件循环里的
    # 任务只持弱引用，返回值不保存就可能在跑完前被 GC 回收＝队列静默停止消费（第五轮 P0-7）。
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_queue.run())
        _worker_tasks.add(task)
        task.add_done_callback(_on_worker_done)
        logger.info("TTSQueue worker started")
    except RuntimeError:
        logger.warning("TTSQueue run() called outside event loop, worker not started")
    return _queue


def get_queue() -> TTSQueue | None:
    """获取全局 TTS 队列实例。未初始化时返回 None。"""
    return _queue
