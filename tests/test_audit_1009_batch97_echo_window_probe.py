#!/usr/bin/env python3
"""批97 O-0b 验收尺：scripts/echo_window_probe.py 的判据腿逐枚证明会咬。

路线图 v2.5 那一格的出口条件是「正对照能报 FAIL＋真窗出三态之一＋数据空洞点名」，
这三件事全靠这把尺自己的形状。尺子坏了产出的是一份「这窗没回声」的假绿，所以逐腿钉：
分组腿（ts 是 REAL，必须 CAST 成整数天号）、活体门（表停了 0 命中不可当证据）、
按窗过滤、WAL 侧文件同拷、只读物跑完不留件、死尺自报、前缀值不落文书。

跑法：python3 -B -m unittest tests.test_audit_1009_batch97_echo_window_probe
变异台用 ECHO_PROBE_PATH 指向改过形状的副本（⛔ 改生产件）。
"""
from __future__ import annotations

import argparse
import calendar
import contextlib
import importlib.util
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE_PATH = os.environ.get("ECHO_PROBE_PATH") or os.path.join(
    REPO, "scripts", "echo_window_probe.py")

MARKER = "ZZECHOMARKER"          # 假前缀，⛔ 与真 SELF_PUSH_PREFIX 同值
DAY_ROW = re.compile(r"^  (\d{4}-\d{2}-\d{2})  (\d+)$", re.M)


