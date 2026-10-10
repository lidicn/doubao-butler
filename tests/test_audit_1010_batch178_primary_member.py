r"""批178 验收（TDD，先红后绿）：P1-42 三处硬编码成员名 `"lidicn"` 收编到 `primary_member` 配置。

来源（现读，⛔ 抄报告行号）：`doc/审计报告/归档/doubao-butler-第十四轮审计报告.md` P1-42（V26）。
落点＝三处**判定**（不是文案）：
  - `butler/morning/routine.py:94`  —— `snapshot…get("lidicn")` 决定「是否已离开主卧」。
  - `butler/modes/auto_switch.py:71` —— `users.get("lidicn")` 决定观影/睡眠两条规则。
  - `butler/timeseries/anomaly.py:172` —— 同一条定位判定，另加 `:177` 去重键、`:178` `entity_id`、
    `:181` 正文三处跟着这个名字派生。
报告给的样＝`self.s.member_by_name(member)`（`core/dialog.py:155`、`core/persona.py:15` 同族），
但这里要的不是「查成员表」而是「读一个可配的主成员名」⇒ 新增 `Settings.primary_member`
（`PRIMARY_MEMBER`，默认 `"lidicn"`＝现行为一字不动）。

闸门形制＝**同快照、只翻配置、结论必须翻**（deny-and-allow 成对）：
  - 只补配置不接线 ⇒ 配置腿红；
  - 改成「任一成员在主卧就算」⇒ 默认腿红。
最后一条形状尺钉「这三个模块里⛔ 再出现 `lidicn` 字面量」，并配双侧合成校准（能放行、能咬）。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from butler import config as config_mod
from butler.config import Settings, set_settings
from butler.modes import auto_switch
from butler.morning import routine
from butler.timeseries import anomaly

REQUIRED_KEYS = ("DOUBAO_API_KEY", "DESKPILOT_API_TOKEN", "TASK_REPORT_TOKEN", "BUTLER_WEB_PASSWORD")
DEFAULT_MEMBER = "lidicn"
OTHER_MEMBER = "Kevin"
# 2026-10-12 现读 tm_wday=0（周一）——无活动检测只在工作日 9:00-11:00 开窗
WEEKDAY_MORNING = time.mktime((2026, 10, 12, 9, 30, 0, 0, 1, -1))
ANY_MORNING = time.mktime((2026, 10, 12, 7, 30, 0, 0, 1, -1))

SOURCE_FILES = {
    "routine": "butler/morning/routine.py",
    "auto_switch": "butler/modes/auto_switch.py",
    "anomaly": "butler/timeseries/anomaly.py",
}
REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    """只替换被测模块命名空间里的 `time`（⛔ 动全进程 `time` 模块）。"""

    def __init__(self, now: float):
        self.now = now

    def time(self) -> float:
        return self.now

    def localtime(self, ts=None):
        return time.localtime(self.now if ts is None else ts)

    def strftime(self, fmt, seq=None):
        return time.strftime(fmt, seq if seq is not None else time.localtime(self.now))


class MemberPatchMixin:
    """判定腿用 `set_settings` 注入，跑完原样还回去（⛔ 把单例留给下一个模块）。"""

    def _use_member(self, member: str):
        prev = config_mod._settings
        set_settings(Settings(primary_member=member))
        self.addCleanup(set_settings, prev)


class _StubPresence:
    def __init__(self, users: dict):
        self._users = users

    def snapshot(self) -> dict:
        return {"users": self._users}


class _StubRT:
    def __init__(self, users: dict):
        self.presence_engine = _StubPresence(users)


class _StubEngine:
    def __init__(self, current: str):
        self.current = current
        self.calls: list[tuple[str, str]] = []

    def switch(self, mode: str, source: str = "") -> None:
        self.calls.append((mode, source))
        self.current = mode


class _StubHA:
    def __init__(self, states: list[dict]):
        self._states = states

    async def get_states(self) -> list[dict]:
        return self._states


class _StubTs:
    def __init__(self, mode: str, transitions: list[dict] | None = None):
        self._mode = mode
        self._transitions = transitions or []

    def get_current_mode(self) -> str:
        return self._mode

    def get_mode_transitions(self, mode: str = "", limit: int = 0) -> list[dict]:
        return list(self._transitions)

    def get_device_anomalies(self, resolved=None, limit: int = 0) -> list[dict]:
        return []


def _drive(coro) -> object:
    async def runner():
        for _ in range(20):
            await asyncio.sleep(0)
        return await coro
    return asyncio.run(runner())


class SettingsPrimaryMemberTest(unittest.TestCase):
    """配置腿：默认＝现行为，env＝可改。"""

    def _pin(self, **kv):
        kv.setdefault("DATA_DIR", self.tmp)
        kv.setdefault("TTS_DIR", self.tmp)
        for k in REQUIRED_KEYS:
            kv.setdefault(k, "probe-not-a-secret")
        old = {}
        for k, v in kv.items():
            old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.addCleanup(self._restore, old)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b178_cfg_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    @staticmethod
    def _restore(old: dict) -> None:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_keeps_current_behaviour(self):
        """默认必须是今天那个名字，否则这次改动一上线就改了现网判定。"""
        self.assertEqual(Settings().primary_member, DEFAULT_MEMBER)

    def test_env_overrides_member(self):
        self._pin(PRIMARY_MEMBER=OTHER_MEMBER)
        self.assertEqual(Settings.load().primary_member, OTHER_MEMBER)

    def test_env_unset_falls_back_to_default(self):
        self._pin(PRIMARY_MEMBER=None)
        self.assertEqual(Settings.load().primary_member, DEFAULT_MEMBER)


class MorningRoutineMemberTest(MemberPatchMixin, unittest.TestCase):
    # 同一份快照：lidicn 在主卧（高置信）、Kevin 在客厅
    USERS = {
        DEFAULT_MEMBER: {"room": "主卧", "confidence": 0.9},
        OTHER_MEMBER: {"room": "客厅", "confidence": 0.8},
    }

    def _run(self, member: str) -> dict:
        self._use_member(member)
        transitions = [
            {"action": "exit", "ts": ANY_MORNING - 600},
            {"action": "enter", "ts": ANY_MORNING - 8 * 3600},
        ]

        class _Recorder(routine.MorningRoutine):
            def __init__(self, rt):
                super().__init__(rt)
                self.broadcasts = []

            async def _build_context(self, sleep_enter_ts, transitions):
                return routine.MorningContext()

            async def _broadcast(self, ctx):
                self.broadcasts.append(ctx)

        with mock.patch.object(routine, "ts", _StubTs("daily", transitions)), \
                mock.patch.object(routine, "time", FakeClock(ANY_MORNING)), \
                mock.patch.object(routine, "_morning_reported_date", ""):
            mgr = _Recorder(_StubRT(self.USERS))
            triggered = _drive(mgr.check_and_broadcast())
        return {"triggered": triggered, "broadcasts": len(mgr.broadcasts)}

    def test_default_member_still_blocks_while_in_bedroom(self):
        """默认腿：主成员仍在主卧高置信 ⇒ 不播报（改成「读任一成员」这条就红）。"""
        out = self._run(DEFAULT_MEMBER)
        self.assertFalse(out["triggered"])
        self.assertEqual(out["broadcasts"], 0)

    def test_configured_member_is_the_one_being_read(self):
        """配置腿：主成员＝客厅那位 ⇒ 同快照下判定读的是他，播报放行。"""
        out = self._run(OTHER_MEMBER)
        self.assertTrue(out["triggered"])
        self.assertEqual(out["broadcasts"], 1)


class AutoSwitchMemberTest(MemberPatchMixin, unittest.TestCase):
    USERS = {
        DEFAULT_MEMBER: {"room": "主卧", "confidence": 0.9},
        OTHER_MEMBER: {"room": "客厅", "confidence": 0.7},
    }
    TV = [{"entity_id": "media_player.mytv", "state": "playing",
           "attributes": {"friendly_name": "客厅电视"}}]

    def _run(self, member: str):
        self._use_member(member)

        class _RT(_StubRT):
            def __init__(self, users, engine, ha):
                super().__init__(users)
                self.mode_engine = engine
                self.ha = ha

        engine = _StubEngine("daily")
        rt = _RT(self.USERS, engine, _StubHA(self.TV))
        with mock.patch.dict(auto_switch._debounce, {}, clear=True):
            switcher = auto_switch.ModeAutoSwitcher(rt)
            result = _drive(switcher.check_and_switch())
            # 快照⛔ 取在 `patch.dict` 外面：退出时它会把字典还原成进场前的样子
            return result, engine, dict(auto_switch._debounce)

    def test_default_member_does_not_trigger_movie(self):
        """默认腿：主成员在主卧 ⇒ ⛔ 因为「客厅里还有别人」就切观影。"""
        result, engine, debounce = self._run(DEFAULT_MEMBER)
        self.assertFalse(result.switched)
        self.assertNotIn("movie", debounce)
        self.assertEqual([c[0] for c in engine.calls], [])

    def test_configured_member_triggers_movie(self):
        """配置腿：主成员在客厅且 TV 开 ⇒ 观影，正文里的名字跟着配置走。"""
        result, engine, _ = self._run(OTHER_MEMBER)
        self.assertTrue(result.switched)
        self.assertEqual(result.to_mode, "movie")
        self.assertIn(OTHER_MEMBER, result.reason)
        self.assertEqual(engine.calls[0][0], "movie")


class NoActivityAnomalyTest(MemberPatchMixin, unittest.TestCase):
    USERS = {
        DEFAULT_MEMBER: {"room": "书房", "confidence": 0.9},
        OTHER_MEMBER: {"room": "主卧", "confidence": 0.9},
    }

    def _run(self, member: str) -> list:
        self._use_member(member)
        rt = _StubRT(self.USERS)
        with mock.patch.object(anomaly, "ts", _StubTs("sleep")), \
                mock.patch.object(anomaly, "time", FakeClock(WEEKDAY_MORNING)):
            detector = anomaly.AnomalyDetector(rt)
            return _drive(detector._check_no_activity())

    def test_default_member_does_not_fire_for_others(self):
        """默认腿：主成员不在主卧 ⇒ 0 条（「任一成员在主卧就叫异常」这条会红）。"""
        self.assertEqual(self._run(DEFAULT_MEMBER), [])

    def test_configured_member_fires_with_its_own_key_and_entity(self):
        """配置腿：去重键／entity_id／正文三处都得跟着名字走，⛔ 留着别人的身份发他的异常。"""
        events = self._run(OTHER_MEMBER)
        self.assertEqual(len(events), 1)
        ev = events[0]
        day = time.strftime("%Y%m%d", time.localtime(WEEKDAY_MORNING))
        self.assertEqual(ev.key, f"no_activity_{OTHER_MEMBER}_{day}")
        self.assertEqual(ev.entity_id, f"person.{OTHER_MEMBER}")
        self.assertIn(OTHER_MEMBER, ev.message)
        self.assertNotIn(DEFAULT_MEMBER, ev.message)


class NoHardcodedMemberLiteralTest(unittest.TestCase):
    """形状尺：这三个模块里⛔ 再出现 `lidicn` 字面量（判定、键、正文、注释都算）。"""

    @staticmethod
    def _count(source: str) -> int:
        return source.count(DEFAULT_MEMBER)

    def test_ruler_bites_synthetic_source(self):
        self.assertEqual(self._count('users.get("lidicn", {})\n'), 1)

    def test_ruler_passes_clean_synthetic_source(self):
        self.assertEqual(self._count('users.get(member, {})\n'), 0)

    def test_three_judgement_modules_have_no_literal(self):
        hits = {}
        for name, rel in SOURCE_FILES.items():
            hits[name] = self._count((REPO_ROOT / rel).read_text(encoding="utf-8"))
        self.assertEqual(hits, {name: 0 for name in SOURCE_FILES})


if __name__ == "__main__":
    unittest.main(verbosity=2)
