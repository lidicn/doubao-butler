import unittest

from butler.bus.topics import parse_event, parse_face, parse_tv_status, PUB_TV_TTS


class TestTopics(unittest.TestCase):
    def test_parse_face(self):
        ev = parse_face("tv/livingroom/face", {"name": "Kevin", "confidence": 0.92, "ts": 123.0})
        self.assertEqual(ev.kind, "face")
        self.assertEqual(ev.member, "Kevin")
        self.assertEqual(ev.confidence, 0.92)
        self.assertEqual(ev.room, "客厅")

    def test_parse_face_stranger(self):
        ev = parse_face("tv/livingroom/face", {"name": "", "confidence": 0.3})
        self.assertEqual(ev.member, "stranger")

    def test_parse_tv_status(self):
        ev = parse_tv_status("tv/livingroom/status", {"state": "online"})
        self.assertEqual(ev.kind, "tv_status")
        self.assertEqual(ev.member, "online")

    def test_parse_event(self):
        ev = parse_event("butler/event/voice", {"member": "lidicn", "text": "你好"})
        self.assertEqual(ev.kind, "voice")
        self.assertEqual(ev.member, "lidicn")

    def test_pub_tv_tts_constant(self):
        self.assertEqual(PUB_TV_TTS, "tv/livingroom/cmd/tts")


if __name__ == "__main__":
    unittest.main()
