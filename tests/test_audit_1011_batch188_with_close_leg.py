"""批188（P1-11 剩余腿）：`with <连接提供者> as conn:` 那一形到底关不关连接，加上实例级 `self._conn` 的释放腿。

现读定性（尺子 `workorders/tools/b188_with_close_ruler_1011.py`，22:48:06Z 现读，
`[CALIBRATION] OK 两侧都过`＋`ast_sites=11 textual=11 [DENOMINATOR] OK`）：
批186 收了**模块级** `_conn` 的释放腿（`[RELEASE_LEG] modules=10 无释放腿=0`），但对两形是瞎的：

1. **`with` 形**：sqlite3 连接的上下文管理器只 commit/rollback，**⛔ 关连接**（批186 已钉过
   `WITH_NO_CLOSE` 这条判据，当时只标了 `triggers/audit.py:66` 一枚）。全仓现读 **11 处**：
   `integrations/newapi.py` 5 枚 `with self._connect()`、`triggers/audit.py` 5 枚
   `with self._get_conn()`＋1 枚 `with sqlite3.connect(...)`。**每调一次留一枚没人关的句柄**——
   `audit.record()` 由 `registry.py:155/:175` 在每次触发执行时调用，定时腿每轮一行，
   这不是冷路径。（分布：LEAKY_PROVIDER=10／WITH_NO_CLOSE=1／UNKNOWN=0。）
2. **实例级 `self._conn`**：`bus/inbox.py` 的 `InboxGate`（`:108` 连接经局部名 `c` 再挂 `:134`）
   与 `presence/store.py` 的 `PresenceStore`（`:28` 直挂）——`[INSTANCE_NOLEG] 2`。
   两枚都是进程级单例（`app.py:568 rt.inbox`／`app.py:450 rt.presence_store`），批186 的关停循环
   只吃模块级那八枚，这两枚没人收。

修法定在**提供者一侧**而不是 11 个调用点各塞一句 `close()`：`_connect()`／`_get_conn()` 改成
`@contextmanager`＋`finally: conn.close()`，调用点的 `with … as conn:` 语法一字不动就同时拿到
「提交」与「关连接」；`audit.py:66` 直挂形用 `contextlib.closing(...)` 包。
判据钉的是**行为**（退出 with 后句柄必须真的关了：`sqlite3.ProgrammingError`），⛔ 只钉形状。

⚠ 尺子有两份定义：本门自带一份（`_site_kinds`/`_instance_legs`，纯 AST），
`workorders/tools/b188_with_close_ruler_1011.py` 是 PM 侧量分母那份——`workorders/` 从不入 git
（台账 §「读数件只落盘」），所以⛔ 让门去 import 它。两份同参数同判据，本批两把尺同日同读数
（11／11／UNKNOWN=0／无腿=2）。
"""
from __future__ import annotations

import ast
import collections
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

CELLS = 14
SITE_PINS = {"butler/integrations/newapi.py": 5, "butler/triggers/audit.py": 6}
INSTANCE_PINS = {"butler/presence/store.py": "PresenceStore", "butler/bus/inbox.py": "InboxGate"}
CONNECT_OK = {"CLOSING_WRAPPER", "CLOSING_PROVIDER"}


def _text(rel: str) -> str:
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _dotted(node) -> str:
    return ast.unparse(node) if node is not None else ""


def _is_connect(call) -> bool:
    return isinstance(call, ast.Call) and _dotted(call.func).startswith("sqlite3.connect")


