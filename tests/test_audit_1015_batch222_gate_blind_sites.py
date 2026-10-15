# -*- coding: utf-8 -*-
"""批222 第一档：批221 全仓形状闸的两种盲区，逐枚钉行为。

盲区由 `workorders/readings/1015/b464_cast_denominator.txt` 现读定性：
批221 那把 AST 闸只认「`.get()` 的接收者根名 ∈ request|body|d|payload」，所以
 - `float(it.get("confidence") or 0.5)`（接收者 `it`）
 - `int(value)`（正则分组／标签值，裸名）
两形在闸面上是 0 命中，码面却一直在。本档先把行为钉红，再修站点。

RED 判据（改前）：
 - DB109：一枚坏 `confidence` 让 `probe()` 抛穿 `asyncio.gather` ⇒ **整个 presence 通道**
   被外层 `except Exception` 收成一行 debug，结果与「全屋没找到这个人」逐字同形。
 - DB126：`【温度：26.5】` 让 `parse_device_command_from_reply` 抛 `ValueError` ⇒
   同一条回复里**其余设备的命令全部不执行**（解析在第一条坏值处断掉）。
"""
import asyncio
import unittest

from butler.api.doubao_webhook import parse_device_command_from_reply
from butler.locator import MemberLocator


class _FakeMemory:
    """只填 `presence` 一腿；每个房间回一批固定条目。"""

    def __init__(self, per_room):
        self._per_room = per_room

    async def presence(self, room, minutes=30):
        return self._per_room.get(room, [])


class _FakeSettings:
    def nickname_of(self, member):
        return ""


def _locator(per_room):
    return MemberLocator(_FakeSettings(), _FakeMemory(per_room))


class GarbageConfidenceTest(unittest.TestCase):

    def test_bad_confidence_keeps_the_presence_hit(self):
        """坏 confidence 只丢那枚数字，⛔ 废掉整条命中。"""
        located = asyncio.run(_locator({
            "客厅": [{"name": "顾安恒", "last_seen": "2026-10-15T05:00:00",
                        "confidence": "很高"}],
        })._probe_presence(["客厅"], {"顾安恒"}, 30))
        self.assertIsNotNone(located, "一枚坏数字把 presence 通道打成『查无此人』")
        self.assertEqual(located["room"], "客厅")
        self.assertAlmostEqual(located["confidence"], 0.5, places=6)

    def test_explicit_zero_confidence_is_not_read_as_the_default(self):
        """`0` 是有效读数，⛔ 被 `or 0.5` 换成默认值（报告 §三③ 同族）。"""
        located = asyncio.run(_locator({
            "书房": [{"name": "顾安恒", "last_seen": "2026-10-15T05:00:00",
                       "confidence": 0}],
        })._probe_presence(["书房"], {"顾安恒"}, 30))
        self.assertEqual(located["confidence"], 0.0)

    def test_one_bad_room_does_not_silence_the_others(self):
        """一间房的载荷烂掉，⛔ 连带另一间的正常命中一起吞。"""
        located = asyncio.run(_locator({
            "客厅": [{"name": "顾安恒", "last_seen": "2026-10-15T05:00:00",
                       "confidence": "很高"}],
            "书房": [{"name": "顾安恒", "last_seen": "2026-10-15T06:00:00",
                       "confidence": 0.9}],
        })._probe_presence(["客厅", "书房"], {"顾安恒"}, 30))
        self.assertEqual(located["room"], "书房")


class GarbageTemperatureTest(unittest.TestCase):

    def test_bad_temperature_does_not_abort_the_rest_of_the_reply(self):
        reply = ("【设备：书房空调】【操作：打开】【温度：26.5】"
                 "【设备：客厅电视】【操作：换台】【频道：深圳卫视】")
        cmds = parse_device_command_from_reply(reply)
        self.assertEqual([c["device"] for c in cmds], ["客厅电视"],
                         "一枚坏温度把同一条回复里后面的设备也一起废了")

    def test_valid_temperature_still_lands_as_a_number(self):
        cmds = parse_device_command_from_reply(
            "【设备：书房空调】【操作：打开】【温度：26】")
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0]["temperature"], 26)

    def test_unparseable_block_is_dropped_whole(self):
        """丢掉整块设备命令：⛔ 只把温度省了照常「打开」（用户要的是 26℃）。"""
        cmds = parse_device_command_from_reply(
            "【设备：书房空调】【操作：打开】【温度：二十六】")
        self.assertEqual(cmds, [])


if __name__ == "__main__":
    unittest.main()
