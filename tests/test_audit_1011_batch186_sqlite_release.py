"""批186（P1-11 一条腿）：八枚进程级 sqlite 缓存连接必须有释放腿。

现读定性（`workorders/tools/b186_sqlite_close_ruler_1011.py`，21:28:44Z：站点定性六腿＋释放腿三腿
校准 OK、`ast_sites=18 textual=18` MISSING=0、`[RELEASE_LEG] modules=10 无释放腿=8`）：
18 处 `sqlite3.connect` 站点里 10 枚模块把连接挂在模块全局 `_conn` 上长期持有，
**只有 2 枚有释放腿**（`store/db.py:532 close()`、`store/write_failures.py:56 _reset_conn`），
其余 8 枚⛔ 释放——于是：
  · 测试在临时目录里造完连接没法关 ⇒ `tests/test_decision_engine.py` 现读
    `PermissionError [WinError 32]` + `ResourceWarning: unclosed database`（21:04:50Z）；
  · 生产侧换 DATA_DIR／重建时旧句柄只能等 GC，WAL 文件被占着；
  · `app.py:875` 的关停钩子只关 `store.db` 那一枚，其余 8 枚关不掉。

⚠ 重定性（本报告原先的形状判据把它读窄了）：P1-11 的真缺口**⛔「函数内少写一句 close」**——
那 12 枚 `CLOSED_NOFINALLY` 里大部分是**故意的长驻单例**，异常路径不关才是对的；
缺的是「把这枚进程级缓存释放掉」的 API 本身。`with sqlite3.connect(...) as conn:` 也不算释放腿：
那枚上下文管理器只 commit/rollback，⛔ 关连接（`triggers/audit.py:66` 就是这一形）。

判据分母：顶层 10 格＝1 canary＋1 锚点＋1 盘面溯源＋4 释放行为＋1 释放后重建＋2 关停腿
（形状尺校准＋真座关停）。批186 中途把锚点从 6 改到 8、再加关停腿到 10——两次都是**先加格再改锚**，
⛔ 反过来迁就代码。

实现取向：8 枚站点的 `get_conn` 已全部收敛到 `butler.core.singleton`（批75），释放腿因此
**同样⛔ 在 8 个站点各写一遍**＝`close_conn()` 薄薄一层，真身在 `singleton.release()`，
与 `init` 共用同一条（模块, 全局名）锁；`singleton.release` 的两腿在
`tests/test_audit_1008_batch75_singleton_race.py::SingletonHelperTest`。

判据格全为 `unittest.TestCase` 同步方法（⛔ 协程进 TestCase＝收到但从不 await，静默判 PASS）。
"""
from __future__ import annotations

import ast
import gc
import os
import sys
import tempfile
import types
import unittest
import warnings
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import butler.config as config_mod  # noqa: E402
from butler.core import decision_store, notification_store, pwa_chat_store, user_location  # noqa: E402
from butler.ha_tools import whitelist  # noqa: E402
from butler.proactive import engine as proactive_engine  # noqa: E402
from butler.timeseries import store as timeseries_store  # noqa: E402
from butler.store import task_store  # noqa: E402

STORES = [
    ("butler.core.decision_store", decision_store),
    ("butler.core.notification_store", notification_store),
    ("butler.core.pwa_chat_store", pwa_chat_store),
    ("butler.core.user_location", user_location),
    ("butler.ha_tools.whitelist", whitelist),
    ("butler.proactive.engine", proactive_engine),
    ("butler.timeseries.store", timeseries_store),
    ("butler.store.task_store", task_store),
]


def _shutdown_release_calls(src: str) -> list:
    """形状尺：`app.py` 关停腿（那枚 `from butler.store.db import close` 的函数体）里，
    除了 `store.db` 那一枚之外，还有没有对其它站点调 `close_conn`。

    返回被调用的表达式列表；空＝关停只关了一枚连接，其余 8 条句柄等 OS 回收。
    """
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(isinstance(n, ast.ImportFrom) and n.module == "butler.store.db"
                   and any(a.name == "close" for a in n.names) for n in ast.walk(fn)):
            continue
        return [ast.unparse(n.func) for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "close_conn"]
    return []


APP_PY = os.path.join(REPO_ROOT, "butler", "app.py")

# 两侧校准（形状尺⛔ 只跑真文件）：同一把尺跑合成源码，应放行⇒非空、应咬⇒空。
_SYNTH_RELEASE = '''
async def lifespan(rt):
    try:
        yield
    finally:
        from butler.store.db import close
        close()
        from butler.core import decision_store
        for _m in (decision_store,):
            _m.close_conn()
'''
_SYNTH_NO_RELEASE = '''
async def lifespan(rt):
    try:
        yield
    finally:
        from butler.store.db import close
        close()
'''


