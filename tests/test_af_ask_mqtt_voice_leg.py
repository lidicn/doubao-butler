"""AF 主动询问语音环（MQTT）验收：af/ask/pending → 念题 → 收回答 → af/ask/answer。

线协议（AF `af_mqtt_bridge.py` v1.28.3，2026-11-08 交接）：
- pending `{ask_id, room, prompt, source, priority, timeout_remaining}`；
- answer `{ask_id, answer: yes|no, raw_text}`，只两值、retain=false；
- 带参数回答整条拒收；识别不出/静默不发；晚到丢弃；同 ask_id 只念一遍；同房间串行。
"""
from __future__ import annotations

import asyncio
import pathlib
import time
import unittest

from butler.bus import af_ask
from butler.bus.af_ask import AfAskManager, classify_answer

REPO = pathlib.Path(__file__).resolve().parents[1]


class _Role:
    id = "butler"
    name = "管家"


class _Roles:
    def get(self, rid):
        return _Role() if rid == "butler" else None


class _Dialog:
    def __init__(self, spoken: bool = True):
        self.calls = []
        self.spoken = spoken

    async def speak_as_role(self, role, text, room, member=""):
        self.calls.append({"text": text, "room": room, "member": member})
        return {"spoken": self.spoken, "devices": [{"device": "xiao_touch8", "ok": True}]}


class _MQTT:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=1, retain=False):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})


class _RT:
    def __init__(self, spoken=True):
        self.dialog = _Dialog(spoken)
        self.roles = _Roles()
        self.mqtt = _MQTT()
        self.devices = None


def _payload(**over):
    d = {"ask_id": "ask_001", "room": "shu_fang", "prompt": "书房温度 28 度，要开空调制冷到 26 度吗？",
         "source": "af", "priority": 50, "timeout_remaining": 5.0}
    d.update(over)
    return d


async def _until(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


class ClassifyAnswer(unittest.TestCase):
    def test_yes_variants(self):
        for t in ("要", "好", "是", "开", "行", "可以", "需要", "嗯", "对", "ok", "yes", "要开"):
            self.assertEqual(classify_answer(t)[0], "yes", t)

    def test_no_variants(self):
        for t in ("不要", "不用", "不", "否", "别", "关", "不需要", "不行", "不是", "no", "不要开"):
            self.assertEqual(classify_answer(t)[0], "no", t)

    def test_param_answers_rejected(self):
        for t in ("开到24度", "26度", "调到 26 度", "百分之五十", "开空调到26"):
            self.assertEqual(classify_answer(t)[0], None, t)
            self.assertEqual(classify_answer(t)[1], "param", t)

    def test_empty_and_garbage(self):
        self.assertEqual(classify_answer("")[0], None)
        self.assertEqual(classify_answer("   ")[0], None)
        self.assertEqual(classify_answer("今天天气怎么样")[0], None)
        self.assertEqual(classify_answer("嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯嗯")[1], "too_long")


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rt = _RT()
        self.mgr = AfAskManager(self.rt)
        self.addCleanup(self.mgr.close)

    async def speak(self, **over):
        await self.mgr.handle_pending(_payload(**over))
        ok = await _until(lambda: len(self.rt.dialog.calls) >= 1)
        self.assertTrue(ok, "没有把问题念出来")


class PendingLeg(_Base):
    async def test_speaks_prompt_to_room(self):
        await self.speak()
        call = self.rt.dialog.calls[0]
        self.assertEqual(call["room"], "shu_fang")
        self.assertIn("要开空调", call["text"])
        self.assertEqual(call["member"], "系统")

    async def test_dedupes_same_ask_id(self):
        await self.speak()
        await self.mgr.handle_pending(_payload())
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.rt.dialog.calls), 1, "同一 ask_id 被念了两遍")

    async def test_expired_payload_not_spoken(self):
        await self.mgr.handle_pending(_payload(timeout_remaining=0))
        await asyncio.sleep(0.05)
        self.assertEqual(self.rt.dialog.calls, [], "timeout_remaining<=0 还念题")

    async def test_bad_payloads_dropped_without_crash(self):
        for bad in (None, {}, {"ask_id": ""}, _payload(prompt=""), _payload(room=""),
                    _payload(timeout_remaining="abc")):
            await self.mgr.handle_pending(bad)
        await asyncio.sleep(0.05)
        self.assertEqual(self.rt.dialog.calls, [])

    async def test_unsent_when_no_device(self):
        self.rt.dialog.spoken = False
        await self.mgr.handle_pending(_payload())
        await _until(lambda: False, 0.1)
        self.assertEqual(self.rt.mqtt.published, [])


