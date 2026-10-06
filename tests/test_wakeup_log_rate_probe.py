# -*- coding: utf-8 -*-
"""WO-ME-225 J4 尺子（`workorders/tools/wakeup_log_rate_probe.py`）的自带测试。

只碰两个新文件：本文件 + 那把尺子。夹具库落在 `tempfile` 里（**不碰仓内 `data/`，不碰现网 `/app/data`**）。
形状按 R11（`workorders/判据勘误-20260921.md:27`）要求：纯 `def test_*` + `unittest.TestCase`，
**不用 `asyncio.run()`**（台账 新53：那会把同进程里靠后的既有测试刷成 ERROR）。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROBE_PATH = REPO / "workorders" / "tools" / "wakeup_log_rate_probe.py"
T0 = 1_700_000_000.0          # 夹具基准时刻（合成值，与现网无关）
DAY = 86400.0

# 与 butler/store/db.py:63-72 的 DDL 同形（抄结构，不 import butler —— 尺子在容器里以 stdin 跑，不能依赖项目模块）
DDL = """
CREATE TABLE wakeup_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    trigger   TEXT NOT NULL,
    room      TEXT,
    member    TEXT,
    decision  TEXT NOT NULL,
    reason    TEXT,
    cost_ms   INTEGER
);
"""


def load_probe():
    spec = importlib.util.spec_from_file_location("wakeup_log_rate_probe_under_test", PROBE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_db(path: str, rows) -> None:
    c = sqlite3.connect(path)
    c.execute(DDL)
    c.executemany(
        "INSERT INTO wakeup_log (ts, trigger, room, member, decision, reason, cost_ms) VALUES (?,?,?,?,?,?,?)",
        [(ts, trig, "客厅", mem, dec, dec, 1) for ts, trig, mem, dec in rows])
    c.commit()
    c.close()


def run_probe(mod, argv) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(argv)
    return rc, buf.getvalue()


def stream(seconds: float, step: float, member: str, decision: str, trigger: str = "face", start: float = T0):
    """从 start 起每 step 秒一条，连续 seconds 秒（合成事件流，非现网数据）。"""
    return [(start + i * step, trigger, member, decision) for i in range(int(seconds / step))]


def reference_kept(rows, window: float, exempt=()) -> int:
    """门的独立实现（只当对照，不与尺子共享代码）。"""
    last: dict[tuple, float] = {}
    kept = 0
    for ts, _trig, mem, dec in sorted(rows, key=lambda r: (r[2], r[3], r[0])):
        if dec in exempt:
            kept += 1
            continue
        prev = last.get((mem, dec))
        if prev is None or ts - prev >= window:
            last[(mem, dec)] = ts
            kept += 1
    return kept


def kept_default_key(out: str) -> int:
    return int(re.search(r"未被抑制\*\*的行数 = (\d+)", out).group(1))


class WakeupLogRateProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wlr_probe_")
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "butler.db")
        self.mod = load_probe()
        self.assertTrue(PROBE_PATH.is_file(), f"尺子不在盘上：{PROBE_PATH}")

    # ── 1. 三个数对得上盘上 SQL 的独立读数，且洪水必然判红 ──
    def test_probe_reports_total_kept_and_per_day(self):
        rows = stream(3600.0, 0.467, "老豆", "pass") + stream(DAY, 30.0, "小甜菜", "cooldown")
        make_db(self.db, rows)
        c = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        sql_total = c.execute("SELECT count(*) FROM wakeup_log").fetchone()[0]
        c.close()

        rc, out = run_probe(self.mod, ["--db", self.db, "--hours", "24", "--window", "60"])
        expected = reference_kept(rows, 60.0)
        self.assertEqual(sql_total, len(rows))
        self.assertIn(f"raw = {sql_total}", out)
        self.assertEqual(kept_default_key(out), expected)
        self.assertAlmostEqual(float(re.search(r"折算行数/天 = ([\d.]+)", out).group(1)), expected, places=1)
        self.assertEqual(rc, 1)                      # 尺子必须能判红：0.467s 的洪水过不了 500/天
        self.assertIn("VERDICT FAIL rc=1", out)

    # ── 2. 只读：跑完字节不变；连接里任何写都被拒 ──
    def test_probe_opens_database_read_only(self):
        make_db(self.db, stream(60.0, 10.0, "老豆", "pass"))
        before = Path(self.db).read_bytes()
        run_probe(self.mod, ["--db", self.db, "--hours", "1"])
        self.assertEqual(before, Path(self.db).read_bytes(), "跑过尺子后库文件字节变了 = 存在写路径")
        conn = self.mod.open_ro(self.db)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM wakeup_log")
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE should_not_exist (x INTEGER)")
        conn.close()

    # ── 3. R1 的 N > 9.01 是**独立**一道门：速率达标时 N=9 仍判红，N=9.02 才放行 ──
    def test_n_gate_fails_independently_of_rate(self):
        rows = stream(DAY, 300.0, "老豆", "cooldown")          # 288 行/天 < 500 ⇒ 速率那一关本来就过
        make_db(self.db, rows)
        rc9, out9 = run_probe(self.mod, ["--db", self.db, "--hours", "24", "--window", "9"])
        self.assertEqual(rc9, 1)
        self.assertIn("未过 R1 门限 N > 9.01", out9)
        self.assertNotIn("行/天 > 门槛", out9)                  # 红只因为门宽，不掺速率原因

        rc, out = run_probe(self.mod, ["--db", self.db, "--hours", "24", "--window", "9.02"])
        self.assertEqual(rc, 0)
        self.assertIn("N > 9.01 秒 ⇒ 本量取 N = 9.02 ⇒ 满足", out)
        self.assertIn("VERDICT PASS rc=0", out)

    # ── 4. 采纳后的 J1 口径：spoken 永不抑制、pass 吃抑制，J1 只数 decision!='spoken' ──
    def test_spoken_never_suppressed_while_pass_is(self):
        spoken = [(T0 + DAY / 2 + i * 5.0, "voice", "老豆", "spoken") for i in range(3)]
        flood = stream(3600.0, 0.467, "老豆", "pass")
        make_db(self.db, spoken + flood)
        rc, out = run_probe(self.mod, ["--db", self.db, "--since", str(T0), "--until", str(T0 + DAY),
                                       "--window", "60"])
        flood_kept = reference_kept(flood, 60.0)
        self.assertEqual(kept_default_key(out), flood_kept + 3)
        self.assertIn("spoken=3", out)
        self.assertIn(f"decision!='spoken' 的未被抑制行数 = {flood_kept}", out)
        self.assertEqual(rc, 0)                      # 一天 61 行 ≤ 500
        self.assertIn("VERDICT PASS rc=0", out)

    # ── 5. §三 原文（pass 不抑制）只作并列对照，且必然严于默认口径 ──
    def test_legacy_pass_exempt_printed_as_second_reading(self):
        rows = stream(3600.0, 0.467, "老豆", "pass")
        make_db(self.db, rows)
        _rc, out = run_probe(self.mod, ["--db", self.db, "--hours", "1", "--window", "60",
                                        "--legacy-pass-exempt"])
        self.assertIn("§三 原文对照", out)
        legacy = int(re.search(r"pass 永不抑制口径下未被抑制行数 = (\d+)", out).group(1))
        self.assertGreater(legacy, kept_default_key(out))
        self.assertEqual(legacy, len(rows))

    # ── 6. 量不到一律 rc=2，且不许出现 PASS ──
    def test_missing_database_exits_2_without_claiming_pass(self):
        rc, out = run_probe(self.mod, ["--db", os.path.join(self.tmp.name, "nope.db")])
        self.assertEqual(rc, 2)
        self.assertIn("NOT-MEASURED", out)
        self.assertNotIn("VERDICT PASS", out)

    def test_empty_table_exits_2_not_traceback(self):
        make_db(self.db, [])
        rc, out = run_probe(self.mod, ["--db", self.db, "--hours", "24"])
        self.assertEqual(rc, 2)
        self.assertIn("空表", out)

    def test_reversed_window_exits_2(self):
        make_db(self.db, stream(60.0, 10.0, "老豆", "pass"))
        rc, out = run_probe(self.mod, ["--db", self.db, "--since", str(T0 + DAY), "--until", str(T0)])
        self.assertEqual(rc, 2)
        self.assertIn("窗口反向", out)

    def test_bad_grouping_key_exits_2(self):
        rc, out = run_probe(self.mod, ["--db", self.db, "--key", "cost_ms"])
        self.assertEqual(rc, 2)
        self.assertIn("非 wakeup_log 列", out)

    # ── 7. 分组键口径：R1 字面 (member,decision) 与实现 (trigger,member,decision) 在 distinct_trigger>1 时不等价 ──
    def test_grouping_key_changes_the_answer(self):
        rows = stream(600.0, 10.0, "老豆", "pass") + stream(600.0, 10.0, "老豆", "pass", trigger="voice")
        make_db(self.db, rows)
        _rc, out_md = run_probe(self.mod, ["--db", self.db, "--hours", "1", "--window", "60"])
        _rc, out_tmd = run_probe(self.mod, ["--db", self.db, "--hours", "1", "--window", "60",
                                            "--key", "trigger,member,decision"])
        self.assertIn("distinct_trigger = 2", out_md)
        self.assertIn("组数 = 1）", out_md)           # (member,decision) 只看出一组 ⇒ 折叠得更狠
        self.assertIn("组数 = 2）", out_tmd)
        self.assertEqual(kept_default_key(out_md), 10)
        self.assertEqual(kept_default_key(out_tmd), 20)

    # ── 8. R1 的上界用实测 M 代入，不假设 M=2 ──
    def test_r1_bound_uses_measured_distinct_member(self):
        rows = (stream(3600.0, 10.0, "老豆", "cooldown")
                + stream(3600.0, 10.0, "小甜菜", "cooldown")
                + stream(3600.0, 10.0, "顾安恒", "cooldown"))
        make_db(self.db, rows)
        _rc, out = run_probe(self.mod, ["--db", self.db, "--hours", "1", "--window", "60"])
        self.assertIn("M = 3", out)
        self.assertIn("5×3×4502/60 = 1125.5 行/天", out)
        self.assertIn("需 N > 135.06 秒", out)         # 5×3×4502/500 ⇒ 三人要达标得把门开到 135 秒以上


if __name__ == "__main__":
    unittest.main()
