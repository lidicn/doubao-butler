"""AF 主动询问「唤醒窗口可感知化」· 判据件（D0–D6）。

现场（2026-11-23）：主卧 Sound Pro（l17a）的按压唤醒是**纯静音开窗**（无提示音、无灯），
聆听窗只有 2–9 秒且无声可感 ⇒ 用户只能盲猜何时作答。修复＝念完题后先播 `wake_ding.mp3`
（880Hz／0.6s，`startup_assets.FFMPEG_ARGV` 钉死），播完再按 `wake_button` 开窗。

顺序硬约束（ADM-链主 2026-11-23 补三条）：
- `dialog.speak_as_role` 下发即返回、不等播放完成 ⇒ 必须保留「等问句播完」再播 ding（D0）；
- ding 时长 0.2s ⇒ 「开始 + 0.2s + 余量」为播完，时钟可注入（D4 用桩时钟，⛔ 真等）；
- ding 只给「本次真会 press 的设备」播（D5）；播放通道照抄念题那条小爱腿（D6）。

跑法：`python -B -m unittest tests.test_af_ask_wake_ding`
"""
from __future__ import annotations

import pathlib
import tempfile
import types
import unittest

from butler.bus import af_ask
from butler.bus.af_ask import (
    WAKE_DING_DURATION_S,
    WAKE_DING_MARGIN_S,
    WAKE_DING_STOP_REPORT_S,
    AfAskManager,
    _Ask,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
DING_URL_TAIL = "/tts/wake_ding.mp3"


class _Clock:
    """可注入时钟：sleep 会推进 now，D4 靠它验「press 不早于 ding 播完」。"""

    def __init__(self, t: float = 1000.0):
        self.t = float(t)
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, secs) -> None:
        self.sleeps.append(float(secs))
        self.t += max(0.0, float(secs))


class _Role:
    id = "butler"
    name = "管家"


class _Roles:
    def get(self, rid):
        return _Role() if rid == "butler" else None


class _Dialog:
    """`_est_speak_secs` 固定 5.0 ⇒ 问句等待 = 5.0 + 1.5 = 6.5s（D0 用它看时序）。"""

    def __init__(self):
        self.calls = []

    async def speak_as_role(self, role, text, room, member=""):
        self.calls.append({"text": text, "room": room})
        return {"spoken": True, "devices": [{"device": "xiao_zw", "ok": True}]}

    @staticmethod
    def _est_speak_secs(text):
        return 5.0


class _Dev:
    def __init__(self, dev_id="xiao_zw", wake_button="button.xiao_zw_wake_up",
                 entity="media_player.xiao_zw_play_control", xiaomi_play=None):
        self.id = dev_id
        self.wake_button = wake_button
        self.ha_player_entity = entity
        self.xiaomi_play = xiaomi_play


class _Devices:
    def __init__(self, devs=None):
        self._d = {d.id: d for d in (devs or [])}

    def get(self, dev_id):
        return self._d.get(dev_id)


class _HA:
    """记录每个动作发生的时钟读数；播放成败可控。"""

    def __init__(self, play_ok=True, clock=None):
        self.calls = []
        self.play_ok = play_ok
        self.clock = clock

    def _ts(self):
        return self.clock.now() if self.clock is not None else None

    async def call_service(self, domain, service, data=None):
        self.calls.append({"kind": "call_service", "domain": domain, "service": service,
                           "data": data or {}, "t": self._ts()})
        return "ok"

    async def tts_play_url(self, url, entity_id):
        self.calls.append({"kind": "tts_play_url", "url": url, "entity": entity_id, "t": self._ts()})
        return "ok" if self.play_ok else "error: boom"

    async def schedule_media_stop(self, entity_id, audio_secs):
        self.calls.append({"kind": "schedule_media_stop", "entity": entity_id, "secs": audio_secs})

    async def get_xiaoai_id(self, entity_id):
        self.calls.append({"kind": "get_xiaoai_id", "entity": entity_id, "t": self._ts()})
        return "aid_x"

    async def play_xiaomi_url_once(self, device_id, url, method="url"):
        self.calls.append({"kind": "play_xiaomi_url_once", "aid": device_id, "url": url,
                           "method": method, "t": self._ts()})
        return {"code": 0}

    def schedule_xiaomi_stop(self, device_id, audio_secs):
        self.calls.append({"kind": "schedule_xiaomi_stop", "aid": device_id, "secs": audio_secs})


