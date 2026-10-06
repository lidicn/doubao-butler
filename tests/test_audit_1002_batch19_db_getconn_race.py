"""批19 验收＝第 12 份来源报告（`doubao-butler-稳定性功能审计报告.md`，P0-2）里**能自己站住**的那两枚。

报告坐标（E 盘现读，该目录只在 E 盘）：`:78 ### P0-2 SQLite 全局单连接跨线程共享，存在启动期竞态与写入冲突`；
其自报快照 `836df79` ⇒ `git cat-file -t 836df79` = `Not a valid object name`（权威树查无此号）。它给三现象一修法，我分开判：

  ① 懒初始化竞态＝真：`butler/store/db.py:39` 的 `if _conn is None:` 判定在锁外，第一个 `with _lock` 在 `:57`
     ⇒ 并发首调各建一条，先建的那条被 `:59` 覆盖后⛔ 关（句柄泄漏）。
  ② 半初始化连接对外可见＝真：`:59 _conn = c` 在 `:61 _init(_conn)` **之前**，建表／ALTER 期间别的线程已能
     从 `get_conn()` 拿到「表还没建完」的连接 ⇒ `no such table: dialog_turns`。
  ③ 并发 execute/commit 无保护＝**同题于既有写面停摆线，本批⛔ 按报告原样落地**（数见
     `workorders/readings/1003b19/atomicity_sites_run1.txt`：全仓 `sqlite3.connect` 18 处、0 处设过
     `isolation_level`、258 execute／92 commit、**33 个函数是多条写共用一次 commit**）。报告给的
     `isolation_level=None` 要把那 33 处的跨语句原子性拆掉；「线程本地连接」把 1 条连接变 N 条，而
     `doc/写面复摆取证-20261001.md:144-149` 已定根因＝「某条连接的隐式事务没被 commit 悬住」⇒ 方案与自己的
     根因方向相反。这条挂既有账（裁 C 自愈 + `txn_census`），另发决策申请。

去重（开单前先拿机制原文 grep 自家台账）：`scripts/audit_1002/roster_reconcile_1002.tsv` **表行 3**
（`M-21,P0-2,P0-3,T-10`／桶 B）机制原文＝「SQLite 全局单连接跨线程共享：双检竞态、半初始化连接可见、
execute/commit 无锁」＝本报告 P0-2 同物；**表行 24**（`M-11,P1-7`／桶 B）前半「`_conn` 先缓存后 `_init`
（坏连接永久缓存）」由本批第四条腿一并销，其后半「全树 0 处 rollback → 事务悬挂带写锁」归既有写面线
（裁 C 已落码，task #44）。桶 B 在台账 `:1548` 登记为「⛔ 现网不可复现类」待抽，本批把前两枚现象做成可复现验收。

本文件对 ①② 及派生的两枚（`_init` 抛错后⛔ 缓存坏连接、⛔ 留着那条句柄）下验收，腿⛔ 靠 sleep 赌时序：
  · 「建表前不对外」＝读者线程从 `_init` **内部**起，「主线程正在建表」由代码结构给出，读者另外记下
    `get_conn` 返回那一刻建表是否还在跑（两个信号，不靠谁先抢到 CPU）；
  · 「只建一条」＝在 `connect` 里等满 THREADS 枚到齐（最多 0.6s），并发是被强制出来的。
另有三枚**护栏**（改前改后都绿，⛔ 指望它们先红）：守「把 `_init` 挪进 `_lock`」这一步不自死锁
（`_lock` 是 `threading.Lock`＝非重入），以及热路径⛔ 因此加上抢锁（现网多模态 P95 已 6,259ms）。

命名纪律：全部 `unittest.TestCase` 方法（⛔ 模块级 `test_*`，discover 不 import pytest 时会静默收下）。
运行时腿＝真临时 sqlite：`config._settings` 钉 tmp + `db_mod._conn = None`，⛔ 碰现网库、⛔ mock 掉我要验的那层。
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

import butler.config as config
from butler.config import Settings
from butler.store import db as db_mod

TREE = pathlib.Path(__file__).resolve().parents[1]
DB_PY = TREE / "butler" / "store" / "db.py"
THREADS = 8


class SqliteNamespace:
    """只替 `butler.store.db` 命名空间里的 `sqlite3`，⛔ 动 stdlib 全局属性。

    `db.py` 运行期只用到 `connect` 与 `Row`（`Connection` 只出现在注解里，且该文件
    `from __future__ import annotations` ⇒ 不求值），其余属性一律转发给真模块。
    """

    def __init__(self):
        self.Row = sqlite3.Row
        self.created: list = []
        self.gate_deadline: float | None = None
        self.gate_hold = 0.0
        self.connect = sqlite3.connect

    def __getattr__(self, item):
        return getattr(sqlite3, item)


def gated_connect(holder: SqliteNamespace, hold: float):
    real_connect = sqlite3.connect
    holder.gate_hold = hold

    def connect(path, *a, **kw):
        holder.created.append(None)
        idx = len(holder.created) - 1
        if hold:
            if holder.gate_deadline is None:
                holder.gate_deadline = time.time() + hold
            # 等其余线程也走到这里（坏码里 8 枚都进得来；好码里只有 1 枚进得来，等满时限）
            while len(holder.created) < THREADS and time.time() < holder.gate_deadline:
                time.sleep(0.01)
        c = real_connect(str(path), *a, **kw)
        holder.created[idx] = c
        return c

    holder.connect = connect
    db_mod.sqlite3 = holder
    return holder


class _Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="butler_b19_")
        self.saved_settings = config._settings
        config._settings = Settings(data_dir=self.tmp)
        self.saved_conn = db_mod._conn
        self.saved_lock = db_mod._lock
        self.saved_sqlite3 = db_mod.sqlite3
        db_mod._conn = None

    def tearDown(self):
        if db_mod._conn is not None:
            try:
                db_mod._conn.close()
            except Exception:
                pass
        db_mod._conn = self.saved_conn
        db_mod._lock = self.saved_lock
        db_mod.sqlite3 = self.saved_sqlite3
        config._settings = self.saved_settings
        shutil.rmtree(self.tmp, ignore_errors=True)


class PublishOrderTest(_Harness):
    """现象②：先跑完建表／迁移，再把连接对外发布。"""

    def test_init_runs_before_the_connection_is_published(self):
        """`_init` 被调的那一刻，模块全局 `_conn` 必须还是 None。"""
        seen = {}
        real_init = db_mod._init

        def spy_init(c):
            seen["conn_at_init_time"] = db_mod._conn
            seen["same_object"] = c
            return real_init(c)

        db_mod._init = spy_init
        try:
            conn = db_mod.get_conn()
        finally:
            db_mod._init = real_init

        self.assertIn("conn_at_init_time", seen, "get_conn 压根没调 _init＝建表/迁移没跑")
        self.assertIsNone(
            seen["conn_at_init_time"],
            "_init 执行期间 _conn 已＝%s ⇒ 表还没建完的连接已对外可见（P0-2 现象②）"
            % type(seen["conn_at_init_time"]).__name__,
        )
        self.assertIs(conn, seen["same_object"], "发布出去的连接≠跑过 _init 的那条")

    def test_concurrent_reader_never_gets_a_tableless_connection(self):
        """在 `_init` 里插一条读者线程：它⛔ 能拿到一条查不到 `dialog_turns` 的连接。

        两个独立信号：(a) 读者 `get_conn` 返回那一刻建表还在不在跑；(b) 读者的查询有没有报
        `no such table`。(a) 是先响的那一枚——坏码里读者立刻拿到已发布的半初始化连接。
        ⛔ 在本函数里 `join` 读者：好码中读者会卡在 `_lock` 上直到发布，join 必须放到
        `get_conn()` 返回之后，否则这条腿自己造死锁、把「修法有效」读成「修法死锁」。
        """
        box: dict = {}
        real_init = db_mod._init

        def reader():
            try:
                conn = db_mod.get_conn()
                box["init_still_running_when_returned"] = box.get("in_init", None)
                n = conn.execute("SELECT count(*) AS n FROM dialog_turns").fetchone()["n"]
                box.update(ok=True, n=int(n), err=None)
            except Exception as exc:
                box.update(ok=False, err="%s: %s" % (type(exc).__name__, exc))

        def slow_init(c):
            box["in_init"] = True
            th = threading.Thread(target=reader, name="b19-reader")
            box["th"] = th
            th.start()
            time.sleep(0.3)          # 坏码里读者此刻早已返回；好码里它必然还卡在锁上
            real_init(c)
            box["in_init"] = False

        db_mod._init = slow_init
        try:
            db_mod.get_conn()
        finally:
            db_mod._init = real_init

        box["th"].join(timeout=15)
        self.assertFalse(box["th"].is_alive(), "读者线程 15s 还没返回＝锁形状把我自己卡住了，本条读数作废")
        self.assertIs(box.get("init_still_running_when_returned"), False,
                      "读者在「建表还在跑」时就从 get_conn 拿到了连接（P0-2 现象②）；"
                      "它当时看到的 in_init＝%r" % box.get("init_still_running_when_returned"),)
        self.assertTrue(box.get("ok", False),
                        "并发读者查不到表：%s（P0-2 现象②）" % box.get("err"))
        self.assertEqual(box.get("n"), 0)


class SingleConnectionTest(_Harness):
    """现象①：并发首调只建一条连接（先建的那条被覆盖＝永不关闭的句柄泄漏）。"""

    def test_concurrent_first_call_builds_exactly_one_connection(self):
        holder = gated_connect(SqliteNamespace(), hold=0.6)
        results, errors = [], []

        def worker():
            try:
                results.append(db_mod.get_conn())
            except Exception as exc:
                errors.append("%s: %s" % (type(exc).__name__, exc))

        ths = [threading.Thread(target=worker, name="b19-w%d" % i) for i in range(THREADS)]
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=30)

        live = [c for c in holder.created if c is not None]
        # 断言顺序＝失败原因顺序：先报「建了几条」（我要修的那件事），再报附带抛错
        self.assertEqual(
            len(live), 1,
            "并发首调建了 %d 条连接（P0-2 现象①：判定在锁外⇒各建一条，先建的那条被覆盖后永不关闭）"
            % len(live),
        )
        self.assertFalse(errors, "并发首调里有线程抛错：%s" % errors)
        self.assertEqual(len(results), THREADS, "有线程没返回（卡死）")
        self.assertTrue(all(r is db_mod._conn for r in results),
                        "各线程拿到的连接不是同一个对象")
        self.assertIs(db_mod._conn, live[0], "发布的全局连接≠唯一那条新建连接")
        live[0].execute("SELECT count(*) AS n FROM dialog_turns").fetchone()  # 建表跑过⪰1 次
        self.assertEqual(live[0].execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal",
                         "WAL 那道 PRAGMA 被我这把改造挤掉了")


class BrokenPublishTest(_Harness):
    """②的派生两枚（＝合并表 表行 24 前半 ＋ 现象① 同类泄漏）：`_init` 抛错时的处置。

    坏码里 `:59` 先 `_conn = c` 再 `:61 _init(_conn)`，建表一抛错那条未建表的连接就留在模块全局上，
    此后每次 `get_conn()` 都直接返回它 ⇒ 一次启动期故障变成永久故障（且⛔ 再跑过一次 `_init`）。
    第二条腿管那条被放弃的连接：⛔ 关＝每次启动失败漏一个句柄。
    """

    def test_init_failure_does_not_cache_a_broken_connection(self):
        real_init = db_mod._init

        def boom(c):
            raise sqlite3.OperationalError("simulated _init failure")

        db_mod._init = boom
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db_mod.get_conn()
        finally:
            db_mod._init = real_init

        self.assertIsNone(
            db_mod._conn,
            "_init 抛错后 `_conn` 已＝%s ⇒ 未建表的连接被永久缓存（表行 24 前半／P0-2 现象②）"
            % type(db_mod._conn).__name__,
        )
        again = {"n": 0}

        def counting_init(c):
            again["n"] += 1
            return real_init(c)

        db_mod._init = counting_init
        try:
            conn = db_mod.get_conn()
        finally:
            db_mod._init = real_init

        self.assertEqual(again["n"], 1, "第二次 get_conn 没重跑 _init＝复用了缓存的坏连接")
        conn.execute("SELECT count(*) AS n FROM dialog_turns").fetchone()

    def test_init_failure_closes_the_abandoned_connection(self):
        """抛错那条连接必须被关掉：⛔ 留着＝每次启动失败漏一个句柄（与现象① 同一类泄漏）。"""
        box: dict = {}
        real_init = db_mod._init

        def grab(c):
            box["c"] = c
            raise sqlite3.OperationalError("simulated _init failure")

        db_mod._init = grab
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db_mod.get_conn()
        finally:
            db_mod._init = real_init

        still_open: bool | None = None
        try:
            box["c"].execute("SELECT 1").fetchone()
            still_open = True
        except sqlite3.ProgrammingError:
            still_open = False
        self.assertFalse(
            still_open,
            "_init 抛错后那条连接（id 已记）仍开着＝句柄泄漏（现象① 同类：先建的那条没人关）",
        )


class GuardRails(unittest.TestCase):
    """三枚护栏：改前改后都该绿，⛔ 指望它们先红。守的是「把 `_init` 挪进 `_lock`」会不会自死锁／变慢。"""

    def test_init_body_does_not_call_get_conn(self):
        tree = ast.parse(DB_PY.read_text(encoding="utf-8"), str(DB_PY))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_init")
        hits = [n.lineno for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "get_conn"]
        self.assertEqual(hits, [], "_init 里调了 get_conn（行 %s）⇒ 把 _init 放进 _lock 会自死锁" % hits)

    def test_no_lock_body_calls_get_conn(self):
        tree = ast.parse(DB_PY.read_text(encoding="utf-8"), str(DB_PY))
        bad = []
        for n in ast.walk(tree):
            if isinstance(n, ast.With) and any(
                    isinstance(m.context_expr, ast.Name) and m.context_expr.id == "_lock"
                    for m in n.items):
                bad += [i.lineno for i in ast.walk(n)
                        if isinstance(i, ast.Call) and isinstance(i.func, ast.Name)
                        and i.func.id == "get_conn"]
        self.assertEqual(bad, [], "with _lock 体内调用 get_conn（行 %s）＝非重入 Lock 上自死锁" % bad)

    def test_second_call_does_not_take_the_lock(self):
        """热路径：连接已发布后 `get_conn()` ⛔ 再抢 `_lock`（⛔ 把每次取连接都排进全局锁）。"""
        class CountingLock:
            def __init__(self, real):
                self.real = real
                self.acquisitions = 0

            def __enter__(self):
                self.acquisitions += 1
                return self.real.__enter__()

            def __exit__(self, *exc):
                return self.real.__exit__(*exc)

        tmp = tempfile.mkdtemp(prefix="butler_b19_guard_")
        saved_settings, saved_conn = config._settings, db_mod._conn
        config._settings = Settings(data_dir=tmp)
        db_mod._conn = None
        try:
            db_mod.get_conn()                    # 冷启动一次（允许抢锁）
            counter = CountingLock(db_mod._lock)
            db_mod._lock = counter
            try:
                for _ in range(100):
                    db_mod.get_conn()
            finally:
                db_mod._lock = counter.real
            self.assertEqual(counter.acquisitions, 0,
                             "热路径上 get_conn 抢了 %d 次锁" % counter.acquisitions)
        finally:
            if db_mod._conn is not None:
                try:
                    db_mod._conn.close()
                except Exception:
                    pass
            db_mod._conn = saved_conn
            config._settings = saved_settings
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
