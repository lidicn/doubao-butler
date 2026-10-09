"""批150 验收门（v2.6#2 补齐腿）：播报的号随条目走，⛔ 指望 worker 的上下文继承。

现网证据（88h 冻结样本 103,321 行，台账 §123）：
  `tts.speak queued` 15/15、`played on` 15/15 一条 trace 都不带 ⇒
  「这句话是谁触发播的」到音箱那一行是断的；而 `emit xiaomi` 那条旁路 180/180 全带号。
本门钉两条管子：
  1) PlaybackQueue：号随 TTSResult 一起进队，worker 取一条就把这条 task 的上下文换成它的号，播完复位；
  2) NotifyRouter：不在对话链上的推送（定时/主动/巡检）当场生一个号，随通知走到 TTS/bark 腿。
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "butler").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

from butler.core.trace_ctx import current_trace_id  # noqa: E402
from butler.notify.router import NotifyRouter  # noqa: E402
from butler.tts import playback_queue  # noqa: E402
from butler.tts.base import TTSResult  # noqa: E402
from butler.tts.playback_queue import PlaybackQueue  # noqa: E402

HEX8 = re.compile(r"^[0-9a-f]{8}$")
BASE_URL = "http://127.0.0.1:8095/tts/"


def _run(coro):
    return asyncio.run(coro)


def _result(name: str) -> TTSResult:
    return TTSResult(filename=name, public_url=BASE_URL + name,
                     provider="nowvoice", voice="v", duration_ms=0, cached=False)


class _BlockingHA:
    """假 HA：把「播放那一刻上下文里的号」记下来，并可用一条事件把 worker 卡住。"""

    def __init__(self):
        self.seen: list[str] = []
        self.played: list[str] = []
        self.gate = asyncio.Event()

    async def resolve_play_method(self, device_id: str) -> str:
        return "url"

    async def play_xiaomi_url_once(self, device_id: str, url: str, method: str = "url") -> dict:
        self.seen.append(current_trace_id.get() or "")
        await self.gate.wait()
        self.played.append(url)
        return {"code": "0"}


class PlaybackWorkerTraceTest(unittest.TestCase):
    def setUp(self):
        self.ha = _BlockingHA()
        self.pq = PlaybackQueue(ha_client=self.ha, play_gap_s=0.0)

    async def _wait_seen(self, n: int) -> None:
        for _ in range(200):
            if len(self.ha.seen) >= n:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("worker 没走到播放腿")

    def test_played_leg_carries_the_caller_trace(self):
        async def body():
            await self.pq.enqueue("dev-9", _result("a.mp3"), trace_id="cafe1234")
            await self._wait_seen(1)
            self.ha.gate.set()
            await asyncio.wait_for(self.pq._queues["dev-9"].join(), timeout=2.0)
            await self.pq.shutdown()

        _run(body())
        self.assertEqual(self.ha.seen, ["cafe1234"], "音箱那一行取不到调用方的号")
        self.assertEqual(self.ha.played, [BASE_URL + "a.mp3"])

    def test_item_without_a_trace_does_not_inherit_the_previous_one(self):
        async def body():
            self.ha.gate.set()
            await self.pq.enqueue("dev-9", _result("a.mp3"), trace_id="cafe1234")
            await self.pq.enqueue("dev-9", _result("b.mp3"))
            await asyncio.wait_for(self.pq._queues["dev-9"].join(), timeout=2.0)
            await self.pq.shutdown()

        _run(body())
        self.assertEqual(self.ha.seen, ["cafe1234", ""], "复位没做：第二条继承了第一条的号")

    def test_queue_full_drop_hands_the_survivor_trace(self):
        """队列满丢最旧那条——丢的分支也要按新形制解包，且交给 worker 的号是活下来的那条。"""
        old_max = playback_queue.MAX_QUEUE_PER_DEVICE
        playback_queue.MAX_QUEUE_PER_DEVICE = 1
        try:
            async def body():
                await self.pq.enqueue("dev-9", _result("a.mp3"), trace_id="first")
                await self._wait_seen(1)
                self.assertTrue(await self.pq.enqueue("dev-9", _result("b.mp3"), trace_id="second"))
                self.assertTrue(await self.pq.enqueue("dev-9", _result("c.mp3"), trace_id="third"))
                self.ha.gate.set()
                await asyncio.wait_for(self.pq._queues["dev-9"].join(), timeout=2.0)
                await self.pq.shutdown()

            _run(body())
        finally:
            playback_queue.MAX_QUEUE_PER_DEVICE = old_max
        self.assertEqual(self.ha.seen, ["first", "third"])
        self.assertNotIn(BASE_URL + "b.mp3", self.ha.played)


class _RecordingTTSQueue:
    def __init__(self):
        self.traces: list[object] = []
        self.ctx_during: list[str] = []

    def enqueue(self, text: str, **kw):
        self.traces.append(kw.get("trace_id"))
        self.ctx_during.append(current_trace_id.get() or "")
        return SimpleNamespace(accepted=True, reason="ok")


class NotifyTraceBirthTest(unittest.TestCase):
    def setUp(self):
        self.q = _RecordingTTSQueue()
        self.router = NotifyRouter(tts_queue=self.q)

    def test_offchain_notice_births_a_trace_and_passes_it_to_the_tts_leg(self):
        after = _run(self._route_and_read())
        self.assertTrue(isinstance(self.q.traces[0], str) and HEX8.match(self.q.traces[0]),
                        "定时/主动推送在日志里是匿名开口")
        self.assertEqual(self.q.ctx_during[0], self.q.traces[0], "分发时上下文里不是这条号")
        self.assertEqual(after, "", "route() 退出后没把上下文复位")

    async def _route_and_read(self) -> str:
        await self.router.route("tts", "有人在门口", room="living", priority=3)
        return current_trace_id.get() or ""

    def test_caller_supplied_trace_is_not_overwritten(self):
        _run(self.router.route("tts", "复诊提醒", trace_id="deadbeef", priority=2))
        self.assertEqual(self.q.traces[-1], "deadbeef")

    def test_two_offchain_notices_do_not_share_one_id(self):
        async def body():
            await self.router.route("tts", "第一条", priority=3)
            await self.router.route("tts", "第二条", priority=3)

        _run(body())
        first, second = self.q.traces[-2], self.q.traces[-1]
        self.assertTrue(HEX8.match(first or "") and HEX8.match(second or ""))
        self.assertNotEqual(first, second, "两条通知共用一个号 ⇒ 反查会串")


if __name__ == "__main__":
    unittest.main()
