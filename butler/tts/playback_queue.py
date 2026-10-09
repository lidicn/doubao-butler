"""Per-device TTS 播放队列：同一个音箱的 TTS 请求串行执行，不重叠。

设计：
- 每个 device_id 一个 asyncio.Queue + 一个消费 task
- 队列项 = (TTSResult, url, trace_id)：号随条目本体走，⛔ 指望 worker 的 contextvar 继承
- 消费顺序：合成完成 → 打给 HA → 等播放间隔 → 下一个
- 不同 device_id 之间完全并发，互不阻塞
- 队列长度上限 5，超出丢最旧的（防内存膨胀）
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

from butler.core.trace_ctx import current_trace_id
from butler.logging_setup import get_logger
from butler.tts.base import TTSResult

logger = get_logger("butler.tts.queue")

MAX_QUEUE_PER_DEVICE = 5
DEFAULT_PLAY_GAP_S = 2.0  # 播完一条后等 2s 再播下一条


class PlaybackQueue:
    """Per-device 串行播放队列。"""

    def __init__(self, ha_client=None, play_gap_s: float = DEFAULT_PLAY_GAP_S):
        self._ha = ha_client
        self._gap = play_gap_s
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        # 格14（R1）：原名 _lock。它守的是「这台设备的队列＋消费 task 只建一次」
        # （_ensure_worker），不守播放顺序；守播放顺序的是 butler/tts/queue.py TTSQueue._play_lock。
        self._worker_lock = asyncio.Lock()

    def set_ha(self, ha_client) -> None:
        """运行时注入 HA client（启动后才可用时调用）。"""
        self._ha = ha_client

    async def _ensure_worker(self, device_id: str) -> asyncio.Queue:
        async with self._worker_lock:
            q: asyncio.Queue | None = self._queues.get(device_id)
            if q is None:
                q = asyncio.Queue(maxsize=MAX_QUEUE_PER_DEVICE)
                self._queues[device_id] = q
            task = self._workers.get(device_id)
            if task is None or task.done():
                if task is not None:
                    # V35：旧形状只查 `_queues` ⇒ worker 死后 enqueue 照样返回 True，而这条队没人消费
                    logger.error("playback worker died on %s, restarting", device_id)
                    if not task.cancelled():
                        exc = task.exception()
                        if exc is not None:
                            logger.error("playback worker %s death cause: %r", device_id, exc)
                self._workers[device_id] = asyncio.create_task(
                    self._consume(device_id, q),
                    name=f"tts-play-{device_id}",
                )
            return q

    async def _consume(self, device_id: str, q: asyncio.Queue) -> None:
        """逐消费一个设备的播放队列。"""
        logger.info("playback queue worker started: %s", device_id)
        while True:
            try:
                result, url, trace_id = await q.get()
                # 这条 task 的上下文是「第一条 enqueue」那一刻的快照，之后进来的条目各属各的调用方
                token = current_trace_id.set(trace_id) if trace_id else None
                try:
                    await self._play_one(device_id, result, url)
                except Exception as e:
                    logger.warning("playback error on %s: %s", device_id, e)
                finally:
                    q.task_done()
                    if token is not None:
                        current_trace_id.reset(token)
            except asyncio.CancelledError:
                logger.info("playback queue worker stopped: %s", device_id)
                break
            except Exception as e:
                logger.error("playback queue fatal on %s: %s", device_id, e)
                await asyncio.sleep(1)

    async def _play_one(self, device_id: str, result: TTSResult, url: str) -> None:
        """播放一条音频到指定音箱。"""
        if self._ha is None:
            logger.warning("playback skipped: HA not ready, device=%s", device_id)
            return
        t0 = time.monotonic()
        try:
            method = await self._ha.resolve_play_method(device_id)
            rj = await self._ha.play_xiaomi_url_once(device_id, url, method=method)
            code = rj.get("code")
            ok = str(code) == "0"
            logger.info("played on %s: %.30s (%.1fs) code=%s ok=%s",
                       device_id, result.voice, time.monotonic() - t0, code, ok)
            if not ok:
                logger.warning("play xiaomi returned non-zero: %s", rj)
            # music 模式播完自动停，防止单曲循环
            if ok and method == 'music' and hasattr(self._ha, 'schedule_xiaomi_stop'):
                # 估算播报时长：约 0.3s/字 + 缓冲
                est_sec = max(5, len(getattr(result, 'text', '') or '') * 0.3 + 2)
                self._ha.schedule_xiaomi_stop(device_id, est_sec)
        except Exception as e:
            logger.warning("play failed on %s: %s", device_id, e)
            raise
        # 播放间隔：让音箱真正播完再放第二条
        await asyncio.sleep(self._gap)

    async def enqueue(self, device_id: str, result: TTSResult, trace_id: str = "") -> bool:
        """把一条合成好的音频排入设备播放队列。

        ``trace_id``＝调用方那一刻的号，随条目走（worker task 的上下文不跟着条目换）。

        Returns:
            True=入队成功, False=队列满被丢弃
        """
        if not device_id or result is None:
            return False
        q = await self._ensure_worker(device_id)
        url = result.public_url
        try:
            q.put_nowait((result, url, trace_id))
            logger.debug("enqueued to %s (queue=%d): %.30s", device_id, q.qsize(), result.voice)
            return True
        except asyncio.QueueFull:
            # 队列满：丢最旧的一条，腾位置
            try:
                old_result, _, _ = q.get_nowait()
                logger.warning("playback queue full on %s, dropping oldest: %.30s",
                               device_id, old_result.voice)
                q.task_done()
                q.put_nowait((result, url, trace_id))
                return True
            except Exception:
                logger.error("playback queue full on %s and drain failed", device_id)
                return False

    def queue_size(self, device_id: str) -> int:
        q = self._queues.get(device_id)
        return q.qsize() if q else 0

    async def shutdown(self) -> None:
        """停止所有 worker。"""
        for device_id, task in self._workers.items():
            task.cancel()
        for task in self._workers.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._workers.clear()
        self._queues.clear()
        logger.info("playback queue shutdown complete")
