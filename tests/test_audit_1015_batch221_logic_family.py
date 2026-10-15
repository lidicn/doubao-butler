# -*- coding: utf-8 -*-
"""批221 第二档：纯逻辑族（DB93/99/103/109/112/120/123/135/137/139 一族）的失败用例。

RED 判据＝修复前这些调用要么抛未捕获的 ValueError/TypeError，要么给出与调用方本意相反的值。
"""
import re
import tempfile
import unittest
from pathlib import Path

from butler.bus.topics import parse_experiment, parse_face
from butler.decision.config import DecisionConfig
from butler.param import ParamError
from butler.roles.store import Role
from butler.tts.queue import TTSQueueConfig, _parse_hhmm


class TestParseHHMM(unittest.TestCase):
    """DB93：`str(value).strip().split(":")` 的三重崩溃面＝无冒号、多冒号、非数字。"""

    def test_valid_form(self):
        self.assertEqual(_parse_hhmm("06:50"), 6 * 60 + 50)

    def test_no_colon_does_not_unpack_crash(self):
        with self.assertRaises(ValueError):
            _parse_hhmm("6")

    def test_non_numeric_raises_param_error_not_bare_valueerror(self):
        with self.assertRaises(ParamError):
            _parse_hhmm("ab:cd")

    def test_out_of_range_hour_rejected(self):
        with self.assertRaises(ParamError):
            _parse_hhmm("25:00")

    def test_quiet_window_with_broken_config_is_visible_not_fatal(self):
        """静默时段配坏 ⇒ 全屋永久静音或整条 TTS 崩，两种都不是调用方要的。"""
        cfg = TTSQueueConfig(quiet_start="not-a-time", quiet_end="23:00")
        self.assertFalse(cfg.is_quiet(12 * 60))


class TestDecisionConfigNumbers(unittest.TestCase):
    """DB99：五个配置属性裸 int()/float()，一枚坏值让调度器注册块整块中断。"""

    def _cfg(self, mapping):
        with tempfile.TemporaryDirectory() as tmp:
            d = DecisionConfig(tmp)
        d.cfg = dict(mapping)
        return d

    def test_garbage_values_fall_back_to_documented_defaults(self):
        d = self._cfg({"interval_minutes": "abc", "night_start": None, "night_end": "",
                       "confidence_threshold": "high", "cooldown_minutes": "10min"})
        self.assertEqual(d.interval_minutes, 10)
        self.assertEqual(d.night_start, 23)
        self.assertEqual(d.night_end, 7)
        self.assertAlmostEqual(d.confidence_threshold, 0.6)
        self.assertEqual(d.cooldown_minutes, 30)

    def test_legal_zero_interval_is_clamped_to_one(self):
        self.assertEqual(self._cfg({"interval_minutes": 0}).interval_minutes, 1)

    def test_bool_config_reads_string_false_as_false(self):
        self.assertFalse(self._cfg({"enabled": "false"}).enabled)


class TestTimeRangeOverlap(unittest.TestCase):
    """DB135：同一函数里两处方向相反的错——跨午夜漏报、坏格式误报。"""

    def _det(self):
        from butler.skills.conflict import ConflictDetector
        return ConflictDetector.__new__(ConflictDetector)  # 纯判定，不需要装配

    def test_plain_overlap(self):
        self.assertTrue(self._det()._time_ranges_overlap("09:00-12:00", "11:00-13:00"))

    def test_plain_disjoint(self):
        self.assertFalse(self._det()._time_ranges_overlap("09:00-10:00", "11:00-13:00"))

    def test_cross_midnight_overlap_is_reported(self):
        """23:00-01:00 与 00:30-02:00 确实重叠 ⇒ 漏报就是本条缺陷。"""
        self.assertTrue(self._det()._time_ranges_overlap("23:00-01:00", "00:30-02:00"))

    def test_cross_midnight_disjoint_is_not_flagged(self):
        self.assertFalse(self._det()._time_ranges_overlap("23:00-01:00", "02:00-03:00"))

    def test_malformed_range_is_not_flagged_as_conflict(self):
        """坏格式 ⇒ 误报「冲突」：解析不出来该判不重叠并留痕，⛔ 当成万能冲突。"""
        self.assertFalse(self._det()._time_ranges_overlap("abc", "09:00-10:00"))


class TestRoleFromDictEnabled(unittest.TestCase):
    """DB137／DB120／DB123 同族：`bool(d.get("enabled", True))` 对显式 null 得到 False。"""

    def test_explicit_null_keeps_default_true(self):
        role = Role.from_dict({"id": "jarvis", "enabled": None})
        self.assertTrue(role.enabled)

    def test_absent_key_keeps_default_true(self):
        self.assertTrue(Role.from_dict({"id": "jarvis"}).enabled)

    def test_string_false_disables(self):
        self.assertFalse(Role.from_dict({"id": "jarvis", "enabled": "false"}).enabled)

    def test_explicit_false_still_disables(self):
        self.assertFalse(Role.from_dict({"id": "jarvis", "enabled": False}).enabled)


