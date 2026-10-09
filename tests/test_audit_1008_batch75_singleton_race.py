"""批75 验收＝D14（P1·high，`core/aliases.py get_alias_store` 的 check-then-act 懒单例竞态）
＋ D15（P2·medium，§20 批19 只修了 `store/db.py` 的 `get_conn`，同族其余站点没人逐枚点开）。

报告坐标（E 盘面）：台账 §85 把 D14 记为「`第七轮`自己写明『只修 D4 不修 D14，别名在正常并发下照样丢』」，
D15 记为「批19 修的是 `store/db.py`，同族其余站点未逐枚点开」。现树探针（加压＝`setswitchinterval(1e-6)`，
⛔ patch 被测工厂，8 线程 barrier）读数在 `workorders/readings/1008_b75/b75_probe_1008.txt`：
`get_alias_store` 每 200 轮里 185～197 轮出现 >1 枚实例（worst＝8＝八条线程各造各的），
`decision_store/notification_store.get_conn` 每 60 轮里 55～60 轮 >1 条连接，`get_settings` 20/20 轮 >1 枚。

坏码形状（8 枚 sqlite 站点全同构，现读逐枚点开）：
    if _conn is None:            ← 判定在锁外
        ...
        _conn = c                ← 连接先对外发布
        _init(c)                 ← 建表／迁移在发布之后才跑
⇒ 现象①并发首调各建一条，先建的那条被覆盖后⛔ 关（句柄泄漏）；
⇒ 现象②表还没建完的连接已对外可见（`no such table`）；
⇒ 派生③`_init` 抛错后那条未建表的连接被永久缓存，一次启动期故障变永久故障。
（①②③与批19 在 `store/db.py` 上钉的是同一件事，那一格已有自己的闸：
`tests/test_audit_1002_batch19_db_getconn_race.py`。本批把同一条判定收成一份真源＝
`butler/core/singleton.py`，⛔ 在 10 个站点上各写一遍双检锁。）

腿的分工（⛔ 把「新能力腿」当「bug 先红」的证据）：
  · 竞态咬合腿（HEAD 必红，红因＝并发了却各造各的）：legs 1-6；
  · 护栏腿（改前改后都绿，守的是「收敛到 helper⛔ 换掉语义」）：legs 7-8；
  · 新能力腿（HEAD 上 `butler.core.singleton` 根本不存在⇒ 报 ImportError，红因＝模块缺席，
    ⛔ 拿它们当「bug 成立」的证据）：legs 9-11。
并发⛔ 靠 sleep 赌时序：工厂里等满 THREADS 枚到齐（最多 0.6s）——坏码里 8 枚都进得来，
好码里只有 1 枚进得来、等满时限。运行时腿＝真临时 sqlite（`config._settings` 钉 tmp＋站点 `_conn = None`），
⛔ 碰现网库、⛔ mock 掉我要验的那一层。
命名纪律：全部 `unittest.TestCase` 方法（⛔ 模块级 `test_*`，discover 不 import pytest 时会静默收下）。
"""
from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

import butler.config as config
from butler.config import Settings

THREADS = 8
HOLD = 0.6

# 本批转换面（census 11 枚 − `proactive/engine.py`＝在途 21 枚 DCD 件之一，本批⛔ 动 ⇒ 登记另一半）
SQLITE_SITES = [
    "butler.core.decision_store",
    "butler.core.notification_store",
    "butler.core.pwa_chat_store",
    "butler.core.user_location",
    "butler.ha_tools.whitelist",
    "butler.store.task_store",
    "butler.timeseries.store",
]


class Gate:
    """工厂闸门：够 THREADS 枚到齐就放行，够不到就等满 HOLD 后放行。

    坏码＝8 枚全进得来（立刻放行）；好码＝只有 1 枚进得来（等满 HOLD 才放行）。
    """

    def __init__(self, need=THREADS, hold=HOLD):
        self.need = need
        self.hold = hold
        self.n = 0
        self.deadline = None
        self.lock = threading.Lock()

    def arrive(self):
        with self.lock:
            self.n += 1
            if self.deadline is None:
                self.deadline = time.time() + self.hold
        while True:
            with self.lock:
                if self.n >= self.need or time.time() >= self.deadline:
                    return
            time.sleep(0.005)


