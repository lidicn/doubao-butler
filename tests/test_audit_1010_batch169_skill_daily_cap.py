"""批169（路线图 v2.4:158 剩腿）：技能当日硬上限的两处谎。

盘上现读（本批开窗先逐条点开；两处都按符号定位，⛔ 把行号写成在册锚点）：
  · `butler/store/repo.py` 的 `count_skill_runs_today` 里那行排除清单＝`'breaker','daily_limit',
    'disabled','not_found','engine_missing'`（改前现读在该文件第 407 行）—— 没有 `'dry'`；
    而 `butler/skills/runner.py` 的 `run()` 里 `if dry_run: status = "dry"` 之后那枚
    `self._persist_run(...)` 正是以 `status="dry"` 落 `skill_runs`。
    ⇒ 在面板上按两下「试运行」就把真实当日额度吃光，第二天技能到顶被拦，用户看到的是「技能不干活」。
  · 同一个 `run()` 里那句 `logger.warning("daily count failed: %s", e)`：读计数一旦抛异常，
    只留这行 warning 就**照样放行**，当日上限当场消失。本家有慢性间歇的写面/锁库
    （[[butler-db-write-stall-20260930]]），这不是假想分支。放行本身是既有政策
    （该响的没响比误拦更难查），⛔ 顺手改成拦；要改的是**这一眼看得见的诚实**：
    结果与入库那行都要留记号。

裁定口径：`doc/00-路线图/豆包管家版本开发路线图_v2.5_DCD版:60`「先加触发级冷却兜底」——
冷却层在册（批13），本批只补「额度算错＋额度无声消失」两格，⛔ 动熔断常数、⛔ 动 schema。

运行时腿＝真临时 sqlite（DATA_DIR 钉 tmp），⛔ 碰现网库、⛔ mock 掉计数这一层。
"""
from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import re
import shutil
import tempfile
import unittest

import butler.config as config
from butler.config import Settings, get_settings
from butler.store import db as db_mod, repo
from butler.skills.runner import SkillRunner
from butler.skills.runner_types import SkillResult

TREE = pathlib.Path(__file__).resolve().parents[1]
REPO_PY = TREE / "butler" / "store" / "repo.py"
RUNNER_PY = TREE / "butler" / "skills" / "runner.py"

MARKER = "daily_cap_unknown"
# 排除清单里必须一直在的（真源＝repo.py 里那行 `status NOT IN (...)`，本批只是把 'dry' 补进去）
MUST_EXCLUDE = ("breaker", "daily_limit", "disabled", "not_found", "engine_missing", "dry")
# 被拦在引擎之外的一族，和「跑过但没成功」的一族：后者⛔ 被排除，否则上限形同虚设
MUST_COUNT = ("error", "timeout", "dedup", "tv_offline", "ok")


def func_src(src: str, fname: str) -> str:
    node = ast.parse(src)
    for f in ast.walk(node):
        if isinstance(f, ast.FunctionDef) and f.name == fname:
            return ast.get_source_segment(src, f) or ""
    return ""


def daily_cap_shape_problems(repo_src: str, runner_src: str) -> list[str]:
    """一把尺，两处共用（真文件＋合成件）：返回问题清单，空＝绿。"""
    problems: list[str] = []

    body = func_src(repo_src, "count_skill_runs_today")
    if not body:
        problems.append("repo.count_skill_runs_today 找不到了")
    else:
        m = re.search(r"status NOT IN \(([^)]*)\)", body)
        if not m:
            problems.append("排除清单不在计数函数里")
        else:
            listed = [x.strip().strip("'\"") for x in m.group(1).split(",")]
            for name in MUST_EXCLUDE:
                if name not in listed:
                    problems.append("排除清单缺 %s（试运行/拦下的行被当成真实用量）" % name)
            for name in MUST_COUNT:
                if name in listed:
                    problems.append("排除清单越界：%s 被排除了＝当日上限能被反复重试绕过" % name)
    if MARKER not in runner_src:
        problems.append("runner 里没有任何额度读不出的记号")
    else:
        lines = runner_src.splitlines()
        warn = [i for i, l in enumerate(lines) if "daily count failed" in l]
        if not warn:
            problems.append("记号在，但读失败那条 warning 不见了")
        else:
            near = "\n".join(lines[warn[0]:warn[0] + 4])
            if MARKER not in near:
                problems.append("记号挂在别处＝读失败那条腿仍然是静默的")
    return problems