class TestTopicParsers(unittest.TestCase):
    """DB101／DB103／DB141：MQTT 载荷里 confidence 非数字、`timestamp` 与 `ts` 键名分叉。"""

    def test_face_bad_confidence_does_not_raise(self):
        ev = parse_face("home/face", {"name": "Kevin", "confidence": "high"})
        self.assertEqual(ev.confidence, 0.0)

    def test_face_numeric_confidence_passthrough(self):
        ev = parse_face("home/face", {"name": "Kevin", "confidence": 0.87})
        self.assertAlmostEqual(ev.confidence, 0.87)

    def test_face_bad_ts_does_not_raise(self):
        ev = parse_face("home/face", {"name": "Kevin", "ts": "yesterday"})
        self.assertGreater(ev.ts, 0)

    def test_experiment_accepts_both_ts_key_spellings(self):
        """DB103：同文件其余三个 parse 都读 `ts`，只有这一支读 `timestamp`。"""
        with_ts = parse_experiment("exp/1", {"exp_type": "t", "ts": 1700000000})
        with_timestamp = parse_experiment("exp/1", {"exp_type": "t", "timestamp": 1700000000})
        self.assertEqual(with_ts.ts, 1700000000)
        self.assertEqual(with_timestamp.ts, 1700000000)

    def test_experiment_bad_ts_falls_back_not_crash(self):
        ev = parse_experiment("exp/1", {"exp_type": "t", "ts": "abc"})
        self.assertGreater(ev.ts, 0)


class TestStaticShapeGate(unittest.TestCase):
    """F05「同族只修一半」的护栏：被点名的站点⛔ 再出现裸 int()/float()/bool() 形态。

    形状尺的口径：`int(request.` 这类才算裸解析；`to_int(request.`／`to_float(payload.get(`
    是尺子自己的调用点（`(?<![\\w.])` 这一道后视就是为它留的）。两条腿共用 `_bare()` 本体，
    ⛔ 自检另开一份复制品。
    """

    FILES = [
        "butler/api/memory_routes.py", "butler/api/command_routes.py",
        "butler/api/notify_routes.py", "butler/api/dialog_routes.py",
        "butler/api/skill_routes.py", "butler/api/pwa_chat_routes.py",
        "butler/api/agent_routes.py", "butler/api/bark_routes.py",
        "butler/api/trigger_routes.py", "butler/ha_tools/api.py",
        "butler/triggers/health_api.py", "butler/timeseries/api.py",
        "butler/proactive/api.py", "butler/presence/api.py",
        "butler/bus/topics.py", "butler/roles/store.py",
    ]

    _SOURCES = ("request", "body.get(", "d.get(", "payload.get(")
    _CASTS = ("int", "float", "bool")
    _BARE = re.compile(r"(?<![\w.])(" + "|".join(_CASTS) + r")\((" +
                       "|".join(re.escape(s) for s in _SOURCES) + r")")

    def _bare(self, line: str) -> bool:
        return bool(self._BARE.search(line))

    def test_no_bare_param_parse_left_in_named_files(self):
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for rel in self.FILES:
            text = (root / rel).read_text(encoding="utf-8")
            for no, line in enumerate(text.split("\n"), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"'):
                    continue
                if self._bare(line):
                    offenders.append("%s:%d %s" % (rel, no, stripped[:80]))
        self.assertEqual(offenders, [], "裸解析仍在场：\n" + "\n".join(offenders))

    def test_gate_bites_the_bare_form_and_spares_the_ruler(self):
        """负控＋放行控：同一把 `_bare()`，坏形制必须咬、尺子调用点必须放。"""
        bites = [
            'limit = int(request.query_params.get("limit", "5"))',
            '        confidence = float(payload.get("confidence") or 0.0),',
            '    is_primary=bool(d.get("is_primary", False)),',
        ]
        spared = [
            '    limit = query_num(request, "limit", default=5, minimum=0)',
            '        return to_float(payload.get(key), key, default=default)',
            '            return to_bool(self.cfg.get(name), name, default=default)',
        ]
        for probe in bites:
            self.assertTrue(self._bare(probe), "负控未咬：%s" % probe)
        for probe in spared:
            self.assertFalse(self._bare(probe), "放行控误咬（尺子调用点被当成裸解析）：%s" % probe)


if __name__ == "__main__":
    unittest.main()
