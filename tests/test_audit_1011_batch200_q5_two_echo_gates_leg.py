"""批200 Q5 门（甲）：两把回声闸**并存**是裁过的设计，本档钉住两腿各自在位、各读各的（DCD 20261011 七问裁定 §3 Q5）。

裁定原文口径：
 「Q5（甲）⛔ 不动全局 _echo_until 读侧（dialog.py:152）与设备级 _echo_cooldown（xiaomi_ear.py:150）。
   ⛔ 不得再提删全局腿，除非路线图 154 被正式修订。两把并存写进文档。」
（行号是裁定时的读数，本档按符号定位。）

两把闸管的是两段不同的事实，⛔ 互相替代：
 全局腿 `DialogManager._echo_until`＝butler 侧「我刚说过话」的窗口，读在 `on_user_utterance` 与 `on_wakeup` **两个入口**；
 设备腿 `XiaomiEar._echo_cooldown`＝**某台**小爱自己「刚被下发过播报」的窗口，读在 ear 的事件处理里。
删全局腿会让 NR／ utterance 那条入口失去保护（裁定 §2 Q5 记的独立缺陷），所以本档的红＝「有人又把合并那一档提上来了」。

批192 那 17 格管的是两腿的**口径同源**（max() 语义）；本档管**两腿都在**，两档⛔ 互相覆盖。

跑法：`python -B -m unittest tests.test_audit_1011_batch200_q5_two_echo_gates_leg`（一套一进程）。
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import time
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break

REPO = pathlib.Path(_HERE).resolve().parent
DIALOG_PY = REPO / "butler" / "core" / "dialog.py"
EAR_PY = REPO / "butler" / "core" / "xiaomi_ear.py"

DIALOG_STATE_LINE = "self._echo_until: float = 0.0"
DIALOG_READ = "time.time() < self._echo_until"
EAR_STATE_LINE = "_echo_cooldown: dict = {}"
EAR_READ = "self._echo_cooldown.get(dev.id, 0.0)"
EAR_WRITE = "cls._echo_cooldown.get(device_id, 0.0)"


def _import(what):
    try:
        return __import__(what, fromlist=["*"])
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest("宿主侧缺依赖（%s）⇒ 运行时腿只在容器内跑" % type(exc).__name__)


class TwoLegsCoexistLeg(unittest.TestCase):
    def setUp(self):
        self.dlg = _import("butler.core.dialog")
        self.ear = _import("butler.core.xiaomi_ear")
        saved = dict(self.ear.XiaomiEar._echo_cooldown)

        def _restore():
            self.ear.XiaomiEar._echo_cooldown.clear()
            self.ear.XiaomiEar._echo_cooldown.update(saved)

        self.addCleanup(_restore)

    def _dm(self):
        obj = self.dlg.DialogManager.__new__(self.dlg.DialogManager)
        obj._echo_until = 0.0
        return obj

    def test_01_the_two_legs_are_distinct_state_holders(self):
        dm = self._dm()
        self.assertIsInstance(dm._echo_until, float)
        self.assertIsInstance(self.ear.XiaomiEar._echo_cooldown, dict)
        self.assertFalse(hasattr(dm, "_echo_cooldown"), "两把闸被合成一腿了＝Q5 甲被推翻")
        self.assertFalse(hasattr(self.ear.XiaomiEar, "_echo_until"), "设备侧接管了全局窗口＝同一族")

    def test_02_suppress_echo_moves_only_the_global_leg(self):
        dm = self._dm()
        before = dict(self.ear.XiaomiEar._echo_cooldown)
        dm.suppress_echo("这是一段用来估时长的播报文本")
        self.assertGreater(dm._echo_until, time.time() + 8.0,
                           "全局窗口没被推开⇒读侧那两处等于摆设")
        self.assertEqual(before, self.ear.XiaomiEar._echo_cooldown,
                         "全局腿的写侧顺带动了设备腿⇒两把闸已经并成一把")

    def test_03_note_speak_moves_only_the_device_leg(self):
        dm = self._dm()
        global_before = dm._echo_until
        self.ear.XiaomiEar.note_speak("xiao_living", 3.0)
        self.assertIn("xiao_living", self.ear.XiaomiEar._echo_cooldown)
        self.assertEqual(global_before, dm._echo_until,
                         "设备腿的写侧顺带推了全局窗口＝一台播报全家静音")

    def test_04_global_leg_is_read_at_both_entries(self):
        src = DIALOG_PY.read_text(encoding="utf-8", errors="replace")
        self.assertEqual(2, src.count(DIALOG_READ),
                         "全局腿读侧=%d（应 2：on_user_utterance 与 on_wakeup 各一）⇒少一处＝那条入口失去保护"
                         % src.count(DIALOG_READ))
        for fn in ("on_user_utterance", "on_wakeup"):
            self.assertIn(fn, src, "%s 不在了⇒本格的读侧名册要重看" % fn)

    def test_05_device_leg_is_read_once_in_the_ear(self):
        src = EAR_PY.read_text(encoding="utf-8", errors="replace")
        self.assertEqual(1, src.count(EAR_READ), "设备腿读侧=%d（应 1）" % src.count(EAR_READ))
        self.assertEqual(1, src.count(EAR_WRITE), "设备腿写侧的 max() 腿=%d（应 1）" % src.count(EAR_WRITE))


# ---- 形状尺：两腿「被删」必须咬（两侧校准，⛔ 只跑真文件）----
CLEAN_DIALOG = (
    "    def __init__(self):\n"
    "        self._echo_until: float = 0.0\n"
    "    def on_user_utterance(self):\n"
    "        if time.time() < self._echo_until:\n"
    "            return\n"
    "    def on_wakeup(self):\n"
    "        if time.time() < self._echo_until:\n"
    "            return\n"
)
SYN_DIALOG_NO_STATE = CLEAN_DIALOG.replace("        self._echo_until: float = 0.0\n", "")
SYN_DIALOG_ONE_READ = CLEAN_DIALOG.replace(
    "    def on_wakeup(self):\n        if time.time() < self._echo_until:\n            return\n",
    "    def on_wakeup(self):\n        return\n")
CLEAN_EAR = (
    "class XiaomiEar:\n"
    "    _echo_cooldown: dict = {}\n"
    "    def note_speak(cls, device_id):\n"
    "        cls._echo_cooldown.get(device_id, 0.0)\n"
    "    def _handle_event(self):\n"
    "        expiry = self._echo_cooldown.get(dev.id, 0.0)\n"
)
SYN_EAR_NO_STATE = CLEAN_EAR.replace("    _echo_cooldown: dict = {}\n", "")
SYN_EAR_NO_READ = CLEAN_EAR.replace("        expiry = self._echo_cooldown.get(dev.id, 0.0)\n", "")


def dialog_hits(src: str) -> list:
    out = []
    if DIALOG_STATE_LINE not in src:
        out.append("global-leg-state-gone")
    n = src.count(DIALOG_READ)
    if n != 2:
        out.append("global-leg-read-count-%d" % n)
    return out


def ear_hits(src: str) -> list:
    out = []
    if EAR_STATE_LINE not in src:
        out.append("device-leg-state-gone")
    if src.count(EAR_READ) != 1:
        out.append("device-leg-read-gone")
    if src.count(EAR_WRITE) != 1:
        out.append("device-leg-write-gone")
    return out


CALIB = (
    ("CLEAN-dialog", dialog_hits, CLEAN_DIALOG, []),
    ("SYN-dialog 状态腿没了", dialog_hits, SYN_DIALOG_NO_STATE, ["global-leg-state-gone"]),
    ("SYN-dialog 只剩一处读", dialog_hits, SYN_DIALOG_ONE_READ, ["global-leg-read-count-1"]),
    ("CLEAN-ear", ear_hits, CLEAN_EAR, []),
    ("SYN-ear 状态腿没了", ear_hits, SYN_EAR_NO_STATE, ["device-leg-state-gone"]),
    ("SYN-ear 读腿没了", ear_hits, SYN_EAR_NO_READ, ["device-leg-read-gone"]),
)


class ShapeRulerLeg(unittest.TestCase):
    def test_06_ruler_calibrated_both_ways(self):
        for label, fn, src, want in CALIB:
            self.assertEqual(want, fn(src), "%s 校准不符" % label)

    def test_07_real_files_on_judgment(self):
        self.assertEqual([], dialog_hits(DIALOG_PY.read_text(encoding="utf-8", errors="replace")),
                         "全局腿形状现读不符⇒点开看 dialog.py")
        self.assertEqual([], ear_hits(EAR_PY.read_text(encoding="utf-8", errors="replace")),
                         "设备腿形状现读不符⇒点开看 xiaomi_ear.py")

    def test_08_coexistence_claim_is_written_in_the_gate(self):
        doc = __doc__ or ""
        self.assertIn("两把回声闸", doc)
        self.assertIn("⛔ 不得再提删全局腿", doc, "禁令原文不见了⇒下一任会重新提删（裁定 §2 Q5 记的独立缺陷）")
        self.assertIn("路线图 154", doc, "「除非路线图 154 正式修订」这个前置条件⛔ 丢")


if __name__ == "__main__":
    unittest.main(verbosity=2)