class _RT:
    def __init__(self, devices=None, ha=None):
        self.dialog = _Dialog()
        self.roles = _Roles()
        self.mqtt = None
        self.devices = _Devices(devices or [_Dev()])
        self.ha = ha or _HA()


def _settings_ns(**over):
    d = {
        "tts_dir": "",
        "base_url": "http://127.0.0.1:8095",
        "xiaomi_service_token": "",
        "xiaomi_user_id": "",
    }
    d.update(over)
    return types.SimpleNamespace(**d)


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = _Clock()
        self.ha = _HA(clock=self.clock)
        self.rt = _RT(ha=self.ha)
        self.mgr = AfAskManager(self.rt, clock=self.clock.now, sleep=self.clock.sleep)
        self._saved_settings = af_ask._settings
        self.addCleanup(self._restore)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tts_dir = self.tmp.name

    def _restore(self):
        af_ask._settings = self._saved_settings

    def _with_ding(self, **over):
        pathlib.Path(self.tts_dir, af_ask.WAKE_DING_NAME).write_bytes(b"fake-mp3")
        af_ask._settings = lambda: _settings_ns(tts_dir=self.tts_dir, **over)

    def _ask(self, timeout=60.0):
        return _Ask(ask_id="ask_ding", room="主卧室", room_key="zhu_wo_shi",
                    prompt="要打开主卧室净化器吗？", deadline=self.clock.now() + timeout)

    def _spoken(self, *ids):
        return {"spoken": True, "devices": [{"device": i, "ok": True} for i in ids]}

    def _kinds(self):
        return [c["kind"] for c in self.ha.calls]

    def _presses(self):
        return [c for c in self.ha.calls
                if c.get("kind") == "call_service" and c.get("service") == "press"]


class D0_问句播完才播提示音(_Base):
    async def test_d0_prompt_wait_precedes_ding(self):
        """① 先等问句播完（6.5s），再播 ding；⛔ 在 speak_as_role 返回处就播（会打断问句）。"""
        self._with_ding()
        await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))

        self.assertTrue(self.clock.sleeps, "一问都没等＝在问句尾巴上按按钮")
        self.assertGreaterEqual(self.clock.sleeps[0], 6.0,
                                "第一等必须是问句等待（≈字数×0.32+1.5），不是 ding 等待")
        ding = [c for c in self.ha.calls if c["kind"] == "tts_play_url"]
        self.assertEqual(len(ding), 1, "ding 没播")
        self.assertGreaterEqual(ding[0]["t"], 1000.0 + 6.0,
                                "ding 在问句播完之前就开始了（会 REPLACE_ALL 打断问句）")

    def test_d0_source_prompt_wait_before_ding(self):
        """源码钉：`_arm_wake` 里问句等待的 sleep 必须出现在播 ding 之前。"""
        src = pathlib.Path(af_ask.__file__).read_text(encoding="utf-8")
        body = src[src.index("async def _arm_wake"):src.index("def _wake_targets")]
        i_wait = body.index("await self._sleep(min(delay")
        i_ding = body.index("await self._play_wake_ding(")
        self.assertLess(i_wait, i_ding, "问句等待被挪到 ding 之后（顺序回退）")


