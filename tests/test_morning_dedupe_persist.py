"""P1 晨起播报去重落盘（2026-11-06）：内存变量 → `DATA_DIR/morning_state.json`。

病灶（现状报告 §五）：`morning/routine.py` 的去重是模块级 `_morning_reported_date`，
进程重启清零 ⇒ 同一天重启一次就多播一次晨起模板。

判据：
- 写读往返（落盘是真 JSON）；
- 新实例/新进程读得到（只靠盘上状态）；
- 盘上没有 / 读坏 → 按「没播过」处理并留痕（本仓判例：读不出来 ≠ 没有）；
- 已记今天 ⇒ `check_and_broadcast()` 在**读时间之后、碰任何 DB/HA 之前**返回 False。
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from butler.morning import routine


class _FakeSettings:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir


class DedupePersist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self._p = mock.patch.object(routine, "get_settings", lambda: _FakeSettings(str(self.data_dir)))
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_mark_then_read_roundtrip(self):
        routine._mark_reported("2026-11-06")
        self.assertEqual(routine._reported_date(), "2026-11-06")

    def test_state_file_is_real_json_on_disk(self):
        routine._mark_reported("2026-11-06")
        path = self.data_dir / "morning_state.json"
        self.assertTrue(path.exists(), "去重状态没落盘＝重启仍会重播")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["last_reported_date"], "2026-11-06")

    def test_missing_state_means_not_reported(self):
        self.assertEqual(routine._reported_date(), "")

    def test_unreadable_state_is_treated_as_not_reported_and_traced(self):
        (self.data_dir / "morning_state.json").write_text("{broken", encoding="utf-8")
        with self.assertLogs("butler.morning", level="WARNING") as cm:
            self.assertEqual(routine._reported_date(), "")
        self.assertIn("unreadable", " ".join(cm.output))

    def test_new_reader_sees_previous_process_state(self):
        """模拟重启：写入者与读取者不是同一个对象/生命周期，只共享盘上文件。"""
        routine._mark_reported("2026-11-06")
        # 清掉任何可能的模块级缓存，只留盘上文件
        self.assertFalse(hasattr(routine, "_morning_reported_date"),
                         "还有内存变量残留＝重启磁盘状态仍可能被绕过")
        self.assertEqual(routine._reported_date(), "2026-11-06")

    def test_write_failure_degrades_without_raising(self):
        orig = routine._state_path
        routine._state_path = lambda: Path("Z:/nope/morning_state.json")
        try:
            routine._mark_reported("2026-11-06")  # 不抛即过
        finally:
            routine._state_path = orig


class SkipAlreadyReported(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self._p = mock.patch.object(routine, "get_settings", lambda: _FakeSettings(str(self.data_dir)))
        self._p.start()
        self.addCleanup(self._p.stop)

    async def _check_at(self, y, m, d, hh):
        epoch = time.mktime((y, m, d, hh, 0, 0, 0, 0, -1))
        with mock.patch.object(routine.time, "time", lambda: epoch), \
             mock.patch.object(routine.time, "localtime",
                               lambda ts=None: time.struct_time((y, m, d, hh, 0, 0, 0, 0, -1))):
            return await routine.MorningRoutine().check_and_broadcast()

    async def test_already_reported_today_is_skipped_before_any_db_call(self):
        routine._mark_reported("2026-11-06")
        # 若没在读时间后立即返回，下面的 ts.get_current_mode 会因为没初始化而抛错
        with mock.patch.object(routine.ts, "get_current_mode",
                               side_effect=AssertionError("已播报却继续查模式＝去重没生效")):
            self.assertFalse(await self._check_at(2026, 11, 6, 7))

    async def test_other_day_is_not_skipped_by_state(self):
        """负控：盘上记的是昨天，今天必须继续往下走（这里用 get_current_mode 抛错当探针）。"""
        routine._mark_reported("2026-11-05")
        with mock.patch.object(routine.ts, "get_current_mode",
                               side_effect=AssertionError("走到 DB 了＝这条负控成立")):
            with self.assertRaises(AssertionError):
                await self._check_at(2026, 11, 6, 7)


if __name__ == "__main__":
    unittest.main()
