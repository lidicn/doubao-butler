"""第二十轮（最终轮）D22：`Settings._load_persona` 把「读不出来」当成「没有」。

与 D1（`api/config_routes._load_cfg`）读同一个 `DATA_DIR/config.json`：现树 `butler/config.py:363`
的 `except Exception: return` 无日志，且顶层不是对象时 `raw.get(...)` 直接把异常抛穿出启动路径。
"""
import json
import tempfile
import unittest
from pathlib import Path

from butler.config import PersonaConfig, Settings


class TestBatch77PersonaCfg(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.cfg = self.data_dir / "config.json"

    def _settings(self) -> Settings:
        return Settings(data_dir=str(self.data_dir))

    def test_corrupt_json_logs_error_and_keeps_defaults(self):
        self.cfg.write_text('{"system": "beta", ', encoding="utf-8")
        s = self._settings()
        with self.assertLogs("butler.config", level="ERROR") as cm:
            s._load_persona()
        self.assertIn("config.json", " ".join(cm.output))
        self.assertEqual(s.persona.system, PersonaConfig().system)

    def test_top_level_array_logs_error_instead_of_crashing(self):
        self.cfg.write_text(json.dumps([1, 2]), encoding="utf-8")
        s = self._settings()
        with self.assertLogs("butler.config", level="ERROR") as cm:
            s._load_persona()
        self.assertIn("config.json", " ".join(cm.output))
        self.assertEqual(s.persona.members, [])

    def test_undecodable_bytes_logs_error(self):
        self.cfg.write_bytes(b'{"system": "\xff\xfe\xfd"}')
        s = self._settings()
        with self.assertLogs("butler.config", level="ERROR"):
            s._load_persona()

    def test_missing_config_is_not_reported_as_error(self):
        s = self._settings()
        with self.assertNoLogs("butler.config", level="ERROR"):
            s._load_persona()
        self.assertEqual(s.persona.system, PersonaConfig().system)

    def test_valid_config_still_overrides_and_stays_quiet(self):
        self.cfg.write_text(
            json.dumps(
                {
                    "system": "定制人格",
                    "greeting_template": "{nickname}回家",
                    "members": [{"id": "m1", "name": "小明", "nickname": "明明"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        s = self._settings()
        with self.assertNoLogs("butler.config", level="ERROR"):
            s._load_persona()
        self.assertEqual(s.persona.system, "定制人格")
        self.assertEqual(s.persona.greeting_template, "{nickname}回家")
        self.assertEqual(len(s.persona.members), 1)
        self.assertEqual(s.persona.members[0].nickname, "明明")

    def test_each_reload_leaves_its_own_trace(self):
        self.cfg.write_text("{oops", encoding="utf-8")
        s = self._settings()
        with self.assertLogs("butler.config", level="ERROR") as cm:
            s._load_persona()
            s._load_persona()
        self.assertEqual(len(cm.output), 2)


if __name__ == "__main__":
    unittest.main()
