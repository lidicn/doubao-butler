r"""把 `_缺陷汇总_供审阅.md` 的合并表逐行解析，并和分诊台账做机械对账。

为什么要这把尺：出资人的原话是「核实并修复 doc/审计报告/ 里的所有 bug」，而报告目录会长。
⛔ 靠我记得改过哪几条来判「还剩多少」＝假绿通道；判据必须从**权威源枚举**（汇总表本体＝合并后的
缺陷表，非各报告手挑清单），并把匹配方式写成正则印在输出里，让人能复跑。

用法：
    python scripts/audit_1002/reconcile_report_roster_1002.py --roster <汇总.md> --ledger <台账.md> [--dump tsv]
判读口径（三桶，⛔ 用「命中」一词混指）：
  A id_hit   ＝台账里出现过该行的任一枚报告编号（正则 `\b(?:P[012]-\d+|[A-Z]{1,3}-\d+|NEW-\d+)\b`）
  B file_hit ＝A 不成立，但该行「文件:行号」列里任一 `xxx.py` 出现在台账里 ⇒ 只算**弱**对账
  C no_hit   ＝A、B 都不成立 ⇒ 必须逐条人工点开（本脚本⛔ 下「未修」结论）
C 桶才是待办清单；B 桶要抽查（台账可能只提过那个文件的别的毛病）。
"""
import argparse
import io
import os
import re
import sys

ID_RE = re.compile(r"\b(?:P[012]-\d+|[A-Z]{1,3}-\d+|NEW-\d+)\b")
FILE_RE = re.compile(r"([A-Za-z0-9_]+\.py)")
ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|")


def cells(line):
    parts = [p.strip() for p in line.strip().strip("|").split("|")]
    return parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--dump", default="")
    args = ap.parse_args()
    for p in (args.roster, args.ledger):
        if not os.path.exists(p):
            raise SystemExit("NO_PATH|%s" % p)
    roster = io.open(args.roster, encoding="utf-8").read()
    ledger = io.open(args.ledger, encoding="utf-8").read()
    ledger_files = set(FILE_RE.findall(ledger))
    ledger_ids = set(ID_RE.findall(ledger))

    rows = []
    in_table = False
    table_lines = 0
    for line in roster.splitlines():
        if line.startswith("## 一、合并后的缺陷表"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            in_table = False
            continue
        if not in_table or not ROW_RE.match(line):
            continue
        table_lines += 1
        c = cells(line)
        if len(c) != 6:
            raise SystemExit("ROW_SHAPE|%s|列=%d" % (line[:40], len(c)))
        num, ids, sev, loc, mech, srcs = c[0], c[1], c[2], c[3], c[4], c[5]
        row_ids = set(ID_RE.findall(ids))
        row_files = set(FILE_RE.findall(loc))
        a = sorted(row_ids & ledger_ids)
        b = sorted(row_files & ledger_files)
        bucket = "A" if a else ("B" if b else "C")
        rows.append((int(num), bucket, sorted(row_ids), row_files, a, b, sev, loc, mech, srcs))

    if not rows:
        raise SystemExit("NO_ROWS|分母=0——先确认表格形制（ROW_RE=%s）⛔ 判「全部已处置」" % ROW_RE.pattern)
    nums = [r[0] for r in rows]
    counts = {k: len([r for r in rows if r[1] == k]) for k in "ABC"}
    print("ROSTER|%s" % args.roster)
    print("ROWS_PARSED|%d|段内表格行=%d|first=%d|last=%d|连续=%s|唯一=%s"
          % (len(rows), table_lines, min(nums), max(nums),
             sorted(nums) == list(range(min(nums), max(nums) + 1)),
             len(set(nums)) == len(nums)))
    print("ROW_RE|%s|ID_RE|%s|FILE_RE|%s" % (ROW_RE.pattern, ID_RE.pattern, FILE_RE.pattern))
    print("LEDGER|%s bytes=%d|ledger_ids=%d|ledger_files=%d"
          % (args.ledger, len(ledger.encode("utf-8")), len(ledger_ids), len(ledger_files)))
    print("BUCKETS|A_id_hit=%d|B_file_hit_only=%d|C_no_hit=%d"
          % (counts["A"], counts["B"], counts["C"]))
    print("C 桶待人工点开（逐条，⛔ 批量判词）：")
    for r in rows:
        if r[1] == "C":
            print("  C|%d|ids=%s|files=%s|%s" % (r[0], ",".join(r[2]) or "-",
                                                 ",".join(sorted(r[3])) or "-", r[7][:90]))
    if args.dump:
        with io.open(args.dump, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("num\tbucket\tids\tsev\tfile_loc\tmechanism\tsources\tid_hits\tfile_hits\n")
            for r in rows:
                fh.write("%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n"
                         % (r[0], r[1], ",".join(r[2]), r[6], r[7].replace("\t", " "),
                            r[8].replace("\t", " "), r[9], ",".join(r[4]), ",".join(r[5])))
        print("DUMP|%s|%d 行" % (args.dump, len(rows) + 1))
    print("边界声明：本脚本只解析合并表所在文件；`doc/审计报告/` 目录里的报告⛔ 全被合并表覆盖"
          "（表头自称来源 7 份，目录现读文件数见下）——所以 A/B/C 三桶判的是「合并表的处置面」，"
          "⛔ 等于「全部报告的处置面」。")
    reports = sorted(os.listdir(os.path.dirname(args.roster) or "."))
    print("DIR_LIST|%s|%d 件|%s" % (os.path.dirname(args.roster) or ".", len(reports), ";".join(reports)))


main()
