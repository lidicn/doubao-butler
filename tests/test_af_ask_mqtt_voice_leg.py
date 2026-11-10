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

    @staticmethod
    def _est_speak_secs(text):
        return 0.0


class _HA:
    def __init__(self):
        self.calls = []

    async def call_service(self, domain, service, data=None):
        self.calls.append({"domain": domain, "service": service, "data": data or {}})
        return "ok"

    async def get_xiaoai_id(self, entity_id):
        self.calls.append({"get_xiaoai_id": entity_id})
        return "aid_x"

    async def stop_xiaomi_playback(self, device_id):
        self.calls.append({"stop_xiaomi_playback": device_id})
        return {"code": 0}


class _Dev:
    def __init__(self, dev_id="xiao_touch8", wake_button="button.xiao_touch8_wake_up"):
        self.id = dev_id
        self.wake_button = wake_button
        self.ha_player_entity = "media_player.xiao_touch8_play_control"


class _Devices:
    def __init__(self, devs=None):
        self._d = {d.id: d for d in (devs or [])}

    def get(self, dev_id):
        return self._d.get(dev_id)


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
        self.ha = _HA()


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
        # 唤醒延迟置 0：本档不真等 TTS 念完；睡眠窗的行为由 WakeArming 单独验
        self.mgr._speak_secs = lambda prompt, dialog=None: -1.5
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

    async def test_media_stopped_after_answer(self):
        """答完后必须 stop+清队列：只 stop 不队列清理，小爱会把这条播报当音乐复播（循环声）。"""
        self.rt.devices = _Devices([_Dev()])
        await self.speak()
        await self.mgr.on_transcript("xiao_touch8", "书房", "要")
        ok = await _until(lambda: any("stop_xiaomi_playback" in c for c in self.rt.ha.calls))
        self.assertTrue(ok, "答完没清设备播放队列＝复播路径还在")
        self.assertTrue(self.rt.mqtt.published[0]["payload"]["answer"] == "yes")

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


class WakeArming(_Base):
    """联调 §3.4：念完问题后必须按 wake_button 把音箱切进聆听态（腿 1 红点）。"""

    async def test_presses_wake_button_after_speaking(self):
        self.rt.devices = _Devices([_Dev()])
        await self.speak()
        ok = await _until(lambda: len(self.rt.ha.calls) >= 1)
        self.assertTrue(ok, "念完没按唤醒＝音箱不进聆听态，用户无法零唤醒词作答")
        call = self.rt.ha.calls[0]
        self.assertEqual(call["domain"], "button")
        self.assertEqual(call["service"], "press")
        self.assertEqual(call["data"], {"entity_id": "button.xiao_touch8_wake_up"})

    async def test_missing_wake_button_is_named_not_silent(self):
        self.rt.devices = _Devices([_Dev(wake_button="")])
        with self.assertLogs("butler.bus.af_ask", level="WARNING") as cm:
            await self.speak()
            await _until(lambda: False, 0.1)
        joined = " ".join(cm.output)
        self.assertIn("AF_ASK_WAKE_UNSUPPORTED", joined)
        self.assertEqual(self.rt.ha.calls, [], "没有唤醒按钮还去按压＝假肢")
        # ⛔ 静默降级：必须点名「回退方案需报 AF-PM 裁定」
        self.assertIn("AF-PM", joined)


class UnclassifiedKeepsWindow(_Base):
    """小爱自己的「我在」/听不清的噪声不能把挂起问句关掉。"""

    async def test_ignored_chatter_does_not_close_ask(self):
        self.rt.devices = _Devices([_Dev()])
        await self.speak()
        handled = await self.mgr.on_transcript("xiao_touch8", "书房", "我在")
        self.assertTrue(handled)
        self.assertEqual(self.rt.mqtt.published, [])
        self.assertTrue(await self.mgr.on_transcript("xiao_touch8", "书房", "要"),
                        "噪声之后用户再答「要」应该还能接到")
        self.assertEqual(self.rt.mqtt.published[0]["payload"]["answer"], "yes")


class DeviceWakeButtonMigration(unittest.TestCase):
    """联调腿 1 根因：生产 devices.json 的 wake_button 是空串 ⇒ 念完按不到唤醒。"""

    def test_empty_wake_button_filled_from_seed_and_persisted(self):
        import json
        import tempfile
        from butler.config import Settings
        from butler.devices import DeviceRegistry

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data = pathlib.Path(tmp.name)
        (data / "devices.json").write_text(json.dumps({
            "xiao_study": {
                "id": "xiao_study", "type": "xiaomi", "room": "书房", "enabled": True,
                "ha_player_entity": "media_player.xiaomi_x08a_1648_play_control",
                "ha_sensor": "sensor.xiaomi_x08a_1648_conversation",
                "wake_button": "",
            }
        }, ensure_ascii=False), encoding="utf-8")
        reg = DeviceRegistry(Settings(data_dir=str(data)))
        reg.load()
        self.assertEqual(reg.get("xiao_study").wake_button, "button.xiaomi_x08a_1648_wake_up",
                         "老档空 wake_button 没补上＝腿 1 继续红")
        saved = json.loads((data / "devices.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["xiao_study"]["wake_button"], "button.xiaomi_x08a_1648_wake_up",
                         "补全没落盘＝下次重启又回到空串")


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
