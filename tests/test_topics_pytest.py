"""
测试 topics 模块

批182 收编说明：本档原先是 6 枚**模块级** `test_*` 函数且不 `import pytest`，
仓库判定跑的是 `python -m unittest`——那条路把模块级函数静默收下、一条不跑、
不报错也不判绿（见 [[test-collector-async-gate]]）。改成 `unittest.TestCase`
后第一次真跑。断言逐条照抄原形，⛔ 放宽任何一条。
"""
import unittest

from butler.bus.topics import (
    parse_event,
    parse_face,
    parse_tv_status,
    PUB_TV_TTS,
    SUB_TV_FACE,
)


class ParseFaceTests(unittest.TestCase):
    def test_parse_face(self):
        ev = parse_face("tv/livingroom/face", {
            "name": "Kevin",
            "confidence": 0.92,
            "ts": 123.0,
        })
        self.assertEqual(ev.kind, "face")
        self.assertEqual(ev.member, "Kevin")
        self.assertEqual(ev.confidence, 0.92)
        self.assertEqual(ev.room, "客厅")

    def test_parse_face_stranger(self):
        ev = parse_face("tv/livingroom/face", {
            "name": "",
            "confidence": 0.3,
        })
        self.assertEqual(ev.member, "stranger")


class ParseTvStatusTests(unittest.TestCase):
    def test_parse_tv_status(self):
        ev = parse_tv_status("tv/livingroom/status", {"state": "online"})
        self.assertEqual(ev.kind, "tv_status")
        self.assertEqual(ev.member, "online")


class ParseEventTests(unittest.TestCase):
    def test_parse_event(self):
        ev = parse_event("butler/event/voice", {
            "member": "lidicn",
            "text": "你好",
        })
        self.assertEqual(ev.kind, "voice")
        self.assertEqual(ev.member, "lidicn")


class TopicConstantTests(unittest.TestCase):
    """MQTT 串是订阅匹配的真源，改一个字就等于消息进不了队列。"""

    def test_pub_tv_tts_constant(self):
        self.assertEqual(PUB_TV_TTS, "tv/livingroom/cmd/tts")

    def test_sub_tv_face_constant(self):
        self.assertEqual(SUB_TV_FACE, "tv/livingroom/face")


if __name__ == "__main__":
    unittest.main()
