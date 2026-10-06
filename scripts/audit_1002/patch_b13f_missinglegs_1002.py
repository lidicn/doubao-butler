#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批 13 补腿（变异探针存活的两条）：给 `tests/test_audit_1002_batch13_cooldown.py` 加两条验收。

来历（先说清楚顺序，⛔ 让读者以为这是「先红后绿」的功能腿）：
  `scripts/audit_1002/mutate_b13_cooldown_1002.py` 跑 14 枚变异体，12 咬 2 活：
    · LOAD_SWALLOWS_TO_EMPTY —— 把 `repo.load_cooldowns` 的「读失败直接抛」折成 `return {}`，53 条全绿。
      根因：原有那条失败腿 `test_write_failure_is_loud_ledged_but_in_memory_survives`
      是 **mock 掉 repo 那一层**来喂 engine 的，所以「repo 真会抛」这件事从来没有腿盯过。
    · STALE_SNAPSHOT_WINS —— 把快照合并的 `if v > self._last_fired.get(k, 0)` 拆成无条件覆盖，53 条全绿。
      根因：原有快照腿只测「文件里有的键进了库」，没测「库里已有的键更该赢」。
  这两枚改动的是**本批已落码的产码行为**，所以补的是覆盖面腿：它们在干净树上直接绿，
  红必须由变异体给出 ⇒ 出口判据＝`--only LOAD_SWALLOWS_TO_EMPTY` / `--only STALE_SNAPSHOT_WINS`
  两跑都必须 BITTEN，且咬住的腿名是这次新加的那两条。⛔ 拿「写完了就绿」当证据。

闸门：基线 md5/行数/字节数三对 + 每条锚点 count==1 + 新增前后 AST `test_` 方法数 +2 +
     「def 前必须空一行」的负控（批 13 我自己的分隔符事故就是栽在丢空行）+ ast.parse + CR==0。
