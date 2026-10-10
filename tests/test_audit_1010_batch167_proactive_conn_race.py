#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批167（D15 收尾）：`butler/proactive/engine.py` 的 SQLite 懒连接摘掉最后一枚裸 check-then-act。

病灶现读（改前，取自 `git show HEAD:butler/proactive/engine.py`）：`butler/proactive/engine.py:38-49`

    def get_conn():
        global _conn
        if _conn is None:          ← 判定在锁外：并发首调 8 个线程各进得来
            ...
            c = sqlite3.connect(...)
            _conn = c              ← 先发布
            _init(c)               ← 建表在发布之后才跑

⇒ 八线程建八条连接、只留一条，其余七条没人 close（每条持一个 fd 与一个 WAL 读锁）；
⇒ 半成品连接已对外可见；`_init` 抛错后坏对象永久缓存＝一次故障变永久故障。

同族 9 枚 store（`decision_store/notification_store/pwa_chat_store/user_location/
whitelist/task_store/timeseries.store/config/aliases`）早在 `butler.core.singleton` 上，
本件收最后一枚。授权范围＝只改这一处初始化形状，⛔ 动问询业务规则、⛔ 动表结构。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break

try:
    import butler.proactive.engine as eng
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest("宿主面缺依赖（%s）⇒ 运行时腿只在容器内跑" % type(exc).__name__)

TABLE = "proactive_daily_usage"


class FakeConn:
    """DB 边界假件：记 execute/executescript/close，不碰任何真文件。"""

    def __init__(self, sink):
        self.sink = sink
        self.row_factory = None

    def execute(self, sql, params=None):
        self.sink["executed"].append(sql)
        return types.SimpleNamespace(fetchone=lambda: {"journal_mode": "wal"})

    def executescript(self, sql):
        self.sink["scripts"].append(sql)

    def commit(self):
        self.sink["commits"] += 1

    def close(self):
        self.sink["closed"] += 1


class FakeSqlite:
    """把 `sqlite3.connect` 换成会数次数、且故意慢一点的假件（慢＝把竞态窗口撑开）。"""

    def __init__(self, sink, delay=0.02):
        self.sink = sink
        self.delay = delay
        self.Row = object

    def connect(self, *args, **kwargs):
        self.sink["connects"] += 1
        self.sink["conns"].append(None)      # 占位：真对象在下面返回时替换
        time.sleep(self.delay)               # 慢一点＝把「判定在锁外」的窗口撑开
        c = FakeConn(self.sink)
        self.sink["conns"][-1] = c
        return c


def _sink():
    return {"connects": 0, "conns": [], "closed": 0, "commits": 0,
            "executed": [], "scripts": []}


def _hit_threads(n):
    """n 枚线程在同一时刻冲进 get_conn（改前＝同一时刻各建一条）。"""
    results = [None] * n
    errors = []
    barrier = threading.Barrier(n)

    def body(i):
        barrier.wait(5)          # 齐步走：最后一枚到位即集体放行
        try:
            results[i] = eng.get_conn()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=body, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    return results, errors


class _Harness(unittest.TestCase):
    def setUp(self):
        self._prev_conn = eng._conn
        eng._conn = None
        self.tmp = tempfile.mkdtemp(prefix="b167_")
        p = mock.patch.object(eng, "get_settings",
                              lambda: types.SimpleNamespace(data_dir=self.tmp))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._restore)

    def _restore(self):
        eng._conn = self._prev_conn

    def _fake_sqlite(self, sink, delay=0.02):
        p = mock.patch.object(eng, "sqlite3", FakeSqlite(sink, delay))
        p.start()
        self.addCleanup(p.stop)


class SingleConnectionLeg(_Harness):
    """真缺陷腿：并发首调只准建一条连接。"""

    def test_eight_concurrent_first_calls_build_one_connection(self):
        sink = _sink()
        self._fake_sqlite(sink)
        results, errors = _hit_threads(8)
        self.assertEqual([], errors, "并发首调里抛错了：%s" % errors)
        self.assertEqual(1, sink["connects"],
                         "并发首调建了 %d 条连接（改前＝8 条，7 条泄漏）" % sink["connects"])

    def test_all_callers_get_the_same_object(self):
        sink = _sink()
        self._fake_sqlite(sink)
        results, errors = _hit_threads(8)
        self.assertEqual([], errors, "并发首调里抛错了：%s" % errors)
        self.assertEqual(8, len(results))
        self.assertTrue(all(r is not None for r in results),
                        "有线程拿到了 None（工厂抛错被吞）")
        self.assertTrue(all(r is results[0] for r in results),
                        "拿回来的不是同一枚连接：%s" % [id(r) for r in results])


