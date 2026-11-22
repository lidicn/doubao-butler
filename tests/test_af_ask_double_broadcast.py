"""AF 主动询问「问句双播」修复 · 判据件（C1–C5）。

缺陷（用户现场 2026-11-22 02:18，ask_id 047265e1408d）：
- A 环 `butler/bus/af_ask.py`（MQTT 语音环）：`_speak` → `speak_as_role` → `_arm_wake` →
  `xiaomi_ear.on_transcript` → `af/ask/answer`，完整闭环。
- B 环 `butler/af_bridge.py`（轮询环）：`_poll_once` 对每个新 ask 再 `_announce` 一遍，
  `on_user_reply` 也管回答投递。
两环对同一 ask 各念一遍 ⇒ 设备从 0 位重放 ⇒ 前缀重复（「要打开主卧室净化器吗？」→「要打开要打开…」）。

定案（选项 A）：
1. A 环在场（`rt.af_ask` 非 None）⇒ B 环不播报（`_poll_once` 跳过 `_announce`）、不抢答
   （`on_user_reply` 直接 return False）；
2. A 环缺席 ⇒ B 环照旧播报＋参与应答，并落 `AF_BRIDGE_ANNOUNCE_FALLBACK` 点名日志（⛔ 静默降级）；
3. `app.py` 装配次序前移：`rt.af_ask = AfAskManager(rt)` 在 `af_bridge.start()` 之前。
4. `_emit_devices` 多设备分发 / Q6 门档零改动（C4 由既有 Q6 测试守）。

跑法：`python -B -m unittest tests.test_af_ask_double_broadcast`
"""
from __future__ import annotations

import asyncio
import time
import unittest

import butler.af_bridge as af
from butler.bus.af_ask import AfAskManager, _Ask

BASE = "http://af-fake.invalid:9"


class _Resp:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload or {}

    async def text(self):
        return ""

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeAiohttp:
    """任何请求都要先在 routes 里配好；配不到就大声报错（⛔ 静默放行）。"""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    class _Session:
        def __init__(self, outer):
            self.outer = outer

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def _do(self, verb, url, **kw):
            self.outer.requests.append((verb, url, kw))
            key = (verb, url)
            if key not in self.outer.routes:
                raise AssertionError("夹具没配这条路由：%s %s（⛔ 让代码走真网络）" % (verb, url))
            return self.outer.routes[key]

        def get(self, url, headers=None, timeout=None):
            return self._do("GET", url, headers=headers, timeout=timeout)

        def post(self, url, json=None, headers=None, timeout=None):
            return self._do("POST", url, json=json, headers=headers, timeout=timeout)

    def ClientSession(self):
        return _FakeAiohttp._Session(self)

    def ClientTimeout(self, **kw):
        return ("timeout", kw)

    class ClientError(Exception):
        pass


class _Role:
    id = "butler"
    name = "豆包管家"


class _Roles:
    def get(self, rid):
        return _Role() if rid == "butler" else None


class _Dialog:
    def __init__(self):
        self.spoken = []

    async def speak_as_role(self, role, text, room, member=""):
        self.spoken.append((text, room))
        return {"spoken": True, "devices": [{"device": "xiao_touch", "ok": True}]}


class _Mqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=1, retain=False):
        self.published.append((topic, payload))


class _RT:
    def __init__(self, af_ask_present, dialog, mqtt):
        self.af_ask = object() if af_ask_present else None
        self.dialog = dialog
        self.roles = _Roles()
        self.mqtt = mqtt


def _ask(manager, ask_id="a1", room="主卧室", prompt="要打开主卧室净化器吗？"):
    return _Ask(ask_id=ask_id, room=room, room_key=manager._room_key(room),
                prompt=prompt, deadline=time.monotonic() + 60)


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = {k: getattr(af, k) for k in
                       ("aiohttp", "_rt", "_base_url", "_api_token", "_inbox_key",
                        "_answer_window", "_pending")}
        af._pending.clear()
        af._base_url = BASE
        af._api_token = "SENTINEL"
        af._inbox_key = ""
        af._answer_window = 900.0
        self.dialog = _Dialog()
        self.mqtt = _Mqtt()
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            setattr(af, k, v)
        af._pending.clear()

    def _wire(self, routes):
        fake = _FakeAiohttp(routes)
        af.aiohttp = fake
        return fake

    async def _poll(self, asks):
        self._wire({("GET", BASE + "/api/asks/pending"): _Resp(200, {"asks": asks})})
        await af._poll_once()

    def _count_kind(self, fake, verb):
        return sum(1 for v, _u, _k in fake.requests if v == verb)


class C1_A环在场只出一声(_Base):
    async def test_c1_b_ring_silent_a_ring_speaks_once(self):
        rt = _RT(True, self.dialog, self.mqtt)
        manager = AfAskManager(rt)
        manager._speak = manager._speak  # 保持真实 _speak，不桩
        rt.af_ask = manager
        af._rt = rt

        payload = {"ask_id": "a1", "room": "主卧室", "prompt": "要打开主卧室净化器吗？"}
        with self.assertLogs("butler.af_bridge", level="INFO") as cap:
            await self._poll([payload])
        self.assertEqual(self.dialog.spoken, [], "B 环不得播报（A 环在场）")
        self.assertFalse(any("af_bridge announce" in ln for ln in cap.output), cap.output)
        self.assertTrue(any("AF_BRIDGE_ANNOUNCE_OWNED_BY_A_RING" in ln for ln in cap.output), cap.output)

        with self.assertLogs("butler.bus.af_ask", level="INFO") as cap2:
            await manager._speak(_ask(manager))
        self.assertEqual(len(self.dialog.spoken), 1, "A 环应当且只应当念一次")
        self.assertTrue(any("AF_ASK_SPOKEN" in ln for ln in cap2.output), cap2.output)