一次性：锚点被吃掉就不再匹配 ⇒ 重跑必 raise；`--apply` 才写盘。
"""
import argparse
import ast
import hashlib
import re
import sys

TARGET = "tests/test_audit_1002_batch13_cooldown.py"
BASE_MD5 = "8c11590aee48953d362c7ef68eb5bfc7"
BASE_LINES = 366
BASE_BYTES = 18756

OLD_IMPORT = "import shutil\nimport tempfile\n"
NEW_IMPORT = "import shutil\nimport sqlite3\nimport tempfile\n"

OLD_A = "\n\n\nclass SnapshotImportTest(DbScaffold):"
LEG_A = '''    def test_repo_load_cooldowns_raises_instead_of_folding_to_empty(self):
        """「读失败折成空 dict」这条假绿通道由 **repo 本体**关门，不由 mock 的 engine 腿代关。

        已有的失败腿是把 `repo.load_cooldowns` 换成会抛的假函数再喂 engine，
        那种搭法对「repo 自己吞不吞」零信息（变异体 LOAD_SWALLOWS_TO_EMPTY 就是这样活下来的）：
        所以这里把真表摘掉，让真 repo 去撞真库。
        """
        self.c.execute("DROP TABLE trigger_cooldowns")
        self.c.commit()
        with self.assertRaises(sqlite3.OperationalError) as ctx:
            repo.load_cooldowns()
        self.assertIn("trigger_cooldowns", str(ctx.exception),
                      "抛是抛了，但⛔ 不是这张表读不到——别把「另一种错误」当成本腿的证据")'''

OLD_B = "\n\n    def test_snapshot_file_is_retired_only_after_a_verified_roundtrip(self):"
LEG_B = '''    def test_snapshot_never_overwrites_a_fresher_sqlite_entry(self):
        """两笔来源同键＝取较新那个。库里已经更晚时，旧快照⛔ 把冷却往前推＝提前放行。

        正对照（同一键库里更旧）走 `test_legacy_snapshot_entries_land_in_sqlite` 那条路；
        这条盯的是反方向：`v > self._last_fired.get(k, 0)` 那枚守卫一旦拆掉，
        用户 5 分钟前刚响过的触发器会被快照里 8 分钟前的时刻覆盖掉。
        """
        now = time.time()
        repo.save_cooldowns({"dup": now - 20.0})          # 库里这条更新
        self.write_snapshot({"dup": now - 500.0})          # 快照这条更旧
        eng = self.build_engine()
        eng.set_runtime(FakeRT(FakeRunner(), self.tmp))
        self.assertAlmostEqual(eng._last_fired["dup"], now - 20.0, places=3,
                               msg="进程内冷却被旧快照倒灌＝本进程会提前再响一次")
        self.assertAlmostEqual(rows_of()["dup"], now - 20.0, places=3,
                               msg="库里的时刻被旧快照倒灌＝重启后照样提前再响")'''


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def test_defs(text):
    return [n.name for n in ast.walk(ast.parse(text))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")]


def sep_counts(text):
    """按行量的分隔符尺：返回（紧贴上文的 def 数, 前面空一行的 def 数）。

    第一版我用的是 `\\n(?!\\n)    def test_`——那条先行否定看的是「def 前面那个换行符后面
    是不是换行」，而它后面永远是 4 个空格，所以在基线上就数出 22（＝全部），
    等于一把恒真的尺。改成逐行看**上一行是不是空行**。
    第二版把「紧跟在 `class X:` 后面的第一枚 def」也算成孤立（基线上有 3 枚，
    那是正常形制⛔ 空行），所以这里的「贴上文」只认**语句行**当上文（⛔ 以 `:` 收尾的头行）。
    """
    lines = text.splitlines()
    solo = blank = 0
    for i, line in enumerate(lines):
        if line.startswith("    def test_") and i > 0:
            prev = lines[i - 1].strip()
            if prev == "":
                blank += 1
            elif not prev.endswith(":"):
                solo += 1
    return solo, blank


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with open(TARGET, encoding="utf-8") as fh:
        text = fh.read()

    got = md5(TARGET)
    if got != BASE_MD5:
        raise SystemExit("BASELINE_MISMATCH|%s|want=%s|got=%s ⇒ 不是我预期的那棵树，拒跑"
                         % (TARGET, BASE_MD5, got))
    lines_before = len(text.splitlines())
    if lines_before != BASE_LINES or len(text.encode("utf-8")) != BASE_BYTES:
        raise SystemExit("BASELINE_SHAPE|行=%d(期望%d)|字节=%d(期望%d)"
                         % (lines_before, BASE_LINES, len(text.encode("utf-8")), BASE_BYTES))

    for name, old in (("import", OLD_IMPORT), ("legA", OLD_A), ("legB", OLD_B)):
        n = text.count(old)
        if n != 1:
            raise SystemExit("ANCHOR_%s|%s|count=%d" % ("AMBIGUOUS" if n > 1 else "MISS", name, n))

    for name, leg in (("legA", LEG_A), ("legB", LEG_B)):
        if leg.split("\n", 1)[0] in text:
            raise SystemExit("ALREADY_APPLIED|%s" % name)

    before_defs = test_defs(text)
    solo_before, blank_before = sep_counts(text)

    new = text.replace(OLD_IMPORT, NEW_IMPORT, 1)
    new = new.replace(OLD_A, "\n\n" + LEG_A + OLD_A, 1)
    new = new.replace(OLD_B, "\n\n" + LEG_B + OLD_B, 1)

    after_defs = test_defs(new)
    added = sorted(set(after_defs) - set(before_defs))
    if len(after_defs) != len(before_defs) + 2:
        raise SystemExit("DEF_COUNT|before=%d after=%d" % (len(before_defs), len(after_defs)))
    if added != sorted(re.match(r"\s*def (test_\w+)", l).group(1)
                       for l in (LEG_A, LEG_B) if l.lstrip().startswith("def ")):
        raise SystemExit("UNEXPECTED_NEW_LEGS|%s" % ",".join(added))
    if len(set(added)) != 2:
        raise SystemExit("LEG_NAMES_COLLIDE|%s" % ",".join(added))
    # 负控：⛔ 出现「紧贴上文、前面没空行」的 def（批 13 分隔符事故的那个形状）
    solo_after, blank_after = sep_counts(new)
    if solo_before != 0 or solo_after != 0:
        raise SystemExit("BLANK_LINE_GATE|before=%d after=%d（def 前必须空一行）"
                         % (solo_before, solo_after))
    if blank_after != blank_before + 2:
        raise SystemExit("SEPARATORS|before=%d after=%d（每枚新腿配一枚空行分隔）"
                         % (blank_before, blank_after))
    # 尺自证（会咬才配当闸）：故意吃掉 LEG_A 前面那枚空行，solo 必须 +1、blank 必须 −1
    probe = new.replace("\n\n" + LEG_A, "\n" + LEG_A, 1)
    solo_probe, blank_probe = sep_counts(probe)
    if solo_probe != 1 or blank_probe != blank_after - 1:
        raise SystemExit("RULER_CANNOT_BITE|solo=%d(须1) blank=%d(须%d)"
                         % (solo_probe, blank_probe, blank_after - 1))
    print("RULER_SELFTEST|基线 solo=%d，新码 solo=%d/blank=%d -> 吃掉一枚空行后 solo=%d/blank=%d（尺会咬）"
          % (solo_before, solo_after, blank_after, solo_probe, blank_probe))

    if "\nimport sqlite3\n" not in new:
        raise SystemExit("IMPORT_NOT_LANDED")
    if new.count("sqlite3.") < 1:
        raise SystemExit("SQLITE3_UNUSED")
    if "\r" in new:
        raise SystemExit("CR_INTRODUCED|%d" % new.count("\r"))
    if not new.endswith("\n"):
        raise SystemExit("ENDNL_LOST")

    lines_after = len(new.splitlines())
    print("DRY |%s 行 %d->%d 字节 %d->%d 腿 %d->%d"
          % (TARGET, lines_before, lines_after, len(text.encode("utf-8")),
             len(new.encode("utf-8")), len(before_defs), len(after_defs)))
    print("DRY |new_legs=%s" % ",".join(added))

    if not args.apply:
        print("DRY only，未写盘。加 --apply 才落。")
        return 0

    with open(TARGET, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    wrote = open(TARGET, encoding="utf-8").read()
    cr = wrote.count("\r")
    print("APPLIED|%s|md5=%s|行=%d|字节=%d|CR=%d|末行换行=%s|腿=%d"
          % (TARGET, md5(TARGET), len(wrote.splitlines()), len(wrote.encode("utf-8")),
             cr, wrote.endswith("\n"), len(test_defs(wrote))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
