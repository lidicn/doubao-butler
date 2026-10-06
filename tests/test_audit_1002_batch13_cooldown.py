"""批13（DCD 裁定 20261002 跟办 2）：触发冷却的持久化从 JSON 收口进 SQLite。

裁定原文两层：
  · `decisions/20260928-豆包管家15项决策.md:50`＝「triggers 冷却用 JSON 文件，其他用 SQLite。
    裁定：统一进 SQLite。」
  · `decisions/20261002-DB六件影子代码-裁定.md:107`＝「确认是**欠执行不是待裁**，排进下一批」。
所以本批⛔ 再问「要不要做」，只钉「做成什么样」：
  1. 新表 `trigger_cooldowns(cd_key TEXT PRIMARY KEY, last_fired REAL)`，由真启动路径 `_init` 建（⛔ 我在测试里手搓 CREATE）；
  2. engine 的读写腿走 `repo.load_cooldowns/save_cooldowns`，JSON 那条**只准读一次**（迁移），读完且验过才退役；
  3. 写库失败＝响亮＋进 `db_write_failures` 台账，但进程内 `_last_fired` 照常（旧码就是这个 fail-open 形状，⛔ 顺手改成崩）；
  4. 七天窗（旧 `_load_cooldowns` 的 `86400*7`）语义原样搬，⛔ 悄悄改宽改窄。

运行时腿按跟办 3（`:108`）＝**真临时 sqlite**（DATA_DIR 钉 tmp），⛔ 碰现网库、⛔ mock 掉我要验的那层。
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import pathlib
import shutil
import tempfile
import time
import unittest

import butler.config as config
from butler.config import Settings
from butler.store import db as db_mod, repo, write_failures
from butler.triggers.engine import TriggerEngine

TREE = pathlib.Path(__file__).resolve().parents[1]
ENGINE_PY = TREE / "butler" / "triggers" / "engine.py"
REPO_PY = TREE / "butler" / "store" / "repo.py"
DB_PY = TREE / "butler" / "store" / "db.py"
SNAPSHOT_NAME = "trigger_cooldowns.json"
COLUMNS = ["cd_key", "last_fired"]


def make_trigger(**kw):
    t = {"id": "trig_b13", "name": "批13 冷却", "event": "person_enter",
         "enabled": True, "priority": 50, "conditions": {}, "exclusive": True,
         "cooldown_sec": 300, "resources": [],
         "actions": [{"skill": "s1", "params": {}}]}
    t.update(kw)
    return t


class FakeStore:
    def __init__(self, trigs):
        self._t = [dict(x) for x in trigs]

    def list(self):
        return [dict(x) for x in self._t]


class FakeRunner:
    def __init__(self):
        self.calls = []

    async def run(self, skill_id, source="api", dry_run=False, payload=None, force=False):
        self.calls.append({"skill_id": skill_id, "source": source, "dry_run": dry_run})
        return {"ok": True, "status": "ok"}


class FakeRT:
    """引擎只从 rt 上取一样东西：data_dir（旧码用来拼 JSON，新码只用它找迁移快照）。"""

    def __init__(self, runner, data_dir):
        self.runner = runner
        self.data_dir = data_dir


def rows_of(table="trigger_cooldowns"):
    c = db_mod.get_conn()
    return {r["cd_key"]: float(r["last_fired"])
            for r in c.execute("SELECT cd_key, last_fired FROM %s" % table)}


def fire(eng, event="person_enter", member="m", dry_run=False):
    return asyncio.run(eng.handle_event(event, {"member": member, "room": "客厅"},
                                        dry_run=dry_run))


class DbScaffold(unittest.TestCase):
    """真临时库：`config._settings` 换 tmp 目录 + `db_mod._conn = None` ⇒ get_conn 现开。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="butler_b13_")
        self.saved_settings = config._settings
        config._settings = Settings(data_dir=self.tmp)
        db_mod._conn = None
        self.c = db_mod.get_conn()

    def tearDown(self):
        db_mod._conn = None
        config._settings = self.saved_settings
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build_engine(self, trig=None, runner=None):
        eng = TriggerEngine(FakeStore([trig or make_trigger()]))
        self.runner = runner or FakeRunner()
        eng.rt = FakeRT(self.runner, self.tmp)
        return eng

    def boot(self, eng=None):
        """走真启动路径 `set_runtime`（旧码在这里拼 JSON 路径并读它，新码在这里读库＋迁移一次）。

        ⛔ 用 `eng._cooldown_file = ...` 那种拧旋钮的搭法：批13 之后这枚旋钮不存在，
        而旧码上只有 set_runtime 会真的落盘——不拧它就等于「什么都没发生」的空转绿。
        """
        eng = eng or self.build_engine()
        eng.set_runtime(FakeRT(self.runner, self.tmp))
        return eng