class NoHalfBakedPublishLeg(_Harness):
    """真缺陷腿：`_init` 抛错⇒ 坏连接既不发布也不漏句柄，下次调用能重建。"""

    def test_ddl_failure_closes_the_connection(self):
        sink = _sink()
        self._fake_sqlite(sink)
        p = mock.patch.object(eng, "_init", side_effect=RuntimeError("DDL 崩"))
        p.start()
        self.addCleanup(p.stop)
        with self.assertRaises(RuntimeError):
            eng.get_conn()
        self.assertEqual(1, sink["closed"], "半途而废的连接没关＝句柄泄漏")

    def test_ddl_failure_does_not_poison_the_global(self):
        sink = _sink()
        self._fake_sqlite(sink)
        p = mock.patch.object(eng, "_init", side_effect=RuntimeError("DDL 崩"))
        p.start()
        self.addCleanup(p.stop)
        with self.assertRaises(RuntimeError):
            eng.get_conn()
        self.assertIsNone(eng._conn, "坏对象被缓存了＝一次故障变永久故障")
        p.stop()
        ok = eng.get_conn()
        self.assertIsNotNone(ok, "建表失败过一次之后就再也起不来")
        self.assertEqual(2, sink["connects"], "重建时应再走一次工厂")


class RealBehaviorControl(_Harness):
    """正对照：换成双检锁之后，真 sqlite 上 WAL／建表／row_factory 三件事一件都不能少。"""

    def test_real_connection_has_wal_and_the_table(self):
        expected_row = eng.sqlite3.Row
        c = eng.get_conn()
        self.addCleanup(c.close)
        self.assertEqual("wal", c.execute("PRAGMA journal_mode").fetchone()[0])
        names = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn(TABLE, names, "工厂跑完却没建表：_init 没在发布前执行")
        self.assertIs(expected_row, c.row_factory)

    def test_second_call_is_the_hot_path(self):
        c1 = eng.get_conn()
        self.addCleanup(c1.close)
        with mock.patch.object(eng, "sqlite3", FakeSqlite(_sink())):
            c2 = eng.get_conn()
        self.assertIs(c1, c2, "已发布之后又跑了一遍工厂＝热路径没走无锁分支")


class ShapeRulerLeg(unittest.TestCase):
    """形状尺：这一枚必须是 helper 站点，⛔ 再是裸 check-then-act。"""

    NEEDLES = ('singleton.init(globals(), "_conn"', "def _build_conn")
    FORBIDDEN = re.compile(r"^\s+if _conn is None:\s*$", re.M)

    def _src(self):
        with open(os.path.abspath(eng.__file__), encoding="utf-8") as f:
            return f.read()

    def test_source_is_on_the_helper(self):
        src = self._src()
        for n in self.NEEDLES:
            self.assertTrue(n in src, "缺 %s" % n)
        self.assertIsNone(self.FORBIDDEN.search(src),
                          "还有裸 check-then-act 判定留在锁外")

    def test_ruler_bites_the_old_shape(self):
        """负控：同一把尺子对旧形状必须咬住（⛔ 恒绿尺）。"""
        old = ('def get_conn():\n    global _conn\n    if _conn is None:\n'
               '        _conn = sqlite3.connect("x")\n    return _conn\n')
        self.assertIsNotNone(self.FORBIDDEN.search(old), "尺子咬不住旧形状＝这条绿没意义")

    def test_scope_ddl_and_business_rules_untouched(self):
        """授权边界：只准改初始化形状——表结构与「0 行 UPDATE 也提交」那条写面根因⛔ 一起动。"""
        src = self._src()
        for keep in ("CREATE TABLE IF NOT EXISTS proactive_daily_usage",
                    "CREATE TABLE IF NOT EXISTS proactive_inquiries",
                    "add_column_if_absent"):
            self.assertTrue(keep in src, "越界：源里少了 %s" % keep)
        self.assertTrue("n = int(cur.rowcount or 0)" in src,
                        "越界：sweep_expired 的根因腿被改动")


if __name__ == "__main__":
    unittest.main(verbosity=2)