class D1_正控ding先于press(_Base):
    async def test_d1_ding_plays_before_press(self):
        self._with_ding()
        with self.assertLogs("butler.bus.af_ask", level="INFO") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        kinds = self._kinds()
        self.assertIn("tts_play_url", kinds, "ding 没播")
        self.assertEqual(len(self._presses()), 1, "唤醒按钮没按")
        self.assertLess(kinds.index("tts_play_url"), kinds.index("call_service"),
                        "按压发生在提示音之前（用户听不到窗口打开）")
        joined = " ".join(cm.output)
        self.assertIn("AF_ASK_WAKE_DING_PLAYED", joined, "成功播放没留痕")
        self.assertIn("AF_ASK_WAKE_PRESSED", joined)
        self.assertIn("AF_ASK_LISTEN_ARMED", joined)


class D2_源码钉ding在press之前(_Base):
    def test_d2_source_has_ding_before_press(self):
        """变异门：把播 ding 的调用挪到 `button.press` 之后 ⇒ 本腿红。"""
        src = pathlib.Path(af_ask.__file__).read_text(encoding="utf-8")
        body = src[src.index("async def _arm_wake"):src.index("def _wake_targets")]
        i_ding = body.index("await self._play_wake_ding(")
        i_press = body.index('call_service("button", "press"')
        self.assertLess(i_ding, i_press, "播提示音的调用不在 button.press 之前（反序）")


class D3_提示音降级不阻塞开窗(_Base):
    async def test_d3a_missing_asset_named_and_press_still_happens(self):
        af_ask._settings = lambda: _settings_ns(tts_dir=self.tts_dir)   # 目录里没有 wake_ding.mp3
        with self.assertLogs("butler.bus.af_ask", level="WARNING") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        joined = " ".join(cm.output)
        self.assertIn("AF_ASK_WAKE_DING_UNSUPPORTED", joined, "提示音缺失没点名＝静默降级")
        self.assertEqual(len(self._presses()), 1, "提示音缺失把开窗也阻塞了（用户连听都听不到）")
        self.assertNotIn("tts_play_url", self._kinds())

    async def test_d3b_play_failure_named_and_press_still_happens(self):
        self._with_ding()
        self.ha.play_ok = False
        with self.assertLogs("butler.bus.af_ask", level="INFO") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        joined = " ".join(cm.output)
        self.assertIn("AF_ASK_WAKE_DING_UNSUPPORTED", joined, "播放失败没点名")
        self.assertEqual(len(self._presses()), 1, "播放失败把开窗阻塞了")
        self.assertIn("AF_ASK_WAKE_PRESSED", joined, cm.output)


class D4_按压不早于提示音播完(_Base):
    async def test_d4_press_after_ding_finished(self):
        self._with_ding()
        await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        ding = [c for c in self.ha.calls if c["kind"] == "tts_play_url"][0]
        press = self._presses()[0]
        self.assertGreaterEqual(
            press["t"], ding["t"] + WAKE_DING_DURATION_S,
            "按压早于提示音播完（按钮压下时叮还在响，麦克风拾到自己的叮）")
        # 余量也要真等：press 至少落在 ding 开始 + 时长 + 余量 之后
        self.assertGreaterEqual(press["t"], ding["t"] + WAKE_DING_DURATION_S + WAKE_DING_MARGIN_S)


class D5_只给真会按压的设备播ding(_Base):
    async def test_d5_no_wake_button_no_ding(self):
        self.rt.devices = _Devices([
            _Dev("xiao_zw", wake_button="button.xiao_zw_wake_up",
                 entity="media_player.xiao_zw_play_control"),
            _Dev("xiao_old", wake_button="", entity="media_player.xiao_old_play_control"),
        ])
        self._with_ding()
        with self.assertLogs("butler.bus.af_ask", level="WARNING") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw", "xiao_old"))
        played = [c for c in self.ha.calls if c["kind"] == "tts_play_url"]
        self.assertEqual([c["entity"] for c in played], ["media_player.xiao_zw_play_control"],
                         "给「开不了窗」的设备也播了 ding＝听到叮却答不了")
        presses = self._presses()
        self.assertEqual([p["data"]["entity_id"] for p in presses], ["button.xiao_zw_wake_up"])
        self.assertIn("AF_ASK_WAKE_UNSUPPORTED", " ".join(cm.output))

    async def test_d5b_all_targets_without_wake_button_no_ding(self):
        self.rt.devices = _Devices([_Dev("xiao_old", wake_button="")])
        self._with_ding()
        with self.assertLogs("butler.bus.af_ask", level="WARNING") as cm:
            await self.mgr._arm_wake(self._ask(), self._spoken("xiao_old"))
        self.assertNotIn("tts_play_url", self._kinds(), "整条没有可开窗设备还播 ding")
        self.assertIn("AF_ASK_WAKE_UNSUPPORTED", " ".join(cm.output))