class C2_同一回答只投一次(_Base):
    async def test_c2_b_ring_returns_false_a_ring_publishes_once(self):
        rt = _RT(True, self.dialog, self.mqtt)
        manager = AfAskManager(rt)
        rt.af_ask = manager
        af._rt = rt

        # B 环：A 环在场 ⇒ 不得抢答、不得 POST
        fake = self._wire({})
        ok = await af.on_user_reply("主卧室", "要")
        self.assertFalse(ok, "A 环在场时 B 环必须交回 dialog")
        self.assertEqual(self._count_kind(fake, "POST"), 0, "B 环不得投递 answer")

        # A 环：同一回答只发一次
        ask = _ask(manager)
        manager._active[ask.ask_id] = ask
        with self.assertLogs("butler.bus.af_ask", level="INFO") as cap:
            handled = await manager.on_transcript("xiao_touch", "主卧室", "要")
        self.assertTrue(handled)
        self.assertEqual(len(self.mqtt.published), 1, "A 环 answer 只发一次")
        self.assertEqual(self.mqtt.published[0][1].get("answer"), "yes")
        self.assertTrue(any("AF_ASK_ANSWER_SENT" in ln for ln in cap.output), cap.output)


class C3_负控A环缺席显式兜底(_Base):
    async def test_c3_fallback_announces_and_answers_with_named_log(self):
        rt = _RT(False, self.dialog, self.mqtt)
        af._rt = rt

        payload = {"ask_id": "a1", "room": "主卧室", "prompt": "要打开主卧室净化器吗？"}
        with self.assertLogs("butler.af_bridge", level="INFO") as cap:
            await self._poll([payload])
        self.assertEqual(len(self.dialog.spoken), 1, "A 环缺席时 B 环必须兜底播报")
        self.assertTrue(any("AF_BRIDGE_ANNOUNCE_FALLBACK" in ln for ln in cap.output), cap.output)

        fake = self._wire({("POST", BASE + "/api/asks/answer"): _Resp(200, {"ok": True, "inbox": "/tmp/x"})})
        ok = await af.on_user_reply("主卧室", "要")
        self.assertTrue(ok, "A 环缺席时 B 环必须参与应答")
        self.assertEqual(self._count_kind(fake, "POST"), 1)

    def test_c3b_source_has_fallback_branch(self):
        """变异门：删掉兜底分支（或它那句点名日志）⇒ 本腿红。"""
        from pathlib import Path
        src = Path(af.__file__).read_text(encoding="utf-8")
        self.assertIn("AF_BRIDGE_ANNOUNCE_FALLBACK", src)
        self.assertIn("AF_BRIDGE_ANNOUNCE_OWNED_BY_A_RING", src)


class C5_同ask重发不重复播报(_Base):
    async def test_c5a_a_ring_present_resend_silent(self):
        rt = _RT(True, self.dialog, self.mqtt)
        rt.af_ask = AfAskManager(rt)
        af._rt = rt
        payload = {"ask_id": "a1", "room": "主卧室", "prompt": "要打开主卧室净化器吗？"}
        await self._poll([payload])
        await self._poll([payload])   # AF 重发同一 ask
        self.assertEqual(self.dialog.spoken, [], "A 环在场时 B 环永不播报，重发亦然")

    async def test_c5b_fallback_resend_announces_once(self):
        rt = _RT(False, self.dialog, self.mqtt)
        af._rt = rt
        payload = {"ask_id": "a1", "room": "主卧室", "prompt": "要打开主卧室净化器吗？"}
        await self._poll([payload])
        await self._poll([payload])   # AF 重发同一 ask
        self.assertEqual(len(self.dialog.spoken), 1, "同一 ask 重发不得二次播报")


class C0_装配次序(_Base):
    def test_c0_af_ask_before_af_bridge_start(self):
        """app.py：eager 装配 `rt.af_ask = AfAskManager(rt)` 必须在 `await af_bridge.start()` 之前。

        注意 `_consume` 里还有一处懒装配（另一行），所以尺子取「修复标记 → af_bridge.start()」
        这段区间，⛔ 用全文件首个 index（会先命中懒装配，次序回退也假绿）。
        """
        from pathlib import Path
        root = Path(af.__file__).resolve().parent.parent
        src = (root / "butler" / "app.py").read_text(encoding="utf-8")
        i_mark = src.index("# 2026-11-22 AF 主动询问问句双播修复")
        i_start = src.index("await af_bridge.start()")
        seg = src[i_mark:i_start]
        self.assertIn("rt.af_ask = AfAskManager(rt)", seg,
                      "eager 装配不在 af_bridge.start() 之前（首 poll 抢播窗口回来了）")


if __name__ == "__main__":
    unittest.main()
