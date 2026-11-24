"""AF 主动询问「唤醒提示音时机优化」（方案 A·完成信号驱动）· 判据件（E0–E7）。

现场（2026-11-24 03:01 主卧真触发，NAS 日志）：问句实际 2.23 秒就播完
（`stop@end pos=2129 dev_dur=2232`），但 `_arm_wake` ①段盲等 `字数×0.32+1.5≈5.0s`
⇒ 问句结束到「叮」之间约 **2.0 秒死寂**。

改造：①段改轮询念题设备的 `media_player` 状态（`state!=playing` 或 `pos>=dur-0.5` 即播完），
三道保险——floor 1.0s／cap＝旧等待上限／探测不可用回退固定等待（逐字等价）。
⛔ 四段顺序不变：等问句播完 → 播叮 → 等叮播完 → 按压。

跑法：`python -B -m unittest tests.test_af_ask_wake_ding_timing`
"""
from __future__ import annotations

import pathlib
import tempfile
import types
import unittest

from butler.bus import af_ask
from butler.bus.af_ask import (
    PROMPT_IDLE_FLOOR_S,
    PROMPT_POLL_S,
    WAKE_DING_DURATION_S,
    AfAskManager,
    _Ask,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
CAP_SPEAK_EST = 5.0          # `_est_speak_secs` 桩值：cap = 5.0 + 1.5 = 6.5s（与改造前等值）


class _Clock:
    def __init__(self, t: float = 1000.0):
        self.t0 = float(t)
        self.t = float(t)
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def rel(self) -> float:
        return self.t - self.t0

    async def sleep(self, secs) -> None:
        self.sleeps.append(float(secs))
        self.t += max(0.0, float(secs))


class _Role:
    id = "butler"


class _Roles:
    def get(self, rid):
        return _Role() if rid == "butler" else None


class _Dialog:
    async def speak_as_role(self, role, text, room, member=""):
        return {"spoken": True, "devices": [{"device": "xiao_zw", "ok": True}]}

    @staticmethod
    def _est_speak_secs(text):
        return CAP_SPEAK_EST


class _Dev:
    def __init__(self, dev_id="xiao_zw", wake_button="button.xiao_zw_wake_up",
                 entity="media_player.xiao_zw_play_control"):
        self.id = dev_id
        self.wake_button = wake_button
        self.ha_player_entity = entity


class _Devices:
    def __init__(self, devs):
        self._d = {d.id: d for d in devs}

    def get(self, dev_id):
        return self._d.get(dev_id)


class _HA:
    """`states`：entity → callable(clock) → HA 状态字典；缺省视作读不到（None）。"""

    def __init__(self, clock, states=None):
        self.clock = clock
        self.states = states or {}
        self.calls = []

    async def get_state(self, entity_id):
        self.calls.append({"kind": "get_state", "entity": entity_id, "t": self.clock.now()})
        fn = self.states.get(entity_id)
        return fn(self.clock) if fn else None

    async def tts_play_url(self, url, entity_id):
        self.calls.append({"kind": "tts_play_url", "url": url, "entity": entity_id, "t": self.clock.now()})
        return "ok"

    async def schedule_media_stop(self, entity_id, audio_secs):
        self.calls.append({"kind": "schedule_media_stop", "entity": entity_id})

    async def call_service(self, domain, service, data=None):
        self.calls.append({"kind": "call_service", "domain": domain, "service": service,
                           "data": data or {}, "t": self.clock.now()})
        return "ok"


def _playing_until(clock, end_rel: float, dur: float = 30.0, *, advance_pos: bool = True):
    """相对 `clock.t0` 的 end_rel 之前＝playing，之后＝idle。

    `dur` 默认给大（30s）⇒ 位置判据 `pos>=dur-0.5` 不会先触发，用来单独验「状态变化」这条。
    """

    def fn(c):
        rel = c.now() - clock.t0
        pos = min(max(rel, 0.0), dur) if advance_pos else 0.0
        if rel < end_rel:
            return {"state": "playing", "attributes": {"media_position": pos, "media_duration": dur}}
        return {"state": "idle", "attributes": {"media_position": pos, "media_duration": dur}}

    return fn


def _settings_ns(tts_dir=""):
    return types.SimpleNamespace(tts_dir=tts_dir, base_url="http://127.0.0.1:8095",
                                 xiaomi_service_token="", xiaomi_user_id="")


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = _Clock()
        self.ha = _HA(self.clock)
        self.rt = types.SimpleNamespace(dialog=_Dialog(), roles=_Roles(), mqtt=None,
                                        devices=_Devices([_Dev()]), ha=self.ha)
        self.mgr = AfAskManager(self.rt, clock=self.clock.now, sleep=self.clock.sleep)
        self.mgr._speak_secs = lambda prompt, dialog=None: CAP_SPEAK_EST
        self._saved_settings = af_ask._settings
        self.addCleanup(self._restore)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        pathlib.Path(self.tmp.name, af_ask.WAKE_DING_NAME).write_bytes(b"fake-mp3")
        af_ask._settings = lambda: _settings_ns(self.tmp.name)

    def _restore(self):
        af_ask._settings = self._saved_settings

    def _ask(self):
        return _Ask(ask_id="ask_t", room="主卧室", room_key="zhu_wo_shi",
                    prompt="要打开主卧室净化器吗？", deadline=self.clock.now() + 60)

    def _spoken(self, *ids):
        return {"spoken": True, "devices": [{"device": i, "ok": True} for i in ids]}

    def _t(self, kind):
        rows = [c for c in self.ha.calls if c["kind"] == kind]
        return [c["t"] - self.clock.t0 for c in rows]

    def _press_t(self):
        rows = [c for c in self.ha.calls
                if c.get("kind") == "call_service" and c.get("service") == "press"]
        return [c["t"] - self.clock.t0 for c in rows]


class E0_源码形状(_Base):
    def test_e0_probe_uses_injected_clock_only(self):
        """轮询里⛔ 直接 time.monotonic()/asyncio.sleep（否则 D4 那类桩时钟判据形同虚设）。"""
        src = pathlib.Path(af_ask.__file__).read_text(encoding="utf-8")
        body = src[src.index("async def _wait_prompt_finished"):
                   src.index("async def _play_wake_ding")]
        self.assertNotIn("time.monotonic", body)
        self.assertNotIn("asyncio.sleep", body)
        self.assertIn("self._sleep(", body)
        self.assertIn("self._clock(", body)

    def test_e0_keeps_legacy_fallback_literal(self):
        """回退路径必须仍是改造前那个表达式（逐字等价）。"""
        src = pathlib.Path(af_ask.__file__).read_text(encoding="utf-8")
        arm = src[src.index("async def _arm_wake"):src.index("def _wake_targets")]
        self.assertIn("await self._sleep(min(delay, max(0.0, remaining - 1.0)))", arm)


class E1_提前性(_Base):
    async def test_e1_ding_fires_right_after_idle_not_two_seconds_later(self):
        """问句 T=3.0s 转 idle ⇒ 叮在 ≈T+0.2，⛔ T+2.0。"""
        self.ha.states = {"media_player.xiao_zw_play_control": _playing_until(self.clock, 3.0)}
        with self.assertLogs("butler.bus.af_ask", level="INFO") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        ding = self._t("tts_play_url")
        self.assertEqual(len(ding), 1, "叮没播")
        self.assertGreaterEqual(ding[0], 3.0, "比问句播完还早（会打断问句）")
        self.assertLessEqual(ding[0], 3.0 + PROMPT_POLL_S + 0.21, f"叮来晚了：{ding[0]:.2f}s")
        self.assertLess(ding[0], 5.0, "还是按旧盲等来了（≈5.0s）")
        self.assertTrue(any("AF_ASK_WAKE_WAIT_COMPLETE" in ln for ln in cm.output), cm.output)
        self.assertFalse(any("AF_ASK_WAKE_WAIT_FALLBACK" in ln for ln in cm.output), cm.output)

    async def test_e1b_position_rule_matches_stop_leg(self):
        """位置判据与停播腿同口径：pos ≥ dur−0.5 即视为播完（⛔ 等 state 变 idle）。"""
        self.ha.states = {"media_player.xiao_zw_play_control":
                          _playing_until(self.clock, 999.0, dur=3.0)}
        await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        ding = self._t("tts_play_url")[0]
        self.assertGreaterEqual(ding, 3.0 - 0.5, f"早于停播腿口径：{ding:.2f}s")
        self.assertLessEqual(ding, 3.0 - 0.5 + PROMPT_POLL_S + 0.21, f"没按位置判据提前：{ding:.2f}s")


class E2_floor(_Base):
    async def test_e2_idle_before_floor_must_not_fire_early(self):
        """声音还没起来就读到 idle（陈旧空态）⇒ 不得在 floor 之前放音。"""
        self.ha.states = {"media_player.xiao_zw_play_control": _playing_until(self.clock, 0.0)}
        with self.assertLogs("butler.bus.af_ask", level="INFO") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        ding = self._t("tts_play_url")[0]
        self.assertGreaterEqual(ding, PROMPT_IDLE_FLOOR_S, f"floor 失效：{ding:.2f}s 就放音")
        self.assertLessEqual(ding, PROMPT_IDLE_FLOOR_S + PROMPT_POLL_S + 0.01, f"floor 后拖太久：{ding:.2f}s")
        self.assertTrue(any("AF_ASK_WAKE_WAIT_COMPLETE" in ln for ln in cm.output), cm.output)


class E3_cap(_Base):
    async def test_e3_probe_unavailable_waits_exactly_the_old_cap(self):
        """探测一直读不到 ⇒ 等待 ≤ 旧 cap（5.0+1.5），且与改造前等值（⛔ 更晚）。"""
        # states 留空 ⇒ get_state 永远 None
        with self.assertLogs("butler.bus.af_ask", level="INFO") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        ding = self._t("tts_play_url")[0]
        cap = CAP_SPEAK_EST + 1.5
        self.assertLessEqual(ding, cap + 0.01, f"晚放了：{ding:.2f}s > cap {cap}s")
        self.assertGreaterEqual(ding, cap - 0.01, f"等待与改造前不等值：{ding:.2f}s vs {cap}s")
        self.assertTrue(any("AF_ASK_WAKE_WAIT_FALLBACK" in ln and "上限" in ln for ln in cm.output), cm.output)


class E4_回退等价(_Base):
    async def test_e4a_no_player_entity_falls_back_to_fixed_wait(self):
        """无 ha_player_entity ⇒ 固定等待、零探测、与改造前逐字等价（6.5s），且点名原因。"""
        self.rt.devices = _Devices([_Dev(entity="")])
        with self.assertLogs("butler.bus.af_ask", level="INFO") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        self.assertEqual([c for c in self.ha.calls if c["kind"] == "get_state"], [],
                         "没有播放实体还去探测＝白花 HA 请求")
        self.assertAlmostEqual(sum(self.clock.sleeps[:1]), CAP_SPEAK_EST + 1.5, places=2,
                               msg=f"回退等待不等价：{self.clock.sleeps[:3]}")
        self.assertTrue(any("AF_ASK_WAKE_WAIT_FALLBACK" in ln and "播放实体" in ln for ln in cm.output), cm.output)

    async def test_e4b_get_state_missing_falls_back_to_fixed_wait(self):
        """HA 侧没有 get_state ⇒ 同样回退固定等待（⛔ 抛异常、⛔ 卡死）。"""
        self.rt.ha = types.SimpleNamespace(tts_play_url=self.ha.tts_play_url,
                                           schedule_media_stop=self.ha.schedule_media_stop,
                                           call_service=self.ha.call_service)
        self.mgr.rt = self.rt
        with self.assertLogs("butler.bus.af_ask", level="INFO") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        ding = self._t("tts_play_url")[0]
        self.assertAlmostEqual(ding, CAP_SPEAK_EST + 1.5, places=2, msg=f"回退等待不等价：{ding:.2f}s")
        self.assertTrue(any("AF_ASK_WAKE_WAIT_FALLBACK" in ln and "get_state" in ln for ln in cm.output), cm.output)


class E5_顺序不变(_Base):
    async def test_e5_ding_still_before_press(self):
        self.ha.states = {"media_player.xiao_zw_play_control": _playing_until(self.clock, 2.0)}
        await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        ding = self._t("tts_play_url")[0]
        press = self._press_t()[0]
        self.assertLess(ding, press, "叮没在按压之前")
        self.assertGreaterEqual(press, ding + WAKE_DING_DURATION_S,
                                "按压早于叮播完（麦克风会拾到自己的叮）")


class E6_多设备取最后播完(_Base):
    async def test_e6_multi_device_waits_for_the_last_one(self):
        """一台 2.0s 播完、另一台 4.0s 播完 ⇒ 叮必须等 4.0s 那台（⛔ 2.2s 就插进去）。"""
        self.ha.states = {
            "media_player.a_play_control": _playing_until(self.clock, 2.0),
            "media_player.b_play_control": _playing_until(self.clock, 4.0),
        }
        self.rt.devices = _Devices([
            _Dev("dev_a", entity="media_player.a_play_control"),
            _Dev("dev_b", entity="media_player.b_play_control"),
        ])
        with self.assertLogs("butler.bus.af_ask", level="INFO") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("dev_a", "dev_b"))
        ding = min(self._t("tts_play_url"))
        self.assertGreaterEqual(ding, 4.0, f"叮插进了还没播完那台的问句里：{ding:.2f}s")
        self.assertLessEqual(ding, 4.0 + PROMPT_POLL_S + 0.21, f"等过头：{ding:.2f}s")
        self.assertTrue(any("AF_ASK_WAKE_WAIT_COMPLETE" in ln for ln in cm.output), cm.output)


class E7_留痕不混用(_Base):
    async def test_e7_wait_markers_distinct_from_ding_markers(self):
        """等待留痕⛔ 与 `WAKE_DING_PLAYED`/`WAKE_UNSUPPORTED` 混用。"""
        self.ha.states = {"media_player.xiao_zw_play_control": _playing_until(self.clock, 1.5)}
        with self.assertLogs("butler.bus.af_ask", level="INFO") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        joined = "\n".join(cm.output)
        self.assertIn("AF_ASK_WAKE_WAIT_COMPLETE", joined)
        self.assertIn("AF_ASK_WAKE_DING_PLAYED", joined)
        for ln in cm.output:
            if "AF_ASK_WAKE_WAIT_" in ln:
                self.assertNotIn("AF_ASK_WAKE_DING_PLAYED", ln)
                self.assertNotIn("AF_ASK_WAKE_UNSUPPORTED", ln)


if __name__ == "__main__":
    unittest.main()