class D6_与念题同一条通道(_Base):
    async def test_d6a_ha_leg_uses_player_entity(self):
        """无小爱直连 ⇒ 走 HA media_player（与 dialog 小爱腿同一条）。"""
        self._with_ding()
        await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        ding = [c for c in self.ha.calls if c["kind"] == "tts_play_url"][0]
        self.assertTrue(ding["url"].endswith(DING_URL_TAIL), ding["url"])
        self.assertEqual(ding["entity"], "media_player.xiao_zw_play_control")
        self.assertIn("schedule_media_stop", self._kinds(), "ding 播完没挂停播＝小爱会当网络电台循环")
        self.assertNotIn("play_xiaomi_url_once", self._kinds())

    async def test_d6b_direct_leg_uses_xiaoai_cloud(self):
        """有小爱云端直连 ⇒ 优先 `player_play_url`（dialog 的同一条腿），不另造通道。"""
        self._with_ding(xiaomi_service_token="SENTINEL", xiaomi_user_id="SENTINEL_UID")
        await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        kinds = self._kinds()
        self.assertIn("play_xiaomi_url_once", kinds)
        self.assertNotIn("tts_play_url", kinds, "有直连还走 HA ＝ 与念题不同通道")
        direct = [c for c in self.ha.calls if c["kind"] == "play_xiaomi_url_once"][0]
        self.assertEqual(direct["aid"], "aid_x")
        self.assertTrue(direct["url"].endswith(DING_URL_TAIL))
        self.assertEqual(direct["method"], "url")
        self.assertNotIn("schedule_xiaomi_stop", kinds, "url 模式是一次性投射，不需要停止腿")

    async def test_d6c_music_firmware_arms_stop_with_lead_compensation(self):
        """music 固件（红米触屏/主卧 l17a）播单条会循环 ⇒ 必须挂停止腿，且报的时长要扣掉
        停止腿自带的 800ms 提前量（报 0.2s 会一开播就停，叮被吃掉）。"""
        self.rt.devices = _Devices([_Dev(xiaomi_play="music")])
        self._with_ding(xiaomi_service_token="SENTINEL", xiaomi_user_id="SENTINEL_UID")
        await self.mgr._arm_wake(self._ask(), self._spoken("xiao_zw"))
        direct = [c for c in self.ha.calls if c["kind"] == "play_xiaomi_url_once"][0]
        self.assertEqual(direct["method"], "music")
        stops = [c for c in self.ha.calls if c["kind"] == "schedule_xiaomi_stop"]
        self.assertEqual(len(stops), 1, "music 固件播 ding 没挂停止腿（会单曲循环）")
        self.assertEqual(stops[0]["secs"], WAKE_DING_STOP_REPORT_S,
                         "报 0.2s 会因停止腿 800ms 提前量而一开播就停（叮没了）")
        stop_at_ms = stops[0]["secs"] * 1000 - 800
        self.assertGreaterEqual(stop_at_ms, WAKE_DING_DURATION_S * 1000 * 0.5,
                                "停止点比叮时长还早一半以上（用户听不到完整叮）")


if __name__ == "__main__":
    unittest.main()
