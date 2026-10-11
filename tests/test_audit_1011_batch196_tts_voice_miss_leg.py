"""批196：TTS 角色查表 miss 观测（DCD 裁定 20261011-DB-TTS角色音色表与成员名分叉）。

裁定档位：**丙（维持现状）＋ miss 观测**，方向批准为「甲-先看数」。
- 只做：`tts_routes.py` 两处查表 miss ⇒ 具名日志＋计数。
- ⛔ 折叠大小写、⛔ 动 `ROLE_VOICES`／`ROLE_ICONS`／`ROLE_TO_USER` 键面、⛔ 动播种字典、⛔ 改返回值／判定。
  理由（裁定 §2）：归一化的收益量不出来（跨仓实参拼法分布未测），而它的代价是 owner 耳朵立刻可感的换嗓子。

开工前复跑（裁定 §7 三腿，件 `workorders/readings/1011/b195_preflight_1011.txt`，HEAD `58c36f0`）：
`:15 ROLE_VOICES = {`、`:40 ROLE_ICONS = {`、`:55 ROLE_TO_USER = {`、`:70 return ROLE_VOICES.get(role)`、
`:91 user_id = ROLE_TO_USER.get(role)` ⇒ 与申请件一致，可以落地。
现读键面（程序化取，⛔ 手敲）：ROLE_VOICES 11／ROLE_ICONS 10／ROLE_TO_USER 3。

本档既钉**行为**（miss 真的留痕、hit 真的⛔ 留痕、返回值一个字节没变），也钉**反回归**
（大小写折叠一旦出现就红、键面一旦动了就红）。形状尺配两侧合成校准（`test_08`）。

`butler/api/tts_routes.py` 可 import（sqlite 连接哨兵现读 hits=0），⛔ import `butler/app.py`。

跑法：`python3 -B -m unittest tests.test_audit_1011_batch196_tts_voice_miss_leg`
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = pathlib.Path(_HERE).parent
for _cand in (_HERE, REPO_ROOT):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break

for _k, _v in (("DOUBAO_API_KEY", "dummy1"), ("DESKPILOT_API_TOKEN", "dummy2"),
               ("TASK_REPORT_TOKEN", "dummy3"), ("BUTLER_WEB_PASSWORD", "dummy4")):
    os.environ.setdefault(_k, _v)


def _skip_if_missing(exc: Exception) -> None:
    raise unittest.SkipTest("批196 宿主侧缺依赖（%s: %s）⇒ 本档只在容器内跑"
                            % (type(exc).__name__, exc))


try:
    import butler.api.tts_routes as tr
except Exception as _exc:  # noqa: BLE001
    _skip_if_missing(_exc)

TTS_PY = pathlib.Path(os.path.abspath(tr.__file__))

# 键面快照（stamp=2026-10-11T03:10:27Z 现读，本档的唯一「⛔ 动键面」依据；要动先改裁定）
VOICES_KEYS = {"butler", "emily", "jarvis", "kevin", "lidicn", "xiaoai", "xiaotiancai",
               "小爱", "小甜菜", "管家", "豆包管家"}
ICONS_KEYS = {"butler", "emily", "kevin", "lidicn", "xiaoai", "xiaotiancai",
              "小爱", "小甜菜", "管家", "豆包管家"}
TO_USER_KEYS = {"emily", "kevin", "lidicn"}


class _NoRt:
    """假 runtime：presence_engine 缺位 ⇒ `_resolve_room_by_presence` 在 hit 后也走 None 分支。"""


def _miss_keys() -> list[str]:
    return list(tr.voice_miss_counts().keys())


def _reset() -> None:
    tr._VOICE_MISS.clear()


class MissCounting(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_01_voice_miss_is_counted_and_return_unchanged(self):
        self.assertIsNone(tr._resolve_voice(role="caesar"), "miss 的返回值⛔ 被本批改动")
        self.assertEqual(_miss_keys(), ["ROLE_VOICES|_resolve_voice|caesar"])
        self.assertEqual(tr.voice_miss_counts()[_miss_keys()[0]], 1)

    def test_02_voice_hit_is_not_counted(self):
        hit_key = sorted(tr.ROLE_VOICES)[0]
        self.assertEqual(tr._resolve_voice(role=hit_key), tr.ROLE_VOICES[hit_key])
        self.assertEqual(_miss_keys(), [], "hit 也计数＝读数撒谎")

    def test_03_explicit_voice_short_circuits_without_counting(self):
        self.assertEqual(tr._resolve_voice(role="caesar", voice="zh-CN-YunxiNeural"),
                         "zh-CN-YunxiNeural")
        self.assertEqual(_miss_keys(), [], "显式音色优先时⛔ 记角色 miss")

    def test_04_empty_role_is_not_a_miss(self):
        self.assertIsNone(tr._resolve_voice(role=""))
        self.assertIsNone(tr._resolve_room_by_presence(_NoRt(), ""))
        self.assertEqual(_miss_keys(), [], "空串调用点会刷屏（裁定 §5 的日志量异常）")

    def test_05_to_user_miss_is_counted_and_returns_none(self):
        self.assertIsNone(tr._resolve_room_by_presence(_NoRt(), "xiaoyue"))
        self.assertEqual(_miss_keys(), ["ROLE_TO_USER|_resolve_room_by_presence|xiaoyue"])

    def test_06_to_user_hit_is_not_counted(self):
        self.assertIsNone(tr._resolve_room_by_presence(_NoRt(), "lidicn"))
        self.assertEqual(_miss_keys(), [], "hit 后 engine 缺位≠查表 miss")


class NamedLog(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_07_miss_emits_one_named_line(self):
        with self.assertLogs("butler.api.tts", level="INFO") as cap:
            tr._resolve_voice(role="luna")
        lines = [x for x in cap.output if "tts_role_lookup_miss" in x]
        self.assertEqual(len(lines), 1, "具名标记恰好一条（窗口靠它 grep 取分布）")
        self.assertIn("role=luna", lines[0])
        self.assertIn("table=ROLE_VOICES", lines[0])


class ShapeRuler(unittest.TestCase):
    def _sites(self, src_text: str) -> dict[str, bool]:
        tree = ast.parse(src_text)
        out: dict[str, bool] = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in (
                    "_resolve_voice", "_resolve_room_by_presence"):
                calls = [n for n in ast.walk(fn)
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                         and n.func.id == "_note_voice_miss"]
                out[fn.name] = bool(calls)
        return out

    def test_08_ruler_is_calibrated_both_ways(self):
        clean = (
            "def _resolve_voice(role=''):\n"
            "    return ROLE_VOICES.get(role)\n"
        )
        dirty = (
            "def _resolve_voice(role=''):\n"
            "    hit = ROLE_VOICES.get(role)\n"
            "    if hit is None:\n"
            "        _note_voice_miss('ROLE_VOICES', role, '_resolve_voice')\n"
            "    return hit\n"
        )
        got = self._sites(clean)
        self.assertEqual(got["_resolve_voice"], False, "CLEAN 合成⇒尺子该判「没留痕」")
        self.assertEqual(self._sites(dirty)["_resolve_voice"], True, "SYNTH 合成⇒尺子必须咬")

    def test_09_both_lookup_sites_route_through_the_observer(self):
        got = self._sites(TTS_PY.read_text(encoding="utf-8"))
        self.assertEqual(sorted(got), ["_resolve_room_by_presence", "_resolve_voice"])
        for name, present in got.items():
            self.assertTrue(present, "%s 的 miss 没走具名观测＝本裁未落地" % name)


class AntiRegression(unittest.TestCase):
    """裁定⛔ 做的两件事，判据必须会咬。"""

    def setUp(self):
        _reset()

    def test_10_no_case_folding(self):
        ascii_key = next(k for k in sorted(tr.ROLE_VOICES) if k.isascii() and k.islower())
        self.assertIsNone(tr._resolve_voice(role=ascii_key.upper()),
                          "折叠大小写一旦出现＝现网集体换嗓子，本档判红")
        self.assertIsNone(tr._resolve_voice(role=ascii_key.title()))

    def test_11_key_faces_untouched(self):
        self.assertEqual(set(tr.ROLE_VOICES), VOICES_KEYS, "ROLE_VOICES 键面动了⇒超出裁定")
        self.assertEqual(set(tr.ROLE_ICONS), ICONS_KEYS, "ROLE_ICONS 键面动了⇒超出裁定")
        self.assertEqual(set(tr.ROLE_TO_USER), TO_USER_KEYS, "ROLE_TO_USER 键面动了⇒超出裁定")

    def test_12_accessor_returns_a_copy(self):
        tr._VOICE_MISS["x"] = 1
        snap = tr.voice_miss_counts()
        snap["x"] = 99
        self.assertEqual(tr._VOICE_MISS["x"], 1, "外拿到的是引用＝计数可被改写，读数不可信")
        _reset()


if __name__ == "__main__":
    unittest.main(verbosity=2)