def _providers(tree):
    """提供者名 → 是否自带关闭腿（@contextmanager 且 finally 里 .close）。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not any(_is_connect(sub) for sub in ast.walk(node)):
                continue
            closes = (any("contextmanager" in _dotted(d) for d in node.decorator_list)
                      and any(isinstance(sub, ast.Try) and sub.finalbody
                              and any(isinstance(c, ast.Call) and _dotted(c.func).endswith(".close")
                                      for line in sub.finalbody for c in ast.walk(line))
                              for sub in ast.walk(node)))
            prev = out.get(node.name)
            out[node.name] = closes if prev is None else (prev and closes)
    return out


def _site_kinds(src: str) -> list:
    out = []
    tree = ast.parse(src)
    providers = _providers(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if not isinstance(call, ast.Call):
                continue
            name = _dotted(call.func)
            if name.rsplit(".", 1)[-1] == "closing":
                kind = "CLOSING_WRAPPER"
            elif _is_connect(call):
                kind = "WITH_NO_CLOSE"
            elif name.rsplit(".", 1)[-1] in providers:
                kind = "CLOSING_PROVIDER" if providers[name.rsplit(".", 1)[-1]] else "LEAKY_PROVIDER"
            else:
                leaf = name.rsplit(".", 1)[-1]
                if not (leaf.startswith("connect") or leaf.endswith("_conn")):
                    continue
                kind = "UNKNOWN"
            out.append((call.lineno, kind))
    return out


def _instance_legs(src: str) -> list:
    """类里把 `sqlite3.connect` 挂到 `self.<attr>` 的（含经局部名转手那一形），有没有 `.close()` 腿。"""
    rows = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.ClassDef):
            continue
        holds, legs = set(), set()
        for meth in [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            locals_of_connect = {t.id for sub in ast.walk(meth) if isinstance(sub, ast.Assign)
                                 and _is_connect(sub.value) for t in sub.targets if isinstance(t, ast.Name)}
            for sub in ast.walk(meth):
                if isinstance(sub, ast.Assign):
                    for tgt in sub.targets:
                        holder = _dotted(tgt)
                        if holder.startswith("self.") and (
                                _is_connect(sub.value) or
                                (isinstance(sub.value, ast.Name) and sub.value.id in locals_of_connect)):
                            holds.add(holder)
                if meth.name != "__init__" and isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                        and sub.func.attr == "close" and _dotted(sub.func.value).startswith("self."):
                    legs.add(_dotted(sub.func.value))
        if holds:
            rows.append({"class": node.name, "leg": holds <= legs,
                         "held": sorted(holds), "closed": sorted(legs)})
    return rows


_SYNTH_PASS = '''
import contextlib
import sqlite3
from contextlib import contextmanager


class Box:
    def __init__(self, p):
        self.db_path = p
        self._conn = None

    def _open(self):
        self._conn = sqlite3.connect(self.db_path)
        return self._conn

    @contextmanager
    def cm(self):
        c = sqlite3.connect(self.db_path)
        try:
            yield c
        finally:
            c.close()

    def close_conn(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def use(self):
        with self.cm() as conn:
            conn.execute("SELECT 1")
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("SELECT 1")
'''

_SYNTH_LEAK = '''
import sqlite3


class Box:
    def __init__(self, p):
        self.db_path = p
        self._conn = None

    def _lazy(self):
        if self._conn is None:
            c = sqlite3.connect(self.db_path)
            self._conn = c
        return self._conn

    def use(self):
        with self._lazy() as conn:
            conn.execute("SELECT 1")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("SELECT 1")
'''


class TestBatch188WithCloseLeg(unittest.TestCase):
    # ---------- 自证与校准 ----------

    def test_00_canary_assertion_mechanism_bites(self):
        class _Canary(unittest.TestCase):
            def test_deliberately_wrong(self):
                self.assertEqual(2, 3)

        res = unittest.TestResult()
        unittest.TestLoader().loadTestsFromTestCase(_Canary).run(res)
        self.assertEqual(len(res.failures), 1, "canary 没红：本文件断言机制不可信，整卡不得验收")
        self.assertEqual(res.testsRun, 1)

    def test_01_ast_anchor_fourteen_sync_cases(self):
        tree = ast.parse(_text(os.path.join("tests", os.path.basename(__file__))))
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name.startswith("TestBatch188")]
        sync = [m.name for c in classes for m in c.body
                if isinstance(m, ast.FunctionDef) and m.name.startswith("test_")]
        bad = [m.name for c in classes for m in c.body
               if isinstance(m, ast.AsyncFunctionDef) and m.name.startswith("test_")]
        module_defs = [n.name for n in tree.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name.startswith("test_")]
        self.assertEqual(len(sync), CELLS, f"锚点漂移：本门应现读 {CELLS} 格，现为 {len(sync)}")
        self.assertEqual(bad, [], f"本门⛔ 协程判据格（会静默判 PASS）：{bad}")
        self.assertEqual(module_defs, [], "本门⛔ 模块级 test_*（discover 会静默收下）")

    def test_02_shape_ruler_is_calibrated_both_ways(self):
        pass_kinds = collections.Counter(k for _l, k in _site_kinds(_SYNTH_PASS))
        leak_kinds = collections.Counter(k for _l, k in _site_kinds(_SYNTH_LEAK))
        self.assertEqual(pass_kinds["CLOSING_PROVIDER"], 1, "尺子放行了 CM 提供者却数不到它")
        self.assertEqual(pass_kinds["CLOSING_WRAPPER"], 1, "尺子看不见 closing(...) 包")
        self.assertEqual(pass_kinds["WITH_NO_CLOSE"] + pass_kinds["LEAKY_PROVIDER"], 0,
                         "尺子咬了自带关闭腿的合成件⇒ 假红")
        self.assertEqual(leak_kinds["LEAKY_PROVIDER"], 1, "尺子放行了『提供者只 return 连接』⇒ 假绿")
        self.assertEqual(leak_kinds["WITH_NO_CLOSE"], 1, "尺子放行了『直挂 with sqlite3.connect』⇒ 假绿")
        ok_legs = _instance_legs(_SYNTH_PASS)
        leak_legs = _instance_legs(_SYNTH_LEAK)
        self.assertTrue(ok_legs and all(r["leg"] for r in ok_legs),
                        f"放行件应判『有实例级腿』：{ok_legs}")
        self.assertTrue(leak_legs and not any(r["leg"] for r in leak_legs),
                        f"咬住件（`self._conn = c` 经局部名转手且无 close）应判『无腿』：{leak_legs}")

    # ---------- 分母：站点没被删，只是每条多了关闭腿 ----------

    def test_03_with_site_denominator_is_pinned(self):
        rows, unknown = {}, 0
        for rel in SITE_PINS:
            kinds = _site_kinds(_text(rel))
            rows[rel] = len(kinds)
            unknown += sum(1 for _l, k in kinds if k == "UNKNOWN")
        self.assertEqual(unknown, 0, "有 with 站点的提供者读不出形⇒ 尺子不配放行")
        self.assertEqual(rows, SITE_PINS, f"with 站点分母漂移（修形⛔ 删站点）：{rows}")
        self.assertEqual(sum(rows.values()), 11, f"全批钉的是 11 处：{rows}")

    def test_11_no_provider_is_taken_bare_outside_a_with(self):
        """每个 `_connect()`／`_get_conn()` 出现点都必须在 with 语句里——否则 CM 化会打断裸拿。"""
        bare = []
        for rel in SITE_PINS:
            tree = ast.parse(_text(rel))
            in_with = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.With):
                    for item in node.items:
                        if isinstance(item.context_expr, ast.Call):
                            in_with.add(item.context_expr.lineno)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _dotted(node.func).rsplit(".", 1)[-1] in (
                        "_connect", "_get_conn"):
                    if node.lineno not in in_with:
                        bare.append(f"{rel}:{node.lineno}")
        self.assertEqual(bare, [], f"这些站点是裸拿连接，CM 化会把它们改坏：{bare}")

    # ---------- 行为腿：退出 with 后句柄必须真关了 ----------

    def test_04_newapi_connect_cm_closes_the_handle(self):
        from butler.integrations.newapi import NewApiClient
        tmp = tempfile.mkdtemp(prefix="b188_")
        try:
            db = os.path.join(tmp, "new-api.db")
            sqlite3.connect(db).close()
            c = NewApiClient(db)
            with c._connect() as conn:
                self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")     # 句柄没关就不会抛这一句
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_05_audit_get_conn_cm_closes_the_handle(self):
        from butler.triggers.audit import TriggerAuditor
        tmp = tempfile.mkdtemp(prefix="b188_")
        try:
            a = TriggerAuditor(os.path.join(tmp, "push_guard.db"))
            with a._get_conn() as conn:
                self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_07_every_with_site_now_has_a_close_leg(self):
        leaky = []
        for rel in SITE_PINS:
            for line, kind in _site_kinds(_text(rel)):
                if kind not in CONNECT_OK:
                    leaky.append(f"{rel}:{line} {kind}")
        self.assertEqual(leaky, [], "这些 with 站点退出时仍⛔ 关连接（只提交）：\n" + "\n".join(leaky))

    def test_12_no_direct_with_sqlite3_connect_left(self):
        rows = []
        for rel in SITE_PINS:
            for line, kind in _site_kinds(_text(rel)):
                if kind == "WITH_NO_CLOSE":
                    rows.append(f"{rel}:{line}")
        self.assertEqual(rows, [], "`with sqlite3.connect(...) as conn:` 直挂形必须收进 closing(...)："
                                   "\n".join(rows))

    # ---------- 实例级释放腿 ----------

    def test_08_presence_store_has_instance_release_leg_and_rebuilds(self):
        from butler.presence.store import PresenceStore
        tmp = tempfile.mkdtemp(prefix="b188_")
        try:
            s = PresenceStore(tmp)
            conn = s._get_conn()
            self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
            s.close_conn()
            self.assertIsNone(s._conn, "close_conn 只关了句柄没清引用⇒ 重建腿拿不回连接")
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
            c2 = s._get_conn()
            self.assertEqual(c2.execute("SELECT 1").fetchone()[0], 1)
            s.close_conn()          # 本性格子自己不留句柄（第二次调用＝顺带证幂等）
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_09_inbox_gate_has_instance_release_leg_and_rebuilds(self):
        import butler.config as config_mod
        from butler.bus.inbox import InboxGate
        tmp = tempfile.mkdtemp(prefix="b188_")
        old = config_mod._settings
        config_mod._settings = types.SimpleNamespace(data_dir=tmp)
        try:
            gate = InboxGate()
            conn = gate._db()
            self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
            gate.close_conn()
            self.assertIsNone(gate._conn, "close_conn 只关了句柄没清引用")
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
            c2 = gate._db()
            self.assertEqual(c2.execute("SELECT 1").fetchone()[0], 1)
            gate.close_conn()       # 本性格子自己不留句柄（第二次调用＝顺带证幂等）
        finally:
            config_mod._settings = old
            shutil.rmtree(tmp, ignore_errors=True)

    def test_06_instance_holders_are_two_and_both_have_a_leg(self):
        """实例级那一形的分母＋腿：持连接的类仍恰是那两枚（⛔ 删掉持有者来蒙过行为格），且都有 `.close()` 腿。"""
        rows = []
        for rel, cls in INSTANCE_PINS.items():
            for r in _instance_legs(_text(rel)):
                if r["class"] == cls:
                    rows.append((f"{rel}:{r['class']}", r["leg"], r["held"], r["closed"]))
        self.assertEqual(len(rows), 2, f"实例级持有者分母漂移（现读应 2 枚）：{[r[0] for r in rows]}")
        noleg = [f"{name} held={held} closed={closed}" for name, leg, held, closed in rows if not leg]
        self.assertEqual(noleg, [], "这些实例级 `self._conn` 没有释放腿：\n" + "\n".join(noleg))

    def test_10_shutdown_block_releases_the_instance_stores(self):
        src = _text("butler/app.py")
        start = src.index("批186·P1-11")
        end = src.index("app = Starlette(")
        block = src[start:end]
        missing = [leg for leg in ("presence_store", "inbox", "close_conn") if leg not in block]
        self.assertEqual(missing, [], f"关停循环没接上实例级两枚 store（缺 {missing}）——"
                                      f"批186 那次只收了模块级八枚")

    def test_10b_write_failures_release_leg_is_wired(self):
        src = _text("butler/app.py")
        block = src[src.index("批186·P1-11"):src.index("app = Starlette(")]
        self.assertIn("write_failures", block,
                      "`store/write_failures.py` 的 `_reset_conn` 是现成的释放腿却没接进关停")


if __name__ == "__main__":
    unittest.main()
