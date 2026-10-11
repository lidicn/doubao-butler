"""批200 Q3 门：空 room 的语义「写死＋留痕」，⛔ 把它改成拒绝（DCD 20261011 七问裁定 乙）。

裁定 §3 Q3 原文口径：
 「butler/devices.py:198-210：把 :199 docstring 的『room 为 None 或 * 不过滤房间』升格为判据，
   ⛔ 不动 :207。新增空 room 时返回台数留痕。⛔ 不得把空 room 改成拒绝。」

验收对照（§4-3）：`resolve(role.output_devices, "")` 返回全量且留痕有台数
 （负控：传具体 room 台数必须变少）。

跑法：`python -B -m unittest tests.test_audit_1011_batch200_q3_empty_room_legs`（一套一进程）。
"""
from __future__ import annotations

import pathlib
import sys
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from butler.devices import Device, DeviceRegistry  # noqa: E402

DEVICES_PY = REPO_ROOT / "butler" / "devices.py"

ROOM_FILTER_LINE = 'if room and room != "*" and dev.room and dev.room != "*" and dev.room != room:'
TRACE_LINE = 'logger.info("device resolve room-unfiltered'
DOC_PHRASE = "不按房间过滤"


def _settings(data_dir: str):
    return types.SimpleNamespace(data_dir=data_dir)


def resolve_region(src: str) -> str:
    start = src.index("def resolve(")
    end = src.index("# ---- 编辑", start)
    return src[start:end]


def hits(region: str) -> list:
    out = []
    if "return []" in region:
        out.append("empty-room-becomes-refusal")
    if ROOM_FILTER_LINE not in region:
        out.append("room-filter-line-changed")
    if TRACE_LINE not in region:
        out.append("count-trace-missing")
    if DOC_PHRASE not in region:
        out.append("doc-not-promoted")
    return out


CLEAN_RESOLVE = '''def resolve(self, device_ids, room=None):
        """`room` 为 None／空串／'*' ＝不按房间过滤，返回该角色全部可用台数。"""
        unfiltered = not room or room == "*"
        out = []
        for did in device_ids or []:
            dev = self.devices.get(did)
            if room and room != "*" and dev.room and dev.room != "*" and dev.room != room:
                continue
            out.append(dev)
        if unfiltered:
            logger.info("device resolve room-unfiltered: devices=%d", len(out))
        return out
'''
SYNTH_REFUSES = CLEAN_RESOLVE.replace(
    '        unfiltered = not room or room == "*"\n',
    '        if not room or room == "*":\n            return []\n        unfiltered = False\n')
SYNTH_TOUCHES_207 = CLEAN_RESOLVE.replace(
    '            if room and room != "*" and dev.room and dev.room != "*" and dev.room != room:',
    '            if room != "*" and dev.room != room:')
SYNTH_NO_TRACE = CLEAN_RESOLVE.replace(
    '        if unfiltered:\n            logger.info("device resolve room-unfiltered: devices=%d", len(out))\n', "")
SYNTH_NO_DOC = CLEAN_RESOLVE.replace(DOC_PHRASE, "按房间过滤")


class EmptyRoomSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.reg = DeviceRegistry(_settings(str(REPO_ROOT / "data")))
        self.reg.devices = {
            "xiao_living": Device("xiao_living", "xiaomi", room="客厅"),
            "tv_study": Device("tv_study", "tv", room="书房"),
            "xiao_off": Device("xiao_off", "xiaomi", room="客厅", enabled=False),
        }
        self.ids = ["xiao_living", "tv_study", "xiao_off", "ghost"]

    def test_01_empty_room_returns_all(self):
        got = self.reg.resolve(self.ids, "")
        self.assertEqual([d.id for d in got], ["xiao_living", "tv_study"],
                         "空 room 必须返回全量（裁点：⛔ 改成拒绝）")

    def test_02_none_room_returns_all(self):
        self.assertEqual(len(self.reg.resolve(self.ids, None)), 2)

    def test_03_star_room_returns_all(self):
        self.assertEqual(len(self.reg.resolve(self.ids, "*")), 2)

    def test_04_specific_room_negative_control(self):
        # 验收的负控：传具体房间⇒台数必须变少（否则那枚房间过滤是摆设）
        self.assertEqual([d.id for d in self.reg.resolve(self.ids, "客厅")], ["xiao_living"])
        self.assertEqual([d.id for d in self.reg.resolve(self.ids, "书房")], ["tv_study"])

    def test_05_empty_room_leaves_count_trace(self):
        with self.assertLogs("butler.devices", level="INFO") as cm:
            self.reg.resolve(self.ids, "")
        line = [x for x in cm.output if "room-unfiltered" in x]
        self.assertEqual(len(line), 1, cm.output)
        self.assertIn("devices=2", line[0], line[0])

    def test_06_specific_room_emits_no_unfiltered_trace(self):
        # ⛔ 让留痕在带房间的档也响⇒读数会把「按房间过滤过」说成「没按房间过滤」
        with self.assertLogs("butler.devices", level="INFO") as cm:
            self.reg.resolve(self.ids, "客厅")
        self.assertEqual([x for x in cm.output if "room-unfiltered" in x], [], cm.output)

    def test_07_disabled_and_missing_leg_untouched(self):
        # 房间语义收编⛔ 顺带动设备可用性那两条腿（批71 D3 那族在册）
        got = self.reg.resolve(self.ids, "")
        self.assertNotIn("xiao_off", [d.id for d in got])
        self.assertNotIn("ghost", [d.id for d in got])


class ShapeRulerTests(unittest.TestCase):
    def test_08_ruler_calibrated_both_ways(self):
        self.assertEqual(hits(CLEAN_RESOLVE), [], hits(CLEAN_RESOLVE))
        self.assertEqual(hits(SYNTH_REFUSES), ["empty-room-becomes-refusal"], hits(SYNTH_REFUSES))
        self.assertEqual(hits(SYNTH_NO_TRACE), ["count-trace-missing"], hits(SYNTH_NO_TRACE))
        self.assertEqual(hits(SYNTH_NO_DOC), ["doc-not-promoted"], hits(SYNTH_NO_DOC))
        # SYNTH_TOUCHES_207 同时动了房间行、把「不过滤」那句也抹了⇒至少咬住改行那一格
        self.assertIn("room-filter-line-changed", hits(SYNTH_TOUCHES_207), hits(SYNTH_TOUCHES_207))

    def test_09_real_source_on_judgment(self):
        region = resolve_region(DEVICES_PY.read_text(encoding="utf-8"))
        self.assertEqual(hits(region), [], hits(region))
        self.assertRegex(region, r"unfiltered = not room or room == \"\*\"",
                         "判据必须现读得到（None／空串／'*' 三档同值）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
