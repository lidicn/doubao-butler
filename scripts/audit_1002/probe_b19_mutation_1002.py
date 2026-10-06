#!/usr/bin/env python3
"""批19 变异探针：`butler/store/db.py` 的 `get_conn` 八枚腿各自要咬得住什么。

真实腿（改语义⇒指定腿必须红）：
  M1 删锁内双检→LB3 · M2 发布早于建表→LB1/LB2/LB4 · M4 抛错不关连接→LB5
  M5 去掉无锁快路径→LG3 · M6 锁体内再进 `get_conn`（死分支）→LG2 · M7 `_init` 里再进 `get_conn`→LG1
等价对照（⛔ 动语义⇒8 枚腿必须全绿，被咬＝那条腿在测文案／结构而非行为）：
  C1 只多动作文书 · C2 两条无害 PRAGMA 换序 · C3 局部变量纯改名 · C4 日志文案
M6／M7 是**结构性守卫**的腿：真递归会撞非重入 `Lock` 自死锁、把整条探针挂住，所以变异体走 `if False:`
死分支——守卫读 AST（现读真文件），死分支⛔ 影响运行期，两件事各自成立。

按行号切片＋startswith 断言定位，⛔ 靠空白字面量匹配（列对齐的注释空格我抄不准）。
每条腿的期望只列「我要它咬的那几枚」，额外红原样打印，⛔ 藏。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "butler" / "store" / "db.py"
GOOD_MD5 = "7dcf301fed05450166716f4d47982dbf"
MODULE = "tests.test_audit_1002_batch19_db_getconn_race"
RAN = "Ran 8 tests"

LB1 = "test_init_runs_before_the_connection_is_published"
LB2 = "test_concurrent_reader_never_gets_a_tableless_connection"
LB3 = "test_concurrent_first_call_builds_exactly_one_connection"
LB4 = "test_init_failure_does_not_cache_a_broken_connection"
LB5 = "test_init_failure_closes_the_abandoned_connection"
LG1 = "test_init_body_does_not_call_get_conn"
LG2 = "test_no_lock_body_calls_get_conn"
LG3 = "test_second_call_does_not_take_the_lock"

INIT_DEF = "def _init(c: sqlite3.Connection) -> None:"

MUTANTS = [
    dict(id="M1-no-double-check", kind="real", start=47, end=48,
         assert_first="if _conn is not None:", assert_last="return _conn", repl=[],
         expect=[LB3]),
    dict(id="M2-publish-before-init", kind="real", start=57, end=62,
         assert_first="try:", assert_last="_conn = c",
         repl=["        _conn = c",
               "        try:",
               "            _init(c)",
               "        except Exception:",
               "            c.close()",
               "            raise"],
         expect=[LB1, LB2, LB4]),
    dict(id="M4-no-close-on-failure", kind="real", start=60, end=60,
         assert_first="c.close()", repl=[], expect=[LB5]),
    dict(id="M5-no-lockfree-fastpath", kind="real", start=44, end=45,
         assert_first="if _conn is not None:", assert_last="return _conn",
         repl=[], expect=[LG3]),
    dict(id="M6-getconn-inside-lock", kind="real", start=56, end=56,
         assert_first="c.row_factory = sqlite3.Row",
         repl=["        c.row_factory = sqlite3.Row",
               "        if False:                    # 变异腿：守卫要咬住「锁体内再进 get_conn」",
               "            get_conn()"],
         expect=[LG2]),
    dict(id="M7-getconn-inside-init", kind="real", anchor_line=INIT_DEF,
         repl=["    if False:                      # 变异腿：守卫要咬住「_init 里再进 get_conn」",
               "        get_conn()",
               ""],
         expect=[LG1]),
    dict(id="C1-docstring-only", kind="control", start=42, end=42,
         assert_first='"""',
         repl=["", "    （对照腿：这一段只多动作文书，⛔ 动语义）", '    """']),
    dict(id="C2-pragma-order", kind="control", start=54, end=55,
         assert_first='c.execute("PRAGMA synchronous', assert_last='c.execute("PRAGMA busy_timeout',
         repl=['        c.execute("PRAGMA busy_timeout=5000")',
               '        c.execute("PRAGMA synchronous=NORMAL")']),
    dict(id="C3-rename-local", kind="control", start=49, end=51,
         assert_first="s = get_settings()", assert_last="db_path = Path(s.data_dir)",
         repl=["        cfg = get_settings()",
               "        Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)",
               '        db_path = Path(cfg.data_dir) / "butler.db"']),
    dict(id="C4-log-text", kind="control", start=63, end=63,
         assert_first="logger.info(",
         repl=['        logger.info("sqlite opened (WAL) at %s", db_path)']),
]


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def run_suite() -> tuple[int, str]:
    env = dict(os.environ,
               DOUBAO_API_KEY="dummy", DESKPILOT_API_TOKEN="dummy",
               TASK_REPORT_TOKEN="dummy", BUTLER_WEB_PASSWORD="dummy",
               DATA_DIR="/tmp/b19_qa_data")
    r = subprocess.run([sys.executable, "-B", "-m", "unittest", MODULE, "-v"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def red_legs(out: str) -> list[str]:
    legs = []
    for line in out.splitlines():
        for mark in ("FAIL: ", "ERROR: "):
            if line.startswith(mark):
                name = line[len(mark):].split(" (")[0]
                if name not in legs:
                    legs.append(name)
    return legs


def locate(lines: list[str], m: dict) -> tuple[int, int] | None:
    """返回 0-based [start, end) 切片；锚点不符返回 None（该腿⛔ 计入存活）。"""
    if "anchor_line" in m:
        hits = [i for i, l in enumerate(lines) if l == m["anchor_line"]]
        if len(hits) != 1:
            print("ANCHOR|%s 锚定行命中 %d 次（期望 1）" % (m["id"], len(hits)))
            return None
        i = hits[0]
        return i + 1, i + 1                    # 在 def 之后插入
    s, e = m["start"] - 1, m["end"]             # 1-based 闭区间 → 0-based 半开区间
    if not lines[s].strip().startswith(m["assert_first"]):
        print("ANCHOR|%s :%d 现读 %r 期望前缀 %r" % (m["id"], s + 1, lines[s], m["assert_first"]))
        return None
    if "assert_last" in m and not lines[e - 1].strip().startswith(m["assert_last"]):
        print("ANCHOR|%s :%d 现读 %r 期望前缀 %r" % (m["id"], e, lines[e - 1].strip(), m["assert_last"]))
        return None
    return s, e


def main() -> int:
    print("PROBE_STAMP %s" % os.environ.get("PROBE_STAMP", ""))
    print("TARGET|%s md5=%s 期望=%s" % (DB, md5(DB), GOOD_MD5))
    if md5(DB) != GOOD_MD5:
        print("PRECHECK_RED|树不是批19 打完补丁的真值⇒在此刻度的变异⛔ 算本批证据")
        return 2
    pre_rc, pre_out = run_suite()
    if RAN not in pre_out or pre_rc != 0:
        print("BASELINE_RED|rc=%s ran_ok=%s red=%s" % (pre_rc, RAN in pre_out, red_legs(pre_out)))
        return 2
    print("BASELINE|%s|all-ok|db.py=%s" % (RAN, GOOD_MD5))

    real = bitten = controls = survived = misbite = restore_bad = anchor_bad = 0
    extra_red = []
    for m in MUTANTS:
        original = DB.read_text(encoding="utf-8")
        lines = original.split("\n")
        loc = locate(lines, m)
        if loc is None:
            anchor_bad += 1
            continue
        s, e = loc
        mutated = "\n".join(lines[:s] + m["repl"] + lines[e:])
        try:
            compile(mutated, str(DB), "exec")
        except SyntaxError as exc:
            print("MUTANT_UNCOMPILED|%s %s:%s ⇒ 该腿未跑，⛔ 计入存活" % (m["id"], exc.lineno, exc.msg))
            anchor_bad += 1
            continue
        if m["kind"] == "real":
            real += 1
        else:
            controls += 1
        DB.write_text(mutated, encoding="utf-8")
        try:
            rc, out = run_suite()
            legs = red_legs(out)
            ran_ok = RAN in out
            if m["kind"] == "real":
                hit = [x for x in m["expect"] if x in legs]
                extra = [x for x in legs if x not in m["expect"]]
                if hit and ran_ok:
                    bitten += 1
                    if extra:
                        extra_red.append("%s:%s" % (m["id"], ",".join(extra)))
                    print("BITE|%s|expect_hit=%s|other_red=%s|rc=%s"
                          % (m["id"], ",".join(hit), ",".join(extra) or "无", rc))
                else:
                    misbite += 1
                    print("NOT_BITTEN|%s|expect=%s|actual_red=%s|rc=%s|ran_ok=%s"
                          % (m["id"], ",".join(m["expect"]), ",".join(legs) or "无", rc, ran_ok))
            else:
                if not legs and ran_ok and rc == 0:
                    survived += 1
                    print("SURVIVED|%s|等价对照存活（8 条全 ok）" % m["id"])
                else:
                    misbite += 1
                    print("CONTROL_BITTEN|%s|red=%s|rc=%s|ran_ok=%s ⇒ 该腿在测文案/结构⛔ 行为"
                          % (m["id"], ",".join(legs), rc, ran_ok))
        finally:
            DB.write_text(original, encoding="utf-8")
            if md5(DB) != GOOD_MD5:
                restore_bad += 1
                print("RESTORE_BAD|%s|md5=%s 期望 %s" % (m["id"], md5(DB), GOOD_MD5))

    print("COVERAGE|legs_bitten_expect=%s"
          % ",".join(sorted({x for m in MUTANTS for x in m.get("expect", [])})))
    print("EXTRA_RED|%s" % (";".join(extra_red) or "无"))
    print("CENSUS|real=%d bitten=%d controls=%d survived=%d misbite=%d restore_bad=%d "
          "skipped=%s tree_md5=%s"
          % (real, bitten, controls, survived, misbite, restore_bad,
             "无" if anchor_bad == 0 else "%d条锚点/编译不合格" % anchor_bad, md5(DB)))
    return 0 if (bitten == real and survived == controls and misbite == 0
                 and restore_bad == 0 and anchor_bad == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