class BootPathTest(DbScaffold):
    def test_cooldown_table_is_created_by_the_real_boot_path(self):
        """建表腿只认 `get_conn()` 第一次调用（＝`_init` 本体），⛔ 测试自己 CREATE。"""
        have = {r[0] for r in self.c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("trigger_cooldowns", have,
                      "启动路径没建这张表⇒冷却无处可写，运行时腿会全红在这一步之前")

    def test_cooldown_table_has_exactly_the_ruled_columns(self):
        cols = [r["name"] for r in self.c.execute("PRAGMA table_info(trigger_cooldowns)")]
        self.assertEqual(cols, COLUMNS, "冷却表的列集合＝键＋时刻两枚，多一列就是又造了个账本")
        pk = {r["name"]: r["pk"] for r in self.c.execute("PRAGMA table_info(trigger_cooldowns)")}
        self.assertEqual(pk["cd_key"], 1, "cd_key⛔ 主键⇒同一个键能落两行，读回来取哪条都没人定")


class RepoRoundTripTest(DbScaffold):
    def test_save_then_load_roundtrip_is_exact(self):
        now = time.time()
        values = {"trig_a:x": now - 10.0, "trig_b": now - 20.0}
        repo.save_cooldowns(values, retention_s=3600.0)
        self.assertEqual(repo.load_cooldowns(retention_s=3600.0), values,
                         "写进去和读回来必须一字不差＝冷却跨重启不失忆的全部含义")

    def test_load_ignores_entries_older_than_the_retention_window(self):
        now = time.time()
        repo.save_cooldowns({"fresh": now - 60.0, "stale": now - 7200.0}, retention_s=3600.0)
        got = repo.load_cooldowns(retention_s=3600.0)
        self.assertEqual(sorted(got), ["fresh"],
                         "七天窗（这里压成 3600s）的语义就是「太旧的冷却不再拦人」，⛔ 装看不见")

    def test_save_prunes_expired_rows_instead_of_leaving_them(self):
        """光在读侧过滤＝表永远只增不减；旧码的 JSON 每轮整体重写，收进 SQLite⛔ 顺手变成只增账。"""
        now = time.time()
        repo.save_cooldowns({"gone": now - 7200.0}, retention_s=3600.0)
        repo.save_cooldowns({"kept": now}, retention_s=3600.0)
        self.assertEqual(sorted(rows_of()), ["kept"],
                         "过期行还躺在库里＝这张表成垃圾桶，且⛔ 没人数过它长多大")

    def test_save_is_an_upsert_one_row_per_key(self):
        now = time.time()
        repo.save_cooldowns({"k": now - 100.0}, retention_s=3600.0)
        repo.save_cooldowns({"k": now}, retention_s=3600.0)
        self.assertEqual(list(rows_of()), ["k"], "同一键落两行＝读回来的冷却时刻取决于运气")
        self.assertAlmostEqual(rows_of()["k"], now, places=3)

    def test_repo_functions_are_not_zero_caller_decorations(self):
        """判例（20261002 裁定 §判例：唯独不能「留着」）：新符号必须有调用者，数的是**调用行**⛔ 文件数。"""
        src = ENGINE_PY.read_text(encoding="utf-8")
        for name in ("load_cooldowns", "save_cooldowns"):
            calls = [l for l in src.splitlines() if ("repo.%s(" % name) in l]
            self.assertTrue(calls, "engine 里没有任何一行调用 repo.%s＝又一枚空号" % name)


class SnapshotImportTest(DbScaffold):
    def write_snapshot(self, entries):
        path = os.path.join(self.tmp, SNAPSHOT_NAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        return path

    def test_legacy_snapshot_entries_land_in_sqlite(self):
        now = time.time()
        self.write_snapshot({"greet_evening:lidicn": now - 100.0, "evening_report": now - 200.0})
        eng = self.build_engine()
        eng.set_runtime(FakeRT(FakeRunner(), self.tmp))
        got = repo.load_cooldowns()
        self.assertIn("greet_evening:lidicn", got,
                      "活文件此刻就摆在 data/ 下，迁移不把它搬进库⇒重启即全员失忆，触发器提前再响")

    def test_stale_snapshot_entries_are_not_imported(self):
        now = time.time()
        self.write_snapshot({"fresh": now - 100.0, "ancient": now - 86400.0 * 30})
        eng = self.build_engine()
        eng.set_runtime(FakeRT(FakeRunner(), self.tmp))
        self.assertNotIn("ancient", repo.load_cooldowns(),
                         "30 天前的冷却被搬进来＝一个永不散场的冷却")
        self.assertIn("fresh", eng._last_fired)

    def test_snapshot_file_is_retired_only_after_a_verified_roundtrip(self):
        """「读一次」必须有终点：验过才删，⛔ 留一枚没人再读的活文件当假承诺。"""
        now = time.time()
        path = self.write_snapshot({"k1": now - 50.0, "k2": now - 60.0})
        eng = self.build_engine()
        eng.set_runtime(FakeRT(FakeRunner(), self.tmp))
        got = repo.load_cooldowns()
        self.assertTrue({"k1", "k2"} <= set(got), "库里没有这两条，删档无从谈起")
        self.assertFalse(os.path.exists(path),
                         "已验过还在＝engine 下次启动还会读它一遍，两笔来源谁赢没人定")

    def test_unverified_import_keeps_the_file_and_the_in_memory_state(self):
        """负控腿（给「验过才删」那把尺配的对照）：写库被吞掉时**必须留档**。

        它在旧码上不是「绿」而是 ERROR（`repo.save_cooldowns` 还不存在，取 old_save 就炸）⇒
        这条腿只在新码上有意义：删档这个不可逆动作挂在读回验证之后，验证不过就得停下。
        """
        now = time.time()
        path = self.write_snapshot({"k9": now - 10.0})
        old_save = repo.save_cooldowns
        repo.save_cooldowns = lambda *a, **k: None      # 模拟"写没进去"
        self.addCleanup(setattr, repo, "save_cooldowns", old_save)
        eng = self.build_engine()
        eng.set_runtime(FakeRT(FakeRunner(), self.tmp))
        self.assertTrue(os.path.exists(path),
                        "没验到就把用户的活快照删了＝不可逆的数据动作跑在验证之前")
        self.assertIn("k9", eng._last_fired, "导入失败连进程内状态也没保住＝本进程立刻失忆")

    def test_second_boot_reads_the_library_not_the_file(self):
        now = time.time()
        self.write_snapshot({"only_once": now - 30.0})
        first = self.build_engine()
        first.set_runtime(FakeRT(FakeRunner(), self.tmp))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, SNAPSHOT_NAME)))
        second = TriggerEngine(FakeStore([make_trigger()]))
        second.set_runtime(FakeRT(FakeRunner(), self.tmp))
        self.assertIn("only_once", second._last_fired,
                      "第二次启动读不到＝迁移只对第一代进程有效")


