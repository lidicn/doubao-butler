r"""批18 文书腿：把 §19 段 append 到台账末尾（append-only，⛔ 改已有正文）。

用法（权威树根）：
    python3 -B scripts/audit_1002/patch_ledger_batch18_1002.py          # DRY
    python3 -B scripts/audit_1002/patch_ledger_batch18_1002.py --apply  # 落一次，重跑必 raise

闸门（顺序与批17 同：ALREADY_APPLIED 排第一，批14／批16／批17 各栽过一次「门在但走不到」或「门报错方向反」）：
  ALREADY_APPLIED → 基底 CR／md5／行／字节／段数 → 节文件形状（H2 恰 1、末行换行、⛔ `$(`、⛔ 起首空行、⛔ CR）
  → 旧文本＝新文本前缀（append-only 的物理定义）→ 段数增量＝本节自量的形状 → CR／末行换行 → 兄弟落点计数⛔ 变小 → 写后逐字节回读。
本节条数⛔ 由我写死：H3 从**节文件自己**数出来（现读＝12，§19-0 到 §19-11），再拿它去核追加后的增量；
写死的只有基线段那六个数＝追加前的定值，已于 2026-10-02T19:44Z 在权威树现读。
"""
import hashlib
import os
import sys

TARGET = "doc/审计报告分诊台账-20261002.md"
SECTION = "scripts/audit_1002/ledger_1002_batch18_section.md"
BASE_MD5 = "f8071624d366fb78ad08dcbe073d324b"
BASE_LINES = 1803
BASE_BYTES = 195997
BASE_H2 = 18
BASE_H3 = 87
# 兄弟落点：追加⛔ 得吃掉既有正文里的这些引用（计数只许 ≥ 追加前）
COLLATERAL = ("§18-1", "§17-8", "write_json_atomic", "docker restart",
              "9490b2e3085174ec213d6b34d0e0d01d", "classify_unawaited_1002.py")


def md5(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def heads(text):
    ls = text.splitlines()
    return (len([l for l in ls if l.startswith("## ")]),
            len([l for l in ls if l.startswith("### ")]))


def main():
    apply = "--apply" in sys.argv[1:]
    if not (os.path.exists(TARGET) and os.path.exists(SECTION)):
        raise SystemExit("NO_PATH|%s|%s|%s" % (os.getcwd(), TARGET, SECTION))
    raw = open(TARGET, "rb").read()
    text = raw.decode("utf-8")
    sec_raw = open(SECTION, "rb").read()
    sec = sec_raw.decode("utf-8")

    if "## §19 " in text:
        raise SystemExit("ALREADY_APPLIED|§19 已在台账里，⛔ 二次追加")
    if raw.count(b"\r"):
        raise SystemExit("BASELINE_CR|%d" % raw.count(b"\r"))
    if sec_raw.count(b"\r"):
        raise SystemExit("SECTION_CR|%d" % sec_raw.count(b"\r"))
    if not sec.endswith("\n"):
        raise SystemExit("SECTION_ENDNL")
    if "$(" in sec:
        raise SystemExit("BASH_COMMAND_SUB_IN_PROSE")
    if sec.startswith("\n"):
        raise SystemExit("SECTION_LEADING_BLANK")
    if md5(text) != BASE_MD5:
        raise SystemExit("BASELINE_MD5|%s(期望%s)" % (md5(text), BASE_MD5))
    if len(text.splitlines()) != BASE_LINES or len(raw) != BASE_BYTES:
        raise SystemExit("BASELINE_SHAPE|行=%d 字节=%d" % (len(text.splitlines()), len(raw)))
    b2, b3 = heads(text)
    if (b2, b3) != (BASE_H2, BASE_H3):
        raise SystemExit("BASELINE_HEADS|%d,%d(期望%s)" % (b2, b3, (BASE_H2, BASE_H3)))

    h2, h3 = heads(sec)
    if h2 != 1:
        raise SystemExit("SECTION_H2|%d(必须恰为 1)" % h2)
    if h3 < 1:
        raise SystemExit("SECTION_H3|%d" % h3)

    new = text + "\n" + sec if text.endswith("\n") else text + "\n\n" + sec
    if not new.startswith(text):
        raise SystemExit("NOT_APPEND_ONLY")
    if not new.endswith(sec):
        raise SystemExit("TAIL_NOT_SECTION")
    n2, n3 = heads(new)
    if (n2, n3) != (BASE_H2 + h2, BASE_H3 + h3):
        raise SystemExit("HEADS_AFTER|%d,%d(期望%d,%d)" % (n2, n3, BASE_H2 + h2, BASE_H3 + h3))
    if new.count("\r"):
        raise SystemExit("CR_INTRODUCED")
    if not new.endswith("\n"):
        raise SystemExit("ENDNL_LOST")
    for probe in COLLATERAL:
        if new.count(probe) < text.count(probe):
            raise SystemExit("COLLATERAL|%s" % probe)

    payload = new.encode("utf-8")
    print("%s |%s 行 %d->%d 字节 %d->%d 段 %d,%d->%d,%d md5 %s->%s 本节形状=%d H2/%d H3"
          % ("APPLY" if apply else "DRY", TARGET, len(text.splitlines()), len(new.splitlines()),
             len(raw), len(payload), b2, b3, n2, n3, BASE_MD5, md5(new), h2, h3))
    if apply:
        with open(TARGET, "wb") as fh:
            fh.write(payload)
        if open(TARGET, "rb").read() != payload:
            raise SystemExit("WRITE_VERIFY")
        print("APPLIED|%s|%s|%d 行|%d 字节|段 %d,%d"
              % (TARGET, md5(new), len(new.splitlines()), len(payload), n2, n3))
    else:
        print("DRY_ONLY|未落盘")


main()