def concurrent_getters(fn, threads=THREADS):
    results, errors = [], []
    guard = threading.Lock()
    barrier = threading.Barrier(threads)

    def worker():
        try:
            barrier.wait(timeout=15)
            v = fn()
            with guard:
                results.append(v)
        except BaseException as exc:
            with guard:
                errors.append("%s: %s" % (type(exc).__name__, exc))

    ths = [threading.Thread(target=worker, name="b75w") for _ in range(threads)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(30)
    alive = sum(1 for t in ths if t.is_alive())
    return results, errors, alive


class SqliteNamespace:
    """只替站点模块命名空间里的 `sqlite3`，⛔ 动 stdlib 全局属性。"""

    def __init__(self, gate):
        self.gate = gate
        self.Row = sqlite3.Row
        self.created = []
        self.real = sqlite3.connect

    def __getattr__(self, item):
        return getattr(sqlite3, item)

    def connect(self, path, *a, **kw):
        self.gate.arrive()
        idx = len(self.created)
        self.created.append(None)
        c = self.real(str(path), *a, **kw)
        self.created[idx] = c
        return c


class _SiteHarness(unittest.TestCase):
    """把站点的全局与 `config._settings` 换到探针私有 tmp 目录，用完逐样还原。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="butler_b75_")
        self.saved = []
        self.saved_env = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = self.tmp          # 工厂里重跑 Settings.load 也只会落在 tmp
        self.saved_settings = config._settings
        config._settings = Settings(data_dir=self.tmp)

    def _reset(self, mod, var):
        self.saved.append((mod, var, getattr(mod, var)))
        setattr(mod, var, None)

    def tearDown(self):
        for mod, var, value in self.saved:
            if var == "_conn" and value is not None:
                try:
                    value.close()
                except Exception:
                    pass
            setattr(mod, var, value)
        config._settings = self.saved_settings
        if self.saved_env is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = self.saved_env
        shutil.rmtree(self.tmp, ignore_errors=True)


class AliasStoreRaceTest(_SiteHarness):
    """腿 1＝D14 正主：`get_alias_store` 并发首调只造一枚。"""

    def test_concurrent_callers_get_one_alias_store(self):
        mod = importlib.import_module("butler.core.aliases")
        real_cls = mod.AliasStore
        gate = Gate()
        built = []

        class CountingStore(real_cls):
            def __init__(self):
                gate.arrive()
                super().__init__()
                built.append(self)

        mod.AliasStore = CountingStore
        self._reset(mod, "_alias_store")
        try:
            results, errors, alive = concurrent_getters(mod.get_alias_store)
        finally:
            mod.AliasStore = real_cls

        self.assertEqual(alive, 0, "有线程卡在工厂里没回来＝夹具问题，本条读数作废")
        self.assertEqual(len(built), 1,
                         "并发首调造了 %d 枚 AliasStore（D14：判定在锁外⇒各造一枚，"
                         "先造的那枚被覆盖后没人持有，别名写入照样丢）" % len(built))
        self.assertFalse(errors, "并发首调里有线程抛错：%s" % errors)
        self.assertEqual(len(results), THREADS, "有线程没拿到返回值")
        self.assertTrue(all(r is built[0] for r in results), "各线程拿到的不是同一枚实例")
        self.assertIs(mod._alias_store, built[0], "发布的全局实例≠唯一那枚新建实例")


class SettingsRaceTest(_SiteHarness):
    """腿 3＝`get_settings` 同构竞态（11 枚里最热的一枚）。"""

    def test_concurrent_callers_get_one_settings(self):
        gate = Gate()
        built = []
        orig = Settings.__dict__["load"]
        real_load = Settings.load

        def gated(cls):
            gate.arrive()
            s = real_load()
            built.append(s)
            return s

        Settings.load = classmethod(gated)
        self._reset(config, "_settings")
        try:
            results, errors, alive = concurrent_getters(config.get_settings)
        finally:
            Settings.load = orig

        self.assertEqual(alive, 0, "有线程卡在工厂里没回来＝夹具问题，本条读数作废")
        self.assertEqual(len(built), 1,
                         "并发首调造了 %d 枚 Settings（同一族判定在锁外；这枚一旦被覆盖，"
                         "另一些调用方拿到的配置对象与全局那枚不同）" % len(built))
        self.assertFalse(errors, "并发首调里有线程抛错：%s" % errors)
        self.assertTrue(all(r is built[0] for r in results), "各线程拿到的不是同一枚 Settings")


class McpServerRaceTest(_SiteHarness):
    """腿 4＝`get_mcp_server` 同构竞态。"""

    def test_concurrent_callers_get_one_mcp_server(self):
        mod = importlib.import_module("butler.mcp.http_transport")
        real_cls = mod.MCPServer
        gate = Gate()
        built = []

        class CountingServer(real_cls):
            def __init__(self):
                gate.arrive()
                super().__init__()
                built.append(self)

        mod.MCPServer = CountingServer
        self._reset(mod, "_mcp_server")
        try:
            results, errors, alive = concurrent_getters(mod.get_mcp_server)
        finally:
            mod.MCPServer = real_cls

        self.assertEqual(alive, 0, "有线程卡在工厂里没回来＝夹具问题，本条读数作废")
        self.assertEqual(len(built), 1,
                         "并发首调造了 %d 枚 MCPServer（同族：两套工具注册表并存）" % len(built))
        self.assertFalse(errors, "并发首调里有线程抛错：%s" % errors)


class SqliteRaceTest(_SiteHarness):
    """腿 2／5／6＝7 枚 sqlite 站点逐枚跑同一条判定（一份实现，subTest 逐枚点名）。"""

    def _prep(self, name):
        mod = importlib.import_module(name)
        gate = Gate()
        holder = SqliteNamespace(gate)
        self.saved.append((mod, "sqlite3", getattr(mod, "sqlite3")))
        mod.sqlite3 = holder
        self._reset(mod, "_conn")
        real_init = mod._init
        self.saved.append((mod, "_init", real_init))
        return mod, holder, real_init

    def test_concurrent_first_call_opens_one_connection(self):
        for name in SQLITE_SITES:
            with self.subTest(site=name):
                mod, holder, _ = self._prep(name)
                results, errors, alive = concurrent_getters(mod.get_conn)
                live = [c for c in holder.created if c is not None]
                self.assertEqual(alive, 0, "有线程卡在 connect 里没回来＝夹具问题")
                self.assertEqual(len(live), 1,
                                 "并发首调开了 %d 条连接（D15 同族：先开的那条被覆盖后⛔ 关＝句柄泄漏）"
                                 % len(live))
                self.assertFalse(errors, "并发首调里有线程抛错：%s" % errors)
                self.assertTrue(all(r is mod._conn for r in results), "各线程拿到的连接不是同一条")
                self.assertEqual(live[0].execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal",
                                 "WAL 那道 PRAGMA 被改造挤掉了")
                for c in live:
                    c.close()

    def test_init_runs_before_the_connection_is_published(self):
        for name in SQLITE_SITES:
            with self.subTest(site=name):
                mod, holder, real_init = self._prep(name)
                seen = {}

                def spy_init(c):
                    seen["conn_at_init"] = mod._conn
                    seen["obj"] = c
                    return real_init(c)

                mod._init = spy_init
                try:
                    conn = mod.get_conn()
                finally:
                    mod._init = real_init
                self.assertIn("conn_at_init", seen, "get_conn 压根没调 _init＝建表／迁移没跑")
                self.assertIsNone(seen["conn_at_init"],
                                  "_init 执行期间 _conn 已＝%s ⇒ 表还没建完的连接已对外可见"
                                  % type(seen["conn_at_init"]).__name__)
                self.assertIs(conn, seen["obj"], "发布的连接≠跑过 _init 的那条")
                if conn is not None:
                    conn.close()

    def test_init_failure_is_not_cached_and_abandoned_connection_is_closed(self):
        for name in SQLITE_SITES:
            with self.subTest(site=name):
                mod, holder, real_init = self._prep(name)
                box = {}

                def boom(c):
                    box["c"] = c
                    raise sqlite3.OperationalError("simulated _init failure")

                mod._init = boom
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        mod.get_conn()
                    self.assertIn("c", box, "工厂压根没跑到 `_init`＝本腿后半段的『抛错后重建』没有素材")
                    self.assertIsNone(mod._conn,
                                      "_init 抛错后 _conn 已＝%s ⇒ 未建表的连接被永久缓存"
                                      % type(mod._conn).__name__)
                    loser = box["c"]
                    still_open = None
                    try:
                        loser.execute("SELECT 1").fetchone()
                        still_open = True
                    except sqlite3.ProgrammingError:
                        still_open = False
                    self.assertFalse(still_open, "抛错那条连接仍开着＝句柄泄漏")
                    mod._init = real_init
                    conn = mod.get_conn()
                    conn.execute("SELECT 1").fetchone()
                    conn.close()
                finally:
                    mod._init = real_init
                    for key in ("c",):
                        loser = box.get(key)
                        if loser is not None:
                            try:
                                loser.close()
                            except Exception:
                                pass
                    if mod._conn is not None:
                        try:
                            mod._conn.close()
                        except Exception:
                            pass
                        mod._conn = None


class CompatibilityGuardTest(_SiteHarness):
    """腿 7／8＝护栏（改前改后都绿）：全局仍是存储位，赋值还原的旧用法⛔ 失效。"""

    def test_published_object_is_the_module_global(self):
        mod = importlib.import_module("butler.core.aliases")
        self._reset(mod, "_alias_store")
        first = mod.get_alias_store()
        self.assertIs(mod._alias_store, first, "get_alias_store 没把实例发布到模块全局")
        self.assertIs(mod.get_alias_store(), first, "第二次调用重造了一枚")

    def test_assigning_none_rebuilds(self):
        """现网有几十处测试用 `mod._conn = None` 复位⇒ 收敛到 helper 后这条⛔ 断。"""
        mod = importlib.import_module("butler.core.aliases")
        self._reset(mod, "_alias_store")
        first = mod.get_alias_store()
        mod._alias_store = None
        second = mod.get_alias_store()
        self.assertIsNot(second, first, "置 None 后仍返回旧实例＝缓存搬进了 helper，现网复位手法全废")


class SingletonHelperTest(unittest.TestCase):
    """腿 9／10／11＝新能力腿：`butler.core.singleton` 在 HEAD 不存在⇒ ImportError（红因＝模块缺席）。"""

    def _helper(self):
        return importlib.import_module("butler.core.singleton")

    def test_hot_path_does_not_acquire_the_lock(self):
        helper = self._helper()
        holder = {"n": 0}

        def factory():
            holder["n"] += 1
            return object()

        gl = {"__name__": "b75_fake_module"}
        helper.init(gl, "_thing", factory)
        key = helper.lock_key(gl, "_thing")
        real = helper._LOCKS[key]

        class CountingLock:
            def __init__(self, inner):
                self.inner = inner
                self.acquisitions = 0

            def __enter__(self):
                self.acquisitions += 1
                return self.inner.__enter__()

            def __exit__(self, *exc):
                return self.inner.__exit__(*exc)

        counting = CountingLock(real)
        helper._LOCKS[key] = counting
        try:
            for _ in range(100):
                helper.init(gl, "_thing", factory)
        finally:
            helper._LOCKS[key] = real
        self.assertEqual(counting.acquisitions, 0,
                         "已发布后仍抢锁 %d 次＝热路径被排进全局锁（现网多模态 P95 已 6,259ms）"
                         % counting.acquisitions)
        self.assertEqual(holder["n"], 1, "工厂被跑了 %d 次" % holder["n"])

    def test_lock_is_per_module_and_var(self):
        helper = self._helper()
        gl = {"__name__": "b75_fake_module"}
        other = {"__name__": "b75_other_module"}
        k1 = helper.lock_key(gl, "_a")
        k2 = helper.lock_key(gl, "_b")
        k3 = helper.lock_key(other, "_a")
        self.assertNotEqual(k1, k2, "同模块两枚全局共用一把锁＝互相排队")
        self.assertNotEqual(k1, k3, "不同模块同名全局共用一把锁")

    def test_factory_failure_leaves_nothing_cached(self):
        helper = self._helper()
        gl = {"__name__": "b75_fake_module", "_thing": None}

        def boom():
            raise RuntimeError("factory failure")

        with self.assertRaises(RuntimeError):
            helper.init(gl, "_thing", boom)
        self.assertIsNone(gl["_thing"], "工厂抛错后全局上留了东西＝坏对象被永久缓存")
        ok = helper.init(gl, "_thing", lambda: "good")
        self.assertEqual(ok, "good", "抛错后第二次调用没能重建")


if __name__ == "__main__":
    unittest.main(verbosity=2)
