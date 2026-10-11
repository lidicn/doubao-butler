"""批201 DB35 门：DB 里一行坏 JSON 无降级 ⇒ 整条链路瘫痪（第三期审计报告 第九轮 · high）。

现读四处（同一条病，四条腿）：
  `butler/store/repo.py:73`      `[set(json.loads(r["bigrams"])) for r in rows]`——列表推导⛔ 兜底，
                                 一行坏数据让 `dedup.is_duplicate` 抛穿，`dialog.speak` 全线哑火；
  `butler/core/decision_store.py:175` `json.loads(d["options"])` 在 for 循环里裸着 ⇒ 一条坏决策拖死整页；
  `butler/presence/store.py:98`  `json.loads(row[3])` 同上；
  `butler/core/dialog.py:706`    `await self.dedup.is_duplicate(...)` 没有任何读失败兜底。

口径（挂批162/批70b 那条「读不出来≠没有」的收敛尺子，⛔ 各站点再自己 try/except）：
  新增单点 `butler/core/atomic_json.py:loads_column(raw, default, *, site)`——
  空值／None＝**真没有**⇒静默返回 default；有值但解析失败＝**读不出来**⇒返回 default 并留痕
  （⛔ 把列值本身写进日志，只报 site＋异常类名＋长度）。
  四腿一律改成「坏这一条降级，其余照旧」，⛔ 让一条脏行拖死整批。

极性说明（请追认）：`dialog.speak` 那条在去重闸读不出来时**放行发声**，⛔ 判成重复而静默——
本项目台账里「消息只是不见了」反复被判定为更坏的失败方向（最坏的那次是 webhook 角色分叉）。
"""
import ast
import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

from butler.core import atomic_json

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_PY = ROOT / "butler" / "store" / "repo.py"
DECISION_PY = ROOT / "butler" / "core" / "decision_store.py"
PRESENCE_PY = ROOT / "butler" / "presence" / "store.py"
DIALOG_PY = ROOT / "butler" / "core" / "dialog.py"


def unwguarded_dedup_calls(src: str) -> list[str]:
    """返回「调用 `is_duplicate` 却没被 try 包住」的函数名（按 AST 归属，⛔ 数行数）。"""
    bad = []
    for fn in [n for n in ast.walk(ast.parse(src))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        guarded = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Try):
                guarded.update(id(sub) for sub in ast.walk(node))
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", "") == "is_duplicate"
                    and id(node) not in guarded):
                bad.append(fn.name)
    return bad


class _LogSpy:
    """把 atomic_json 的 logger 换成记录仪：降级必须留痕，⛔ 痕里带列值。"""

    def __init__(self):
        self.warnings = []

    def warning(self, fmt, *args):
        self.warnings.append(str(fmt) % args if args else str(fmt))

    def __getattr__(self, name):
        def _noop(*a, **k):
            return None
        return _noop


