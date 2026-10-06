"""批10 验收＝DCD 裁定②（模式行为规则接上三个入口，ALERT 必须豁免）。

裁定文书＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md:32-42`：
  > **落地点**：发声（`tts/`）/ 推送（`notify/router.py`）/ 技能执行（`skills/runner.py`）
  > 三个入口各查一次 `can_tts`/`can_bark`/`can_run_skill`
  > 风险确认：最容易翻车的不是崩而是「该响的没响」——验收必须包含「睡眠模式下 ALERT 级仍能播」
  `security_level` 本轮不算（:38，已登记独立议题）。

现读（快照 `_scratch_1002/butler`，与部署树 HEAD `f517305` 逐字节对过）＝改前的形状：
  · `modes/engine.py:243/250/256/273` 四枚判定函数**全树零生产调用者**（`can_tts`/`can_bark`/
    `can_run_skill`/`is_care_skill_disabled`），`MODE_RULES` 五枚规则键里 `care_skills_disabled`
    只有 `is_care_skill_disabled` 一个读者，而它没人调 ⇒ 五键全空转；
  · `tts/manager.py:183 async def speak(...)` 是「全家唯一这张嘴」（:192 自述），签名里
    ⛔ 没有 priority ⇒ ALERT 豁免无路可走；队列那条腿 `tts/adapter.py:62` 拿到了
    `TTSItem.priority`（`tts/queue.py:42 PRIORITY_ALERT = 1`）却没往嘴上传；
  · `notify/router.py:295 _to_bark` 直接 `bark.push`，`push_guard.py:223` 那句「由调用方在
    调用前检查模式」的调用方就是零调用的 `can_bark`；
  · `skills/runner.py:140 run()` 的门只有 quarantine／breaker／per-day，没有模式。

⛔ 本文件不覆盖的（留给台账／下一批裁）：
  1. 规则**数值**本身（观影模式 `tts_emergency_only=False` ⇒ ALERT 也被压；离家 `skills_allowed=none`
     ⇒ 安防类技能也不跑）——本文件只把「写在表里的规则」接到链路上，改表是产品语义变更，要裁；
  2. `core/dialog.py:708`、`tools/schedule.py:106`、`decision/action_router.py:80 ha.notify_message`
     这三条绕过 `tts/` 直接叫 ha 的嘴（本裁定的落地点写的是 `tts/`）；
  3. `tts/helper.py:20 override_quiet=True` 是**默认值**，队列那层夜间静默对 helper 调用方形同虚设
     ——与裁定⑤（两套队列归一）同域，本批不动。

命名纪律：全部写成 `unittest.TestCase` 方法（⛔ 模块级 `test_*`，discover 不 import pytest 时会静默收下）。
运行时腿按跟办 3 用假引擎／假 bark／假 store，⛔ 起服务、⛔ 连现网 MQTT、⛔ 碰活库。
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import time
import unittest

from butler.config import get_settings
from butler.modes.engine import MODE_RULES, ModeEngine, ModeState
from butler.notify.router import NotifyRouter
from butler.runtime import _RT
from butler.skills.runner import SkillRunner
from butler.tts.adapter import TTSManagerSpeaker
from butler.tts.manager import TTSManager
from butler.tts.queue import PRIORITY_ALERT, TTSItem

_MISSING = object()


def _engine(mode: str) -> ModeEngine:
    """真 ModeEngine＋真 MODE_RULES，但绕开 `_load_current()`（它要开时序库）。"""
    eng = ModeEngine.__new__(ModeEngine)
    eng.rt = None
    eng._state = ModeState(mode=mode, since=time.time(), source="test")
    return eng


class ModeRuntimeStub(unittest.TestCase):
    """所有会往 `_RT.mode_engine` 上塞东西的类的公共底座：存取必须成对，⛔ 污染同进程别的用例。"""

    def setUp(self):
        self._saved = getattr(_RT, "mode_engine", _MISSING)

    def tearDown(self):
        if self._saved is _MISSING:
            try:
                del _RT.mode_engine
            except AttributeError:
                pass
        else:
            _RT.mode_engine = self._saved

    def _set(self, mode: str | None):
        if mode is None:
            try:
                del _RT.mode_engine
            except AttributeError:
                pass
        else:
            _RT.mode_engine = _engine(mode)


class GateApiTest(ModeRuntimeStub):
    """三个入口共用的那唯一一处 fail-open 读取点。"""

    def test_gate_helpers_exist(self):
        import butler.modes.engine as me
        for name in ("get_mode_engine", "gate_tts", "gate_bark", "gate_skill"):
            self.assertTrue(callable(getattr(me, name, None)),
                            f"缺 {name}：三入口各自 fail-open 会写出四套，正是本批的病因（判例：假承诺隐形）")

    def test_get_mode_engine_reads_the_runtime_attribute(self):
        from butler.modes.engine import get_mode_engine
        eng = _engine("sleep")
        self._set("sleep")
        _RT.mode_engine = eng
        self.assertIs(get_mode_engine(), eng, "模式引擎在 app.py:427 挂在 rt.mode_engine 上，读取点必须认这个名")

    def test_gates_fail_open_when_engine_is_absent(self):
        from butler.modes.engine import gate_bark, gate_skill, gate_tts
        self._set(None)
        self.assertEqual(gate_tts(), (True, ""), "取不到模式引擎⇒让它响（DCD②：最怕「该响的没响」）")
        self.assertEqual(gate_bark(), (True, ""))
        self.assertEqual(gate_skill("batch10_any"), (True, ""))


class ZeroCallerGuardTest(unittest.TestCase):
    """把「判定函数没人调」这件事本身钉成闸——本批六件的共同病因，⛔ 靠人记。"""

    @staticmethod
    def _call_sites(name: str) -> int:
        """数调用形式 `name(`，减掉 `def name(` 那行（定义不是调用）。"""
        pat = re.compile(r"(?<!def )" + re.escape(name) + r"\(")
        root = pathlib.Path(__file__).resolve().parent.parent / "butler"
        hits = 0
        for f in sorted(root.rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            hits += len(pat.findall(f.read_text(encoding="utf-8")))
        return hits

    def test_each_mode_judgement_has_a_real_caller(self):
        for name in ("can_tts", "can_bark", "can_run_skill", "is_care_skill_disabled"):
            self.assertGreaterEqual(self._call_sites(name), 1,
                                    f"{name} 又是零调用判定函数＝下一枚影子代码")

    def test_three_entries_each_call_their_gate(self):
        root = pathlib.Path(__file__).resolve().parent.parent / "butler"
        for rel, gate in (("tts/manager.py", "gate_tts"),
                          ("notify/router.py", "gate_bark"),
                          ("skills/runner.py", "gate_skill")):
            src = (root / rel).read_text(encoding="utf-8")
            self.assertGreaterEqual(len(re.findall(re.escape(gate) + r"\(", src)), 1,
                                    f"{rel} 没查 {gate}＝裁定②那个入口没接上（文书在装作在做）")

    def test_all_five_mode_rule_keys_are_read(self):
        import butler.modes.engine as me
        src = pathlib.Path(me.__file__).read_text(encoding="utf-8")
        for key in MODE_RULES["daily"]:
            if key == "security_level":
                continue  # 裁定 :38 本轮不算，已登记独立议题
            self.assertGreaterEqual(len(re.findall(r'rules\["%s"\]' % key, src)), 1,
                                    f"规则键 {key} 写进表里却没人读＝空号（裁定③同类病）")


class TTSMouthGateTest(ModeRuntimeStub):
    """发声入口：`tts/manager.py speak`。"""

    def _mgr(self):
        mgr = TTSManager(get_settings(), bark=None)
        seen: list[dict] = []

        async def _fake_synthesize(text, **kw):
            seen.append({"text": text, **kw})
            return object()

        mgr.synthesize = _fake_synthesize  # ⛔ 真合成会打网络／落音频文件
        return mgr, seen

    def test_sleep_mode_silences_ordinary_speech(self):
        mgr, seen = self._mgr()
        self._set("sleep")
        res = asyncio.run(mgr.speak("该喝水了", device_id="xiao_living"))
        self.assertEqual(seen, [], "睡眠模式 tts_allowed=False，改前这张嘴照合成照播（夜里电视照旧开口）")
        self.assertIsNone(res, "被模式压掉时不能回一个「已经播了」的假结果")

    def test_sleep_mode_still_plays_alert_priority(self):
        """DCD② 验收硬格：睡眠模式下 ALERT 级仍能播。"""
        mgr, seen = self._mgr()
        self._set("sleep")
        asyncio.run(mgr.speak("燃气泄漏告警", device_id="xiao_living", priority=PRIORITY_ALERT))
        self.assertEqual([s["text"] for s in seen], ["燃气泄漏告警"],
                         "tts_emergency_only=True 就是给 ALERT 留的门，压掉它＝该响的没响")
        self.assertEqual(PRIORITY_ALERT, 1, "豁免语义挂在 queue 的 ALERT=1（数值档，1 最高）")

    def test_movie_mode_follows_the_declared_rule(self):
        """规则表说观影连紧急也不播（tts_emergency_only=False）——本例钉「接上的是表里的值」，
        ⛔ 顺手改表：那是产品语义变更，要单独裁。"""
        mgr, seen = self._mgr()
        self._set("movie")
        asyncio.run(mgr.speak("燃气泄漏告警", device_id="xiao_living", priority=PRIORITY_ALERT))
        self.assertEqual(seen, [])

    def test_daily_mode_is_untouched(self):
        mgr, seen = self._mgr()
        self._set("daily")
        asyncio.run(mgr.speak("今天有雨", device_id="xiao_living", priority=PRIORITY_ALERT))
        self.assertEqual(len(seen), 1)

    def test_tts_mouth_fails_open_without_engine(self):
        mgr, seen = self._mgr()
        self._set(None)
        asyncio.run(mgr.speak("今天有雨", device_id="xiao_living"))
        self.assertEqual(len(seen), 1, "模式层整个不可用⇒⛔ 让整屋变哑（宁多勿漏，与 bark.py:89 同策）")


class AdapterPriorityLegTest(unittest.TestCase):
    """ALERT 要能活着走到那张嘴：`tts/adapter.py` 是队列→嘴的唯一接缝。"""

    def test_adapter_passes_item_priority_to_the_mouth(self):
        calls: list[dict] = []

        class FakeManager:
            ha = None

            async def speak(self, text, **kw):
                calls.append({"text": text, **kw})

        item = TTSItem(text="漏水了", priority=PRIORITY_ALERT, device_id="xiao_living")
        ok = asyncio.run(TTSManagerSpeaker(FakeManager()).speak(item))
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0].get("priority"), PRIORITY_ALERT,
                         "adapter 拿到了 item.priority 却没往 manager.speak 传 ⇒ 睡眠模式下 ALERT 与闲聊同命")


class _FakeBark:
    def __init__(self):
        self.pushes: list[dict] = []

    async def push(self, body, *, title="", **extra):
        self.pushes.append({"body": body, "title": title, **extra})
        return True


class BarkRouteGateTest(ModeRuntimeStub):
    """推送入口：`notify/router.py _to_bark`。"""

    def _route(self, mode, **kw):
        bark = _FakeBark()
        self._set(mode)
        res = asyncio.run(NotifyRouter(bark=bark).notify("bark", "客厅漏水", **kw))
        return bark, res

    def test_sleep_mode_drops_the_push(self):
        bark, res = self._route("sleep")
        cr = res.results["bark"]
        self.assertFalse(cr.ok, "sleep bark_silent=True，改前照样推（push_guard.py:223 那句「调用方查模式」的调用方不存在）")
        self.assertEqual(bark.pushes, [], "被模式压掉就不该已经发出去")
        self.assertTrue(str(cr.error).startswith("mode_blocked"), f"失败原因要点名是模式压的：{cr.error!r}")

    def test_passive_level_still_goes_through_in_sleep(self):
        bark, res = self._route("sleep", bark_kwargs={"level": "passive"})
        self.assertTrue(res.results["bark"].ok,
                        "can_bark(silent=True) 的语义＝静默模式只放静默推送（level=passive）")
        self.assertEqual(len(bark.pushes), 1)

    def test_daily_and_guest_are_untouched(self):
        for mode in ("daily", "guest", "away"):
            self.assertEqual(MODE_RULES[mode]["bark_silent"], False, "前提：这三档没声明静默")
            bark, res = self._route(mode)
            self.assertTrue(res.results["bark"].ok, mode)
            self.assertEqual(len(bark.pushes), 1, mode)

    def test_bark_gate_fails_open_without_engine(self):
        bark, res = self._route(None)
        self.assertTrue(res.results["bark"].ok)
        self.assertEqual(len(bark.pushes), 1)


class _FakeStore:
    def __init__(self, skill):
        self.skill = skill

    def get(self, skill_id):
        return self.skill

    def list(self):
        return [self.skill]


class _FakeRegistry:
    def __init__(self):
        self.requested: list[str] = []

    def get(self, engine_name):
        self.requested.append(engine_name)
        return None  # 引擎取不到⇒run() 在闸门之后立刻返回 engine_missing（不碰库、不跑引擎）


def _runner(skill_id: str, engine: str = "static_text"):
    skill = {"id": skill_id, "name": "批10 夹具", "status": "enabled", "enabled": True,
             "approval": "approved", "brain": {"engine": engine},
             "output": [{"type": "tv_notify"}], "limits": {}}
    reg = _FakeRegistry()
    return SkillRunner(get_settings(), _FakeStore(skill), reg), reg


class SkillRunGateTest(ModeRuntimeStub):
    """技能执行入口：`skills/runner.py run`。

    放行探针的读数口径：blocked⇒status="mode_blocked"；闸门放行⇒跑到引擎查找那行、
    因假 registry 返回 None 而得 status="engine_missing"。两档都是纯内存路径，⛔ 落库。
    """

    def _run(self, mode, skill_id, **kw):
        runner, reg = _runner(skill_id)
        self._set(mode)
        res = asyncio.run(runner.run(skill_id, source="test", **kw))
        return res, reg

    def test_away_mode_blocks_skill_execution(self):
        res, reg = self._run("away", "batch10_water_plant")
        self.assertEqual(res.get("status"), "mode_blocked", f"away skills_allowed=none，改前照跑：{res}")
        self.assertEqual(reg.requested, [], "被压掉的技能不该已经取到引擎")

    def test_sleep_mode_blocks_care_skill_in_guest(self):
        """guest：skills_allowed=all 但 care_skills_disabled=True ⇒ 关怀类要挡（第五枚规则键唯一的读者）。"""
        res, _reg = self._run("guest", "batch10_morning_routine_demo")
        self.assertEqual(res.get("status"), "mode_blocked",
                         f"care_skills_disabled 现在只有零调用的 is_care_skill_disabled 在读：{res}")

    def test_sleep_mode_lets_emergency_skill_through(self):
        res, reg = self._run("sleep", "batch10_fire_alert_probe")
        self.assertEqual(res.get("status"), "engine_missing", f"sleep 是 emergency_only，火警类该放行：{res}")
        self.assertEqual(reg.requested, ["static_text"])

    def test_care_skill_runs_in_daily(self):
        res, _reg = self._run("daily", "batch10_morning_routine_demo")
        self.assertEqual(res.get("status"), "engine_missing")

    def test_force_and_dry_run_bypass_the_mode_gate(self):
        """人工按下的「立即执行／试运行」不当模式的面：run(force=True) 在本仓一直是「跳过编排层检查」的口径。"""
        res, _reg = self._run("away", "batch10_water_plant", force=True)
        self.assertEqual(res.get("status"), "engine_missing")
        res2, _reg2 = self._run("away", "batch10_water_plant", dry_run=True)
        self.assertEqual(res2.get("status"), "engine_missing")

    def test_skill_gate_fails_open_without_engine(self):
        res, _reg = self._run(None, "batch10_water_plant")
        self.assertEqual(res.get("status"), "engine_missing", "模式层不可用⇒照常跑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