class TestBatch186StoreRelease(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b186_")
        self._saved_settings = config_mod._settings
        config_mod._settings = types.SimpleNamespace(data_dir=self.tmp)
        for _, mod in STORES:
            mod._conn = None

    def tearDown(self):
        for _, mod in STORES:
            c = getattr(mod, "_conn", None)
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
            mod._conn = None
        config_mod._settings = self._saved_settings
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 自证与锚点 ----------

    def test_00_canary_assertion_mechanism_bites(self):
        class _Canary(unittest.TestCase):
            def test_deliberately_wrong(self):
                self.assertEqual(2, 3)

        res = unittest.TestResult()
        unittest.TestLoader().loadTestsFromTestCase(_Canary).run(res)
        self.assertEqual(len(res.failures), 1, "canary 没红：本文件断言机制不可信，整卡不得验收")
        self.assertEqual(res.testsRun, 1)

    def test_01_ast_anchor_ten_sync_cases(self):
        """分母自锚：顶层 Test* 类体内 `test_` 方法＝10，且全同步。

        只数**模块顶层**的类：`test_00` 里那枚嵌套 canary（`_Canary.test_deliberately_wrong`）
        不是分母的一部分——`ast.walk` 全树会把它算进来，锚点因此虚高 1（09-21 那把尺犯过的形状）。
        """
        tree = ast.parse(Path(os.path.abspath(__file__)).read_text(encoding="utf-8"))
        methods = [n.name for c in tree.body if isinstance(c, ast.ClassDef)
                   and c.name.startswith("Test")
                   for n in c.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name.startswith("test_")]
        asyncish = [n.name for c in tree.body if isinstance(c, ast.ClassDef)
                    for n in c.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name.startswith("test_")]
        self.assertEqual(asyncish, [], f"async test_ 会静默判 PASS：{asyncish}")
        self.assertEqual(len(methods), 10, f"锚点漂移：顶层 Test* 类体内现读 {len(methods)} 格，"
                                          "增删格必须同步改本判据与台账读数")

    def test_02_stores_are_the_disk_face(self):
        for name, mod in STORES:
            f = os.path.realpath(mod.__file__)
            self.assertTrue(f.startswith(REPO_ROOT + os.sep), f"{name} 不在仓内：{f}")
            self.assertEqual(os.path.basename(f), name.split(".")[-1] + ".py",
                             f"{name} 文件名与模块名不符：{f}")

    # ---------- 判据腿 ----------

    def test_03_every_store_exposes_close_conn(self):
        missing = [name for name, mod in STORES if not callable(getattr(mod, "close_conn", None))]
        self.assertEqual(missing, [],
                         f"缺释放腿的模块：{missing}（八枚都该有 close_conn，形制抄 store/db.py:532）")

    def test_04_close_conn_releases_the_global(self):
        not_released = []
        for name, mod in STORES:
            if not callable(getattr(mod, "close_conn", None)):
                not_released.append(f"{name}:无 close_conn")
                continue
            mod.get_conn()
            if mod._conn is None:
                not_released.append(f"{name}:get_conn 没挂上 _conn")
                continue
            mod.close_conn()
            if mod._conn is not None:
                not_released.append(f"{name}:close_conn 后 _conn 仍在位")
        self.assertEqual(not_released, [], f"释放腿没起作用：{not_released}")

    def test_05_close_conn_is_idempotent_and_needs_no_handle(self):
        errors = []
        for name, mod in STORES:
            if not callable(getattr(mod, "close_conn", None)):
                errors.append(f"{name}:无 close_conn")
                continue
            try:
                mod.close_conn()          # _conn 本来就是 None
                mod.get_conn()
                mod.close_conn()
                mod.close_conn()          # 第二次⛔ 抛
            except Exception as e:
                errors.append(f"{name}:{type(e).__name__}: {e}")
        self.assertEqual(errors, [], f"释放腿不幂等或空句柄即抛：{errors}")

    def test_06_no_unclosed_database_warning_after_release(self):
        leaked = []
        for name, mod in STORES:
            if not callable(getattr(mod, "close_conn", None)):
                leaked.append(f"{name}:无 close_conn")
                continue
            c = mod.get_conn()
            mod.close_conn()
            del c
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                gc.collect()
            hits = [str(w.message) for w in caught
                    if issubclass(w.category, ResourceWarning) and "unclosed database" in str(w.message)]
            if hits:
                leaked.append(f"{name}:{hits[0]}")
        self.assertEqual(leaked, [], f"释放后仍有活句柄被 GC 检出：{leaked}")

    def test_07_get_conn_rebuilds_after_release(self):
        """释放之后必须能重建：否则 `close_conn` 把站点打死，重启窗口内第一次调用就⛔ 连接。"""
        broken = []
        for name, mod in STORES:
            if not callable(getattr(mod, "close_conn", None)):
                broken.append(f"{name}:无 close_conn")
                continue
            first = mod.get_conn()
            mod.close_conn()
            second = mod.get_conn()
            if second is first:
                broken.append(f"{name}:释放后拿回的仍是那条已关的连接")
            if mod._conn is not second:
                broken.append(f"{name}:重建的连接没回落到全局")
            try:
                second.execute("select 1").fetchone()
            except Exception as e:
                broken.append(f"{name}:重建后不可用 {type(e).__name__}: {e}")
            mod.close_conn()
        self.assertEqual(broken, [], f"释放腿之后重建不成立：{broken}")

    def test_08_shape_ruler_is_calibrated_both_ways(self):
        """尺子先自证：同一把尺跑合成源码，应放行⇒非空、应咬⇒恰好空。"""
        self.assertEqual(_shutdown_release_calls(_SYNTH_RELEASE), ["_m.close_conn"],
                         "放行腿被咬＝这把尺会把好码读成坏码")
        self.assertEqual(_shutdown_release_calls(_SYNTH_NO_RELEASE), [],
                         "咬住腿没咬＝这把尺永远绿，配不当闸")

    def test_09_shutdown_hook_releases_every_store(self):
        """生产腿：关停钩子⛔ 只关 `store.db` 一枚，八枚 `close_conn` 必须被接线。

        释放 API 造出来却 0 调用者＝D17 那把「护栏从未接线」的同一形状。
        ⛔ import `butler/app.py`（二次 import＝在停摆库上开连接），只读源码走形状尺。
        """
        with open(APP_PY, encoding="utf-8") as f:
            calls = _shutdown_release_calls(f.read())
        self.assertNotEqual(calls, [],
                            "app.py 关停腿里没有任何 close_conn 调用＝八枚站点关停时不收连接")


if __name__ == "__main__":
    unittest.main()