class EnginePersistenceTest(DbScaffold):
    def test_fire_writes_the_cooldown_row_to_sqlite(self):
        eng = self.boot()
        res = fire(eng)
        self.assertEqual(len(res), 1, "这条腿的前提是没命中触发器，先看命中")
        self.assertEqual(len(self.runner.calls), 1)
        got = rows_of()
        self.assertIn("trig_b13:m", got,
                      "跑完没落库＝旧语义（重启保冷却）在 SQLite 面上没接上")

    def test_fire_never_creates_the_json_snapshot(self):
        eng = self.boot()
        fire(eng)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, SNAPSHOT_NAME)),
                         "还在写 JSON＝两套真源并存，收口裁定没执行完")

    def test_a_fresh_engine_respects_the_sqlite_cooldown(self):
        eng = self.boot()
        fire(eng)
        again = self.boot()
        res = fire(again)
        self.assertEqual([], res, "库里明明有刚才那条冷却，新进程却判定可以再来一遍")
        self.assertEqual([], self.runner.calls, "被判冷却还跑了技能＝谎报")

    def test_the_json_knob_attribute_is_gone(self):
        """`_cooldown_file` 是旧码唯一的注入点（`tests/test_dry_run_gate.py:113`、
        `tests/test_audit_1002_batch4.py:201` 都在拧它）。留着它＝留一条谁都能拧的假开关。"""
        eng = self.boot()
        self.assertFalse(hasattr(eng, "_cooldown_file"),
                         "属性还在＝两套持久化路径都还「可用」，而只有一套是真的")

    def test_write_failure_is_loud_ledged_but_in_memory_survives(self):
        old_save = repo.save_cooldowns
        old_record = write_failures.record
        sites = []
        write_failures.record = lambda site, error, **k: sites.append(site)
        self.addCleanup(setattr, write_failures, "record", old_record)

        def boom(*a, **k):
            raise OSError("database is locked")
        repo.save_cooldowns = boom
        self.addCleanup(setattr, repo, "save_cooldowns", old_save)
        eng = self.boot()
        res = fire(eng)                       # ⛔ 因为写库失败就把整条事件链打断
        self.assertEqual(len(res), 1)
        self.assertIn("trig_b13:m", eng._last_fired, "写库失败把进程内冷却也丢了＝一次锁让本进程失忆")
        self.assertTrue(any("save_cooldowns" in s for s in sites),
                        "写失败没进 db_write_failures 台账＝这条 fail-open 是静默的，"
                        "现网只会看到「冷却怎么不管用」而查不到原因")