class _FakeStore:
    def __init__(self, skill):
        self.skill = skill

    def get(self, skill_id):
        return dict(self.skill, id=skill_id) if skill_id == self.skill["id"] else None

    def list(self):
        return [self.skill]


class _StubEngine:
    """引擎桩：只保证「跑到引擎并成功返回一行文本」，⛔ 网络、⛔ VLM。"""

    def __init__(self):
        self.dry_flags: list[bool] = []

    async def run(self, ctx):
        self.dry_flags.append(bool(ctx.dry_run))
        return SkillResult(ok=True, text="批169 夹具结果", status="ok",
                           meta={"engine": "stub"})


class _FakeRegistry:
    def __init__(self, engine):
        self.engine = engine

    def get(self, engine_name):
        return self.engine


class DbScaffold(unittest.TestCase):
    """真临时库：`config._settings` 换 tmp 目录 + `db_mod._conn = None` ⇒ get_conn 现开。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="butler_b169_")
        self.saved_settings = config._settings
        config._settings = Settings(data_dir=self.tmp)
        db_mod._conn = None
        self.c = db_mod.get_conn()

    def tearDown(self):
        conn = db_mod._conn
        db_mod._conn = None
        if conn is not None:
            conn.close()          # ⛔ 只置 None：19 格同进程会攒下一把未关连接，红字盖住真实读数
        config._settings = self.saved_settings
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 库面 --

    def add(self, skill_id, status, text="x"):
        return repo.add_skill_run(skill_id, "test", status, 12, text, "", {"status": status})

    def rows(self, skill_id):
        return [r["status"] for r in db_mod.get_conn().execute(
            "SELECT status FROM skill_runs WHERE skill_id=? ORDER BY id", (skill_id,))]

    def metas(self, skill_id):
        return [json.loads(r["meta_json"] or "{}") for r in db_mod.get_conn().execute(
            "SELECT meta_json FROM skill_runs WHERE skill_id=? ORDER BY id", (skill_id,))]

    # -- runner 面 --

    def build(self, skill_id="b169_skill", per_day=2):
        skill = {"id": skill_id, "name": "批169 夹具", "status": "enabled", "enabled": True,
                 "approval": "approved", "brain": {"engine": "stub"},
                 "output": [], "limits": {"per_day": per_day}}
        self.engine = _StubEngine()
        self.runner = SkillRunner(get_settings(), _FakeStore(skill), _FakeRegistry(self.engine))
        return skill_id

    def run_seq(self, skill_id, dry_flags):
        """多次运行共用一个事件循环：runner 的 Semaphore/Lock 绑首个 loop
        （P1-20 在册另案），⛔ 在本批测试里制造跨 loop 假红。"""
        async def go():
            return [await self.runner.run(skill_id, source="test", dry_run=d) for d in dry_flags]
        return asyncio.run(go())


class RepoCountTest(DbScaffold):
    """计数口径：额度只吃「真跑过」的行。"""

    def test_01_dry_rows_do_not_consume_the_daily_quota(self):
        sid = "b169_dry"
        for _ in range(3):
            self.add(sid, "dry")
        self.add(sid, "ok")
        self.assertEqual(1, repo.count_skill_runs_today(sid, ok_only=True),
                         "面板按三下试运行＝真实额度只剩 1 次")

    def test_02_documented_block_statuses_stay_excluded(self):
        sid = "b169_excl"
        for status in ("breaker", "daily_limit", "disabled", "not_found", "engine_missing"):
            self.add(sid, status)
        self.add(sid, "ok")
        self.add(sid, "ok")
        self.assertEqual(2, repo.count_skill_runs_today(sid, ok_only=True),
                         "这五枚本来就不该计——本批只加 'dry'，⛔ 顺手改掉既有口径")

    def test_03_attempted_runs_are_still_counted(self):
        sid = "b169_attempt"
        for status in ("error", "timeout", "dedup", "tv_offline"):
            self.add(sid, status)
        self.assertEqual(4, repo.count_skill_runs_today(sid, ok_only=True),
                         "跑进了引擎就没成＝照样是一次消耗，排除清单一宽上限即虚设")

    def test_04_ok_only_false_keeps_the_full_count(self):
        sid = "b169_full"
        self.add(sid, "dry")
        self.add(sid, "ok")
        self.add(sid, "breaker")
        self.assertEqual(3, repo.count_skill_runs_today(sid, ok_only=False),
                         "面板「总运行数」口径⛔ 被本批一起改掉")

    def test_05_count_is_scoped_to_one_skill(self):
        self.add("b169_a", "ok")
        self.add("b169_b", "ok")
        self.assertEqual(1, repo.count_skill_runs_today("b169_a", ok_only=True))


class RunnerQuotaTest(DbScaffold):
    """执行入口：额度拦与放都必须是真话。"""

    def test_06_dry_runs_do_not_block_the_next_real_run(self):
        sid = self.build(per_day=2)
        res = self.run_seq(sid, [True, True, False])
        self.assertEqual("dry", res[0]["status"])
        self.assertEqual("ok", res[2]["status"],
                         "两次试运行之后真实一次被判 daily_limit＝用户从没让它跑过却被告知到顶：%s" % res[2])
        self.assertEqual([True, True, False], self.engine.dry_flags,
                         "试运行仍然走到了引擎（本批只改额度口径，⛔ 把试运行改成不执行）")

    def test_07_real_runs_still_hit_the_cap(self):
        sid = self.build(per_day=2)
        res = self.run_seq(sid, [False, False, False])
        self.assertEqual(["ok", "ok", "daily_limit"], [r["status"] for r in res],
                         "正对照：上限本身⛔ 被本批拆掉")
        self.assertEqual([False, False], self.engine.dry_flags,
                         "第三次就该拦在引擎外，不该再跑一遍")

    def test_08_blocked_run_is_ledged(self):
        sid = self.build(per_day=1)
        self.run_seq(sid, [False, False])
        self.assertEqual(["ok", "daily_limit"], self.rows(sid),
                         "拦下不入库＝面板与排查都看不见这道门咬过谁")

    def test_09_dry_run_is_still_written_as_a_row(self):
        sid = self.build(per_day=2)
        self.run_seq(sid, [True])
        self.assertEqual(["dry"], self.rows(sid),
                         "修法是「计数时别数它」，⛔ 是「别再写这行」——试运行记录是审计材料")

    def test_10_count_failure_allows_but_leaves_a_mark(self):
        sid = self.build(per_day=2)
        old = repo.count_skill_runs_today

        def boom(*a, **k):
            raise OSError("database is locked")
        repo.count_skill_runs_today = boom
        self.addCleanup(setattr, repo, "count_skill_runs_today", old)
        res = self.run_seq(sid, [False])
        self.assertEqual("ok", res[0]["status"],
                         "读不出来就拦人＝一次锁库把全部技能停掉，这条 fail-open 是既有政策")
        self.assertTrue(res[0]["meta"].get(MARKER),
                        "放行可以，静默不行：结果里要看得见「这次上限没算成」%s" % res[0]["meta"])
        self.assertTrue(self.metas(sid)[0].get(MARKER),
                        "入库那行也要带同一个记号，否则事后查不到是哪一次")

    def test_11_healthy_count_leaves_no_mark(self):
        sid = self.build(per_day=2)
        res = self.run_seq(sid, [False])
        self.assertNotIn(MARKER, res[0]["meta"],
                         "记号只在读失败时出现＝每行都带一个是噪声，等于把记号抹平")


class ShapeRulerTest(unittest.TestCase):
    """形状尺腿：真文件要绿，合成旧形要咬（同一把尺，⛔ 两处分叉）。"""

    OLD_REPO = '''
def count_skill_runs_today(skill_id: str, ok_only: bool = True) -> int:
    c = get_conn()
    midnight = 0
    sql = "SELECT COUNT(*) FROM skill_runs WHERE skill_id=? AND ts>=?"
    if ok_only:
        sql += " AND status NOT IN ('breaker','daily_limit','disabled','not_found','engine_missing')"
    row = c.execute(sql, (skill_id, midnight)).fetchone()
    return int(row[0] or 0)
'''

    OLD_RUNNER = '''
class SkillRunner:
    async def run(self, skill_id, source="api", dry_run=False):
        per_day = (skill.get("limits") or {}).get("per_day", 0)
        if per_day and not dry_run:
            try:
                today = repo.count_skill_runs_today(skill_id, ok_only=True)
                if today >= per_day:
                    return {"status": "daily_limit"}
            except Exception as e:
                logger.warning("daily count failed: %s", e)
        return {"status": "ok"}
'''

    MARKER_ELSEWHERE_RUNNER = OLD_RUNNER.replace(
        "        per_day = (skill.get",
        '        _unused = {"%s": True}\n        per_day = (skill.get' % MARKER)
    WIDENED_REPO = OLD_REPO.replace("'engine_missing'", "'engine_missing','dry','error'")

    def test_12_real_files_are_clean(self):
        problems = daily_cap_shape_problems(REPO_PY.read_text(encoding="utf-8"),
                                            RUNNER_PY.read_text(encoding="utf-8"))
        self.assertEqual([], problems, "现读真文件：%s" % problems)

    def test_13_ruler_bites_the_old_shape(self):
        problems = daily_cap_shape_problems(self.OLD_REPO, self.OLD_RUNNER)
        self.assertEqual(2, len(problems),
                         "旧形（缺 'dry' ＋ 静默放行）必须恰好咬两条，实测 %s" % problems)
        self.assertTrue(any("缺 dry" in p for p in problems), problems)
        self.assertTrue(any("没有任何额度读不出的记号" in p for p in problems), problems)

    def test_14_ruler_bites_a_marker_wired_to_the_wrong_leg(self):
        problems = daily_cap_shape_problems(self.OLD_REPO.replace("'engine_missing'",
                                                                 "'engine_missing','dry'"),
                                            self.MARKER_ELSEWHERE_RUNNER)
        self.assertEqual(1, len(problems),
                         "记号挂在不经过那条 except 的地方＝读失败依旧静默，必须单独咬：%s" % problems)
        self.assertIn("挂在别处", problems[0])

    def test_14b_ruler_bites_an_overwidened_exclusion_list(self):
        """第二臂的校准：把 error 也排除掉＝重试绕过上限，尺子必须单独咬这一条。"""
        problems = daily_cap_shape_problems(self.WIDENED_REPO, self.MARKER_ELSEWHERE_RUNNER)
        over = [p for p in problems if "越界" in p]
        self.assertEqual(1, len(over), "排除清单越界那条臂⛔ 只咬一次，实测 %s" % problems)
        self.assertIn("error", over[0])

    def test_15_denominators_are_read_not_assumed(self):
        repo_src = REPO_PY.read_text(encoding="utf-8")
        runner_src = RUNNER_PY.read_text(encoding="utf-8")
        self.assertGreater(len(repo_src), 15_000, "repo.py 读空了＝下面的 0 命中不算数")
        self.assertGreater(len(runner_src), 18_000, "runner.py 读空了")
        self.assertIn("skill_runs", repo_src, "正对照：repo.py 一定写着这张表")
        self.assertIn("_breaker_check", runner_src, "正对照：runner.py 一定写着熔断那枚方法")


class AuthorizationBoundaryTest(unittest.TestCase):
    """⛔ 授权面：熔断常数、per_day 默认值、schema 注释都不在本批射程。"""

    def test_16_breaker_constants_untouched(self):
        src = RUNNER_PY.read_text(encoding="utf-8")
        for line in ("_WINDOW_S = 60", "_WINDOW_MAX = 5", "_COOLDOWN_BASE_S = 300"):
            self.assertIn(line, src, "%s 被改动＝本批越界（熔断常数另案在册）" % line)

    def test_17_per_day_read_path_untouched(self):
        src = RUNNER_PY.read_text(encoding="utf-8")
        self.assertIn('per_day = (skill.get("limits") or {}).get("per_day", 0)', src,
                      "per_day 的取法与默认值⛔ 本批改（默认值归 schema.py:164）")

    def test_18_schema_status_comment_still_documents_dry(self):
        src = (TREE / "butler" / "store" / "db.py").read_text(encoding="utf-8")
        self.assertRegex(src, r"ok \| dry \| breaker \| daily_limit",
                         "schema 注释是 dry 这枚状态的权威文档，改计数⛔ 改这里")


if __name__ == "__main__":
    unittest.main()