class AnswerLeg(_Base):
    async def test_yes_answer_published_exactly(self):
        await self.speak()
        handled = await self.mgr.on_transcript("xiao_touch8", "书房", "要")
        self.assertTrue(handled)
        self.assertEqual(len(self.rt.mqtt.published), 1)
        rec = self.rt.mqtt.published[0]
        self.assertEqual(rec["topic"], "af/ask/answer")
        self.assertEqual(rec["payload"], {"ask_id": "ask_001", "answer": "yes", "raw_text": "要"})
        self.assertEqual(rec["qos"], 1)
        self.assertFalse(rec["retain"])

    async def test_no_answer_published(self):
        await self.speak()
        await self.mgr.on_transcript("xiao_touch8", "书房", "不要")
        self.assertEqual(self.rt.mqtt.published[0]["payload"]["answer"], "no")

    async def test_second_answer_ignored(self):
        await self.speak()
        await self.mgr.on_transcript("xiao_touch8", "书房", "要")
        handled = await self.mgr.on_transcript("xiao_touch8", "书房", "不要")
        self.assertFalse(handled)
        self.assertEqual(len(self.rt.mqtt.published), 1)

    async def test_param_answer_rejected_and_cleared(self):
        await self.speak()
        handled = await self.mgr.on_transcript("xiao_touch8", "书房", "开到24度")
        self.assertTrue(handled, "带参数回答应被认作对本 ask 的回应（拒收而非当普通对话）")
        self.assertEqual(self.rt.mqtt.published, [])
        self.assertFalse(await self.mgr.on_transcript("xiao_touch8", "书房", "要"))

    async def test_other_room_answer_not_captured(self):
        await self.speak()
        self.assertFalse(await self.mgr.on_transcript("xiao_touch8", "客厅", "要"))
        self.assertEqual(self.rt.mqtt.published, [])

    async def test_late_answer_dropped(self):
        await self.mgr.handle_pending(_payload(timeout_remaining=0.05))
        await _until(lambda: len(self.rt.dialog.calls) >= 1)
        await asyncio.sleep(0.1)  # 等过期
        self.assertFalse(await self.mgr.on_transcript("xiao_touch8", "书房", "要"))
        self.assertEqual(self.rt.mqtt.published, [])

    async def test_unanswered_sends_nothing(self):
        await self.mgr.handle_pending(_payload(timeout_remaining=0.05))
        await _until(lambda: len(self.rt.dialog.calls) >= 1)
        await asyncio.sleep(0.12)
        self.assertEqual(self.rt.mqtt.published, [])


class RoomSerial(_Base):
    async def test_second_ask_waits_for_first(self):
        await self.speak(ask_id="ask_001")
        await self.mgr.handle_pending(_payload(ask_id="ask_002"))
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.rt.dialog.calls), 1, "同房间第二条问句被并发念了")
        await self.mgr.on_transcript("xiao_touch8", "书房", "要")
        ok = await _until(lambda: len(self.rt.dialog.calls) >= 2)
        self.assertTrue(ok, "第一条答完，第二条没接着念")


class WiringShape(unittest.TestCase):
    def test_topic_constants_and_subscription(self):
        from butler.bus import topics
        self.assertEqual(topics.SUB_AF_ASK_PENDING, "af/ask/pending")
        self.assertEqual(topics.PUB_AF_ASK_ANSWER, "af/ask/answer")
        self.assertIn(topics.SUB_AF_ASK_PENDING, topics.SUB_TOPICS)

    def test_consume_dispatch_wired(self):
        src = (REPO / "butler" / "app.py").read_text(encoding="utf-8")
        self.assertIn("SUB_AF_ASK_PENDING", src)
        self.assertIn("AfAskManager", src)
        self.assertIn("handle_pending", src)

    def test_ear_hook_wired(self):
        src = (REPO / "butler" / "core" / "xiaomi_ear.py").read_text(encoding="utf-8")
        self.assertIn("af_ask", src)
        self.assertIn("on_transcript", src)


if __name__ == "__main__":
    unittest.main()