class SourceHygieneTest(unittest.TestCase):
    """源码形状腿：这几条⛔ 需要库，只钉「假承诺有没有被留在代码里」。"""

    @staticmethod
    def func_src(tree_src, cls, fname):
        node = ast.parse(tree_src)
        for c in ast.walk(node):
            if isinstance(c, ast.ClassDef) and c.name == cls:
                for f in c.body:
                    if isinstance(f, ast.FunctionDef) and f.name == fname:
                        return ast.get_source_segment(tree_src, f) or ""
        return ""

    def test_save_path_has_no_json_left(self):
        src = ENGINE_PY.read_text(encoding="utf-8")
        body = self.func_src(src, "TriggerEngine", "_save_cooldowns")
        self.assertTrue(body, "_save_cooldowns 没了＝本批的写腿换了地方，那这条腿该改口而不是默默消失")
        self.assertEqual(body.count("json.dump"), 0, "写腿里还在 dump JSON")
        self.assertEqual(body.count(SNAPSHOT_NAME), 0, "写腿里还在拼快照路径")

    def test_import_helper_exists_and_is_actually_called(self):
        src = ENGINE_PY.read_text(encoding="utf-8")
        lines = [l.strip() for l in src.splitlines() if "_import_legacy_snapshot" in l]
        defs = [l for l in lines if l.startswith("def ")]
        self.assertEqual(len(defs), 1, "_import_legacy_snapshot 的 def 行必须恰好 1 枚：%r" % lines)
        self.assertTrue([l for l in lines if not l.startswith("def ")],
                        "只有 def＝批11 §12-4 / 批12 §13-4 同款空号")

    def test_ruler_can_bite_and_denominators_are_reported(self):
        """自证：同一把尺扫一个已知存在的符号必须命中；分母现读，⛔ 把「没扫到」读成「通过」。"""
        src = ENGINE_PY.read_text(encoding="utf-8")
        self.assertGreater(len(src), 20_000, "engine.py 读空了＝扫描面塌了，下面的 0 命中全不算数")
        self.assertGreaterEqual(src.count("_last_fired"), 5,
                                "正对照：`_last_fired` 是这张文件里一定在的符号，它都数不到＝尺坏了")
        repo_src = REPO_PY.read_text(encoding="utf-8")
        self.assertGreater(len(repo_src), 15_000, "repo.py 读空了")
        self.assertIn("trigger_evaluations", repo_src, "正对照：repo.py 一定写着上一批那张表的名字")
        db_src = DB_PY.read_text(encoding="utf-8")
        self.assertGreater(len(db_src), 10_000, "db.py 读空了")

    def test_the_two_old_harnesses_no_longer_turn_the_json_knob(self):
        """`_cooldown_file` 一拆，这两份旧档必须跟着改口——⛔ 留着它们「看起来还能配」。"""
        for rel in ("tests/test_dry_run_gate.py", "tests/test_audit_1002_batch4.py"):
            src = (TREE / rel).read_text(encoding="utf-8")
            self.assertGreater(len(src), 8_000, "%s 读空了＝这条腿没扫到东西" % rel)
            self.assertEqual(src.count("_cooldown_file"), 0,
                             "%s 还在拧 JSON 旋钮＝它验的是已经不存在的路径" % rel)


if __name__ == "__main__":
    unittest.main()
