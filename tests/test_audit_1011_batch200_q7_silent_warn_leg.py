"""批200 Q7 门（丙 先落）：`silent: True` 那条必须伴随一条 **WARN**（DCD 20261011 七问裁定 §3 Q7）。

裁定原文口径：
 「丙（立即）：dialog.py:398 那条 INFO 升 **WARN**。
   乙（排窗口）：须一个现网窗口读回小爱对话传感器尾部文本；本席不批窗口，须 owner 逐次点头。
   ⛔ 不得在取证前把闸门改成「假设小爱自己会答」（那是甲）。」
验收对照（§4-5）：`silent: True` 必须伴随一条 WARN（负控：删 WARN 判据必红）。

行号按符号定位（:398 是裁定时的读数，⛔ 算术）：`on_wakeup` 内 `if source == "xiaoai":` 那一块。
本门⛔ 起 DB、⛔ 起 LLM、⛔ 真发声——沿用批44 那套最小假件（同形⇒ 两档门喂料一致）。

跑法：`python -B -m unittest tests.test_audit_1011_batch200_q7_silent_warn_leg`（一套一进程）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break


def _find_root():
    for cand in (_HERE, os.path.dirname(_HERE), "/app", os.getcwd()):
        if os.path.isfile(os.path.join(cand, "butler", "core", "dialog.py")):
            return cand
    raise unittest.SkipTest("找不到 butler/core/dialog.py⇒ 源码腿无物可读")


ROOT = _find_root()
DIALOG_SOURCE = os.path.join(ROOT, "butler", "core", "dialog.py")

RULE_TEXT = "打开客厅的灯"
SILENT_KEY = "skip TTS"
LOGGER_NAME = "butler.dialog"
BLOCK_HEAD = 'if source == "xiaoai":'
STATES = [{"entity_id": "light.ke_ting", "attributes": {"friendly_name": "客厅灯"}}]


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _import_dialog():
    try:
        import butler.core.dialog as dlg
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest("宿主侧缺依赖（%s）⇒ 运行时腿只在容器内跑" % type(exc).__name__)
    return dlg


class FakeHa:
    def __init__(self, result="ok (200)"):
        self.result = result
        self.calls = []
        self.get_states_calls = 0

    async def get_states(self):
        self.get_states_calls += 1
        return STATES

    async def call_service(self, domain, service, data=None):
        self.calls.append((domain, service, dict(data or {})))
        return self.result


class FakeState:
    def __init__(self):
        self.states = []

    def set_state(self, state):
        self.states.append(state)


class Harness(unittest.TestCase):
    def setUp(self):
        self.dlg = _import_dialog()
        self.saved_runtime = self.dlg.get_runtime
        self.role = types.SimpleNamespace(
            id="butler", name="豆包管家", enabled=True, scope="shared",
            bound_rooms=[], presence_rooms=["*"], member="家人", system="",
        )
        self.spoken = []

        async def _speak_as_role(r, reply, room, member, source_device=""):
            self.spoken.append(reply)
            return reply

        async def _speak_transition(fn):
            await fn()
            return list(self.spoken)

        obj = self.dlg.DialogManager.__new__(self.dlg.DialogManager)
        obj._echo_until = 0.0
        obj._pending_skill_desc = {}
        obj.state = FakeState()
        obj.speak_as_role = _speak_as_role
        obj._speak_transition = _speak_transition
        obj._spawn_idle = lambda: None
        self.d = obj

        from butler.skills.engines.llm_decide.ask import pending_ask_manager as pam
        self.saved_check = pam.check
        pam.check = lambda room, role_id="": None
        self.addCleanup(lambda: setattr(pam, "check", self.saved_check))
        self.addCleanup(lambda: setattr(self.dlg, "get_runtime", self.saved_runtime))

    def _wire(self, ha):
        rt = types.SimpleNamespace(roles={"butler": self.role}, skill_creator=None, ha=ha)
        self.dlg.get_runtime = lambda: rt
        return rt

    def _wakeup(self, source="xiaoai"):
        return _run(self.d.on_wakeup("butler", "客厅", RULE_TEXT, member="家人", source=source))

    def _capture(self, factory):
        with _Capture(LOGGER_NAME) as cap:
            out = factory()
        return out, [r for r in cap.records if SILENT_KEY in r.getMessage()]


class SilentWarnBehaviorLeg(Harness):
    def test_01_silent_result_accompanies_exactly_one_warn(self):
        # 裁定的验收格：拿到 silent: True ⇒ 同一次调用里必有一条带 skip TTS 的 WARN
        ha = FakeHa()
        self._wire(ha)
        out, keyed = self._capture(self._wakeup)
        self.assertTrue(out.get("silent"), out)
        warns = [r for r in keyed if r.levelno == logging.WARNING]
        self.assertEqual(1, len(warns), cm_hint([r.getMessage() for r in keyed]))

    def test_02_the_record_level_is_warning_exactly(self):
        # 升一级＝丙；顺手升成 ERROR＝把设计内的静默报成事故，档位也一起钉住
        ha = FakeHa()
        self._wire(ha)
        out, keyed = self._capture(self._wakeup)
        self.assertEqual(1, len(keyed), cm_hint([r.getMessage() for r in keyed]))
        self.assertEqual(logging.WARNING, keyed[0].levelno,
                         "级别现读=%s⇒ 既⛔ 是 INFO（窗口里被淹没）也⛔ 是 ERROR（事故化）"
                         % logging.getLevelName(keyed[0].levelno))
        self.assertTrue(out.get("silent"))

    def test_03_active_path_emits_no_silent_warn(self):
        # ⛔ 把这条 WARN 挪出 xiaoai 分支⇒ 带房间的常规档位天天响＝噪声闸
        ha = FakeHa()
        self._wire(ha)
        out, keyed = self._capture(lambda: self._wakeup(source="active"))
        self.assertEqual([], [r.getMessage() for r in keyed], cm_hint([r.getMessage() for r in keyed]))
        self.assertFalse(out.get("silent"), out)
        self.assertEqual(1, len(ha.calls), "active 档没走到 HA⇒ 本条在测空气")

    def test_04_silent_shape_untouched(self):
        # 丙＝只升级别。返回值形状、⛔ 播报、⛔ 发 HA 三条腿必须原样
        ha = FakeHa()
        self._wire(ha)
        out, keyed = self._capture(self._wakeup)
        self.assertEqual([], ha.calls)
        self.assertEqual([], self.spoken)
        self.assertEqual([], out["spoken"])
        self.assertEqual("", out["reply"])
        self.assertTrue(out.get("simple_rule"), out)
        self.assertEqual(self.dlg.DialogState.WAITING, self.d.state.states[-1],
                         "闸门的状态收尾腿被顺带动了（现读整段=%s）" % [s.name for s in self.d.state.states])

    def test_05_warn_carries_the_reading_key(self):
        # 窗口取证时靠这句在日志里定位；⛔ 换成另一句⇒ 那条「乙排窗口」的读法失效
        ha = FakeHa()
        self._wire(ha)
        _, keyed = self._capture(self._wakeup)
        self.assertEqual(1, len(keyed), cm_hint([r.getMessage() for r in keyed]))
        self.assertIn("xiaoai", keyed[0].getMessage(), keyed[0].getMessage())


def cm_hint(lines):
    return "现读带 %r 的行=%d 条：%s" % (SILENT_KEY, len(lines), lines)


class _Capture(logging.Handler):
    """自己挂 handler⛔ 用 assertLogs：后者在「一条都没打」时也判红，会把⛔ 响的档位读成缺陷。"""

    def __init__(self, name):
        super().__init__()
        self.name_ = name
        self.records = []

    def __enter__(self):
        self.logger = logging.getLogger(self.name_)
        self.old_level, self.old_prop = self.logger.level, self.logger.propagate
        self.logger.addHandler(self)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self)
        self.logger.setLevel(self.old_level)
        self.logger.propagate = self.old_prop
        return False

    def emit(self, record):
        self.records.append(record)


# ---- 形状尺（⛔ 只跑真文件⇒ 两侧校准）----
LOG_LINE = ('                logger.warning("simple device control from xiaoai, '
            'skip TTS (handled natively): %s", message[:30])\n')
IDLE_LINE = "                self.state.set_state(DialogState.WAITING)\n"
CLEAN_BLOCK = (
    'def on_wakeup(self):\n'
    '        if action.get("action") == "call_service":\n'
    '            if source == "xiaoai":\n'
    + LOG_LINE
    + IDLE_LINE
    + '                self._spawn_idle()\n'
    '                return {"ok": True, "reply": "", "spoken": [], "simple_rule": True, "silent": True}\n'
)
SYNTH_INFO = CLEAN_BLOCK.replace("logger.warning(", "logger.info(")
SYNTH_NO_LOG = CLEAN_BLOCK.replace(LOG_LINE, "")
SYNTH_NO_FLAG = CLEAN_BLOCK.replace(' "simple_rule": True, "silent": True}', ' "simple_rule": True}')
SYNTH_ANSWERS = CLEAN_BLOCK.replace(
    IDLE_LINE,
    '                await self.speak_as_role(role, "好的", room, member)\n' + IDLE_LINE)
SYNTH_DOUBLE = CLEAN_BLOCK.replace(
    IDLE_LINE, '                logger.warning("second reading key skip TTS here")\n' + IDLE_LINE)


def block_of(src: str):
    """取 `if source == "xiaoai":` 那一块（到它自己的 return 行为止）；整块不在⇒ None。"""
    start = src.find(BLOCK_HEAD)
    if start < 0:
        return None
    end = src.find('"silent": True', start)
    if end < 0:
        return src[start:]
    return src[start:src.index("\n", end) + 1]


def hits(block) -> list:
    if block is None:
        return ["silent-gate-block-missing"]
    out = []
    logged = [ln for ln in block.splitlines() if SILENT_KEY in ln]
    if not logged:
        out.append("no-silent-log-line")
    elif any(ln.lstrip().startswith("logger.info(") for ln in logged):
        out.append("level-still-info")
    if len(logged) > 1:
        out.append("duplicate-silent-log")
    if '"silent": True' not in block:
        out.append("silent-flag-missing")
    if "speak_as_role" in block or "_emit_devices" in block or "self.speak(" in block:
        out.append("gate-became-answers")  # 甲的形状：改口成「假设小爱自己会答」⇒ 本裁禁止
    return out


CALIB = (
    ("CLEAN-应放行", CLEAN_BLOCK, []),
    ("SYNTH-仍是 INFO", SYNTH_INFO, ["level-still-info"]),
    ("SYNTH-日志腿被删", SYNTH_NO_LOG, ["no-silent-log-line"]),
    ("SYNTH-返回值丢了 silent 旗", SYNTH_NO_FLAG, ["silent-flag-missing"]),
    ("SYNTH-加了播报（甲）", SYNTH_ANSWERS, ["gate-became-answers"]),
    ("SYNTH-两条同键日志", SYNTH_DOUBLE, ["duplicate-silent-log"]),
    ("SYNTH-整块没了", None, ["silent-gate-block-missing"]),
)


class ShapeRulerLeg(unittest.TestCase):
    def test_06_ruler_calibrated_both_ways(self):
        for label, block, want in CALIB:
            self.assertEqual(want, hits(block), "%s 校准不符" % label)

    def test_07_real_block_on_judgment(self):
        src = open(DIALOG_SOURCE, encoding="utf-8", errors="replace").read()
        block = block_of(src)
        self.assertIsNotNone(block, "xiaoai 静默闸门整块找不到了⇒ 判据要重看")
        self.assertEqual([], hits(block), hits(block))

    def test_08_exactly_one_silent_log_site_in_whole_file(self):
        src = open(DIALOG_SOURCE, encoding="utf-8", errors="replace").read()
        n = sum(1 for ln in src.splitlines() if SILENT_KEY in ln and "logger." in ln)
        self.assertEqual(1, n, "全档带 %r 的日志点=%d（应 1）⇒ 窗口读数会双计" % (SILENT_KEY, n))


if __name__ == "__main__":
    unittest.main(verbosity=2)