class TestBatch201Db35LoadsColumn(unittest.TestCase):

    def setUp(self):
        self.spy = _LogSpy()
        patcher = mock.patch.object(atomic_json, "logger", self.spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_01_helper_exists_at_the_single_point(self):
        self.assertTrue(hasattr(atomic_json, "loads_column"),
                        "单点判据不存在：四腿又会各写各的 try")

    def test_02_bad_column_degrades_with_a_trace(self):
        got = atomic_json.loads_column("{not json", ["fallback"], site="t.options")
        self.assertEqual(got, ["fallback"])
        self.assertEqual(len(self.spy.warnings), 1, "读不出来必须留痕")

    def test_03_trace_never_carries_the_value(self):
        secret = '{"token": "super-secret-value"}{'
        atomic_json.loads_column(secret, {}, site="t.x")
        self.assertTrue(self.spy.warnings)
        for w in self.spy.warnings:
            self.assertNotIn("super-secret-value", w, "降级留痕⛔ 把列值本身写进日志")

    def test_04_blank_is_really_absent_and_stays_quiet(self):
        """「读不出来」≠「没有」：空串／None 是真没有，⛔ 喊成故障（批162 同口径）。"""
        for blank in (None, "", "   "):
            with self.subTest(blank=blank):
                self.assertEqual(atomic_json.loads_column(blank, [], site="t.x"), [])
        self.assertEqual(self.spy.warnings, [], "空值不该留痕")

    def test_05_valid_json_parses(self):
        self.assertEqual(atomic_json.loads_column('["a", "b"]', None, site="t.x"), ["a", "b"])


class TestBatch201Db35ReadSites(unittest.TestCase):
    """三枚读腿逐枚验：坏一行只降级这一行，其余照常返回。"""

    def _conn(self, ddl: str) -> sqlite3.Connection:
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript(ddl)
        return c

    def test_06_recent_fingerprints_skips_only_the_bad_row(self):
        from butler.store import repo
        c = self._conn("CREATE TABLE dedup_fingerprints (id INTEGER PRIMARY KEY, ts REAL,"
                       " member TEXT, text_hash TEXT, bigrams TEXT);")
        c.execute("INSERT INTO dedup_fingerprints (ts, member, text_hash, bigrams)"
                  " VALUES (1000, 'm', 'h1', ?)", (json.dumps(["ab", "bc"]),))
        c.execute("INSERT INTO dedup_fingerprints (ts, member, text_hash, bigrams)"
                  " VALUES (1001, 'm', 'h2', ?)", ('["broken"',))
        c.execute("INSERT INTO dedup_fingerprints (ts, member, text_hash, bigrams)"
                  " VALUES (1002, 'm', 'h3', ?)", (json.dumps(["cd"]),))
        c.commit()
        with mock.patch.object(repo, "get_conn", return_value=c):
            got = repo.recent_fingerprints("m", 999)
        self.assertEqual(len(got), 2, f"坏一行拖死了整批：{got}")
        self.assertIn({"ab", "bc"}, [set(g) for g in got])

    def test_07_list_decisions_survives_a_bad_options_row(self):
        from butler.core import decision_store
        c = self._conn("CREATE TABLE decisions (id TEXT PRIMARY KEY, title TEXT,"
                       " description TEXT, options TEXT, status TEXT, created_at REAL,"
                       " resolved_at REAL, resolution TEXT, result_option INTEGER);")
        good = ("d1", "标题一", "", json.dumps(["A", "B"]), "待投票", 1.0)
        bad = ("d2", "标题二", "", '["half', "待投票", 2.0)
        c.executemany("INSERT INTO decisions (id, title, description, options, status, created_at)"
                      " VALUES (?,?,?,?,?,?)", [good, bad])
        c.commit()
        with mock.patch.object(decision_store, "get_conn", return_value=c):
            rows = decision_store.list_decisions(limit=10)
        self.assertEqual(len(rows), 2, f"一条坏决策让整页消失：{len(rows)}")
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["d1"]["options"], ["A", "B"])
        self.assertEqual(by_id["d2"]["options"], [], "坏行应降级成空列表")

    def test_08_presence_history_survives_a_bad_sources_row(self):
        from butler.presence.store import PresenceStore
        tmp = tempfile.mkdtemp(prefix="butler_test_batch201_db35_")
        store = PresenceStore(tmp)
        c = store._get_conn()
        c.execute("INSERT INTO presence_history (user_id, room, confidence, sources, ts)"
                  " VALUES ('u', '客厅', 0.9, ?, 1.0)", (json.dumps(["face"]),))
        c.execute("INSERT INTO presence_history (user_id, room, confidence, sources, ts)"
                  " VALUES ('u', '卧室', 0.8, ?, 2.0)", ('["broken',))
        c.commit()
        try:
            rows = store.get_history("u", limit=10)
        finally:
            store.close_conn()
        self.assertEqual(len(rows), 2, "一条坏 sources 让历史整段消失")
        # get_history 是 ORDER BY ts DESC⇒按房间取，⛔ 按行下标（上一版按 [1] 断言把好的那条读成了坏的）
        by_room = {r["room"]: r for r in rows}
        self.assertEqual(by_room["卧室"]["sources"], [], "坏行应降级成空列表")
        self.assertEqual(by_room["客厅"]["sources"], ["face"], "坏行⛔ 拖死隔壁那条好记录")


class TestBatch201Db35DedupGuardPolarity(unittest.TestCase):

    def test_09_dialog_speak_dedup_call_is_guarded(self):
        src = DIALOG_PY.read_text(encoding="utf-8")
        self.assertEqual(unwguarded_dedup_calls(src), [],
                         "去重闸裸着＝闸一坏，主动发声整条链一起哑")

    def test_10_shape_ruler_bites_synthetic(self):
        bare = ('async def speak(self):\n'
                '    dup, sim = await self.dedup.is_duplicate("m", "t")\n'
                '    return dup\n')
        self.assertEqual(unwguarded_dedup_calls(bare), ["speak"],
                         "尺子咬不住裸调用⇒test_09 是摆设")
        guarded = ('async def speak(self):\n'
                   '    try:\n'
                   '        dup, sim = await self.dedup.is_duplicate("m", "t")\n'
                   '    except Exception:\n'
                   '        dup, sim = False, 0.0\n'
                   '    return dup\n')
        self.assertEqual(unwguarded_dedup_calls(guarded), [])

    def test_11_three_read_sites_use_the_single_point(self):
        """四腿⛔ 再各写各的 try：读列的降级口径必须只有 `loads_column` 一处定义。"""
        for rel, path in (("repo.py", REPO_PY), ("decision_store.py", DECISION_PY),
                          ("presence/store.py", PRESENCE_PY)):
            with self.subTest(site=rel):
                src = path.read_text(encoding="utf-8")
                # assertIn 失败会把整份源码打进读数件⇒红证没法看，自己写短消息
                if "loads_column" not in src:
                    self.fail(f"{rel} 的 JSON 列读法没挂上单点")


if __name__ == "__main__":
    unittest.main()