def load_probe():
    spec = importlib.util.spec_from_file_location("ewp_under_test", PROBE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def day_epoch(date_str: str) -> int:
    return calendar.timegm(time.strptime(date_str + "T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))


def iso_z(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def day_rows(out: str):
    return [(d, int(n)) for d, n in DAY_ROW.findall(out)]


class ProbeRunLegs(unittest.TestCase):
    """run() 面：内存库 + 显式 now，量它打印什么、返回哪个 rc。"""

    def setUp(self):
        self.mod = load_probe()
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE dialog_turns(ts REAL, text TEXT)")

    def tearDown(self):
        self.conn.close()

    def add(self, ts, text):
        self.conn.execute("INSERT INTO dialog_turns VALUES (?,?)", (ts, text))

    def probe(self, since, now, prefix=MARKER, liveness_min=60):
        args = argparse.Namespace(liveness_max_min=liveness_min)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.mod.run(self.conn, prefix, since, now, args)
        return rc, buf.getvalue()

    # ---------- 分组腿（这一格修的就是它） ----------
    def test_fractional_ts_in_one_utc_day_collapses_to_a_single_day_row(self):
        d0, d1 = day_epoch("2026-10-05"), day_epoch("2026-10-06")
        for ts in (d0 + 100.5, d0 + 20000.25, d0 + 40000.75, d1 + 500.125):
            self.add(ts, MARKER + " 正文")
        rc, out = self.probe(since=d1 + 40000, now=d1 + 40300)
        self.assertEqual(
            [("2026-10-05", 3), ("2026-10-06", 1)], day_rows(out),
            "ts 是 REAL：按 ts/86400 逐行分组会把同一天打成多行（09-30 第一版的病）")
        self.assertIn("TOTAL=4 闸门后=0 闸门前=4", out)
        self.assertEqual(4, rc)

    def test_more_than_40_hit_days_prints_the_truncation_note(self):
        base = day_epoch("2026-01-01")
        for i in range(41):
            self.add(base + i * 86400 + 10.5, MARKER + " x")
        rc, out = self.probe(since=base, now=base + 41 * 86400)
        self.assertIn("(截断：共 41 天有命中)", out)
        self.assertEqual(40, len(DAY_ROW.findall(out)), "只列 40 天这件事必须在文书里点名")

    def test_within_40_hit_days_prints_no_truncation_note(self):
        base = day_epoch("2026-01-01")
        for i in range(3):
            self.add(base + i * 86400 + 1.5, MARKER + " x")
        rc, out = self.probe(since=base, now=base + 3 * 86400)
        self.assertNotIn("截断", out)
        self.assertEqual(3, len(DAY_ROW.findall(out)))

    # ---------- 三态判据腿 ----------
    def test_stale_dialog_turns_refuses_to_judge(self):
        now = time.time()
        self.add(now - 90 * 60, "普通用户消息")
        rc, out = self.probe(since=now - 3600, now=now, liveness_min=60)
        self.assertEqual(4, rc)
        self.assertIn("VERDICT=INCONCLUSIVE-", out)
        self.assertIn("0 命中不可当证据", out)
        self.assertNotIn("VERDICT=PASS", out)

    def test_age_exactly_at_the_liveness_threshold_still_counts_as_live(self):
        now = time.time()
        self.add(now - 60 * 60, "普通用户消息")        # 边界：等于阈值算活
        rc, out = self.probe(since=now - 7200, now=now, liveness_min=60)
        self.assertEqual(0, rc)
        self.assertIn("VERDICT=PASS-PARTIAL", out)

    def test_live_table_with_zero_in_window_hits_is_pass_partial(self):
        now = time.time()
        self.add(now - 30 * 60, "普通用户消息")          # 新鲜行，撑活体门
        self.add(now - 90000, MARKER + " 闸门前的回声")  # 命中在窗外
        rc, out = self.probe(since=now - 86400, now=now)
        self.assertEqual(0, rc)
        self.assertIn("VERDICT=PASS-PARTIAL", out)
        self.assertIn("TOTAL=1 闸门后=0 闸门前=1", out)

    def test_in_window_hit_reports_fail(self):
        now = time.time()
        self.add(now - 10 * 60, MARKER + " 闸门后仍在回声")
        rc, out = self.probe(since=now - 86400, now=now)
        self.assertEqual(1, rc)
        self.assertIn("VERDICT=FAIL 窗内仍有 1 条回声落库", out)

    def test_non_numeric_ts_is_skipped_not_read_as_zero_age(self):
        now = time.time()
        self.add("not-an-epoch", MARKER + " 正文")
        rc, out = self.probe(since=now - 3600, now=now)
        self.assertRegex(out, r"dialog_turns\s+rows=1\s+ts 非 epoch")
        self.assertIn("命中跨度 ts 非 epoch", out,
                      "有命中时那句跨度打印也不许把尺子带走")
        self.assertEqual(4, rc, "ts 读不出＝没有活体证据 ⇒ 只能拒判")

    def test_tables_without_ts_are_skipped_not_counted_as_fresh(self):
        now = time.time()
        self.add(now - 10 * 60, "普通用户消息")
        rc, out = self.probe(since=now - 3600, now=now)
        self.assertRegex(out, r"agent_traces\s+skip\(no ts\)")
        self.assertIn("FRESH_TABLES=1/7", out)

    # ---------- 满窗与否的诚实度 ----------
    def test_window_shorter_than_24h_prints_the_missing_hours_note(self):
        now = time.time()
        self.add(now - 10 * 60, "普通用户消息")
        rc, out = self.probe(since=now - 5 * 3600, now=now)
        self.assertIn("NOTE 验收句要的是连续 24h", out)
        self.assertIn("距满窗还差 19.0 h", out)

    def test_full_24h_window_prints_no_shortfall_note(self):
        now = time.time()
        self.add(now - 10 * 60, "普通用户消息")
        rc, out = self.probe(since=now - 30 * 3600, now=now)
        self.assertNotIn("NOTE 验收句", out)

    # ---------- 前缀值不落文书 ----------
    def test_prefix_value_is_never_printed(self):
        now = time.time()
        secret = "ZZLEAKPROOFMARKER7Q"
        self.add(now - 10 * 60, secret + " 正文")
        rc, out = self.probe(since=now - 3600, now=now, prefix=secret)
        self.assertIn("PREFIX_LEN=%d（值不落文书）" % len(secret), out)
        self.assertNotIn(secret, out)


class ReadPrefixLegs(unittest.TestCase):
    """死尺自报腿：读不到字面量就退出，⛔ 拿记忆里的串当默认值。"""

    def setUp(self):
        self.mod = load_probe()
        self.tmp = tempfile.mkdtemp(prefix="b97_readprefix")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_src(self, body, bom=False):
        path = os.path.join(self.tmp, "app.py")
        with open(path, "wb") as f:
            f.write(("\ufeff" if bom else "").encode("utf-8") + body.encode("utf-8"))
        self.mod.SRC_APP = path
        return path

    def test_returns_the_literal_from_the_source_file(self):
        self.write_src('SELF_PUSH_PREFIX = "%s 不要加前缀"\n' % MARKER)
        self.assertEqual(self.mod.read_prefix(), MARKER + " 不要加前缀")

    def test_bom_prefixed_source_still_reads(self):
        self.write_src('SELF_PUSH_PREFIX = "%s"\n' % MARKER, bom=True)
        self.assertEqual(self.mod.read_prefix(), MARKER)

    def test_missing_literal_exits_with_code_3_instead_of_defaulting(self):
        self.write_src('SOME_OTHER_KEY = "whatever"\n')
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(err):
                self.mod.read_prefix()
        self.assertEqual(3, cm.exception.code)
        self.assertIn("读不到 SELF_PUSH_PREFIX", err.getvalue())

    def test_real_repo_source_still_exposes_the_literal(self):
        self.mod.SRC_APP = os.path.join(REPO, "butler", "notifier", "app.py")
        value = self.mod.read_prefix()
        self.assertTrue(isinstance(value, str) and len(value) > 0)
        self.assertNotIn("\n", value)


class MainCopyLegs(unittest.TestCase):
    """main() 面：只读物复制、WAL 侧文件同拷、冲突即拒、跑完不留件。"""

    def setUp(self):
        self.mod = load_probe()
        self.tmp = tempfile.mkdtemp(prefix="b97_mainlegs")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "butler.db")
        self.cp = os.path.join(self.tmp, "echo_window_probe.db")
        src = os.path.join(self.tmp, "app.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write('SELF_PUSH_PREFIX = "%s"\n' % MARKER)
        self.mod.DB = self.db
        self.mod.CP = self.cp
        self.mod.SRC_APP = src
        self._argv = sys.argv
        self.writer = None
        self.addCleanup(setattr, sys, "argv", self._argv)

    def seed(self, rows, wal=False):
        c = sqlite3.connect(self.db)
        c.execute("PRAGMA journal_mode=%s" % ("WAL" if wal else "DELETE"))
        c.execute("CREATE TABLE dialog_turns(ts REAL, text TEXT)")
        c.executemany("INSERT INTO dialog_turns VALUES (?,?)", rows)
        c.commit()
        return c

    def append_while_open(self, rows):
        """连接不关 ⇒ 这些行只住在 -wal 里（主文件读不到）。"""
        c = sqlite3.connect(self.db)
        c.executemany("INSERT INTO dialog_turns VALUES (?,?)", rows)
        c.commit()
        self.writer = c
        return c

    def invoke(self, since_epoch):
        sys.argv = ["echo_window_probe.py", "--since", iso_z(since_epoch)]
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = self.mod.main()
        return rc, buf.getvalue() + err.getvalue()

    def dialog_rows_in_output(self, out):
        m = re.search(r"dialog_turns\s+rows=(\d+)", out)
        return m.group(1) if m else None

    def test_copies_wal_sidecars_so_wal_resident_rows_are_visible(self):
        now = time.time()
        c = self.seed([(now - 7200, "普通用户消息")], wal=True)
        c.close()                       # 关连接＝checkpoint，主文件里有 1 行
        self.append_while_open([(now - 60, "普通用户消息")])   # 第 2 行只在 wal 里
        rc, out = self.invoke(now - 86400)
        self.assertIn("COPY_PAIRS=3", out)
        self.assertEqual("2", self.dialog_rows_in_output(out),
                         "只拷主库文件会漏掉仍住在 WAL 里的最新行")
        self.assertIn("COPY_LEFT=0", out)

    def test_leaves_no_copy_behind_when_sidecars_are_absent(self):
        now = time.time()
        c = self.seed([(now - 60, "普通用户消息")])
        c.close()
        rc, out = self.invoke(now - 3600)
        self.assertIn("COPY_PAIRS=1", out)
        self.assertIn("COPY_LEFT=0", out)
        for p in (self.cp, self.cp + "-wal", self.cp + "-shm"):
            self.assertFalse(os.path.exists(p), "只读物必须当场删除：%s" % p)

    def test_refuses_when_a_concurrent_probe_copy_is_already_there(self):
        now = time.time()
        c = self.seed([(now - 60, "普通用户消息")])
        c.close()
        open(self.cp, "wb").close()
        rc, out = self.invoke(now - 3600)
        self.assertEqual(3, rc)
        self.assertIn("并发探针", out)
        self.assertNotIn("COPY_PAIRS", out)

    def test_unparsable_since_exits_before_touching_the_copy_path(self):
        sys.argv = ["echo_window_probe.py", "--since", "2026-13-45T99:00:00Z"]
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = self.mod.main()
        self.assertEqual(3, rc)
        self.assertIn("--since 解析", err.getvalue())
        self.assertFalse(os.path.exists(self.cp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
