r"""批15 文书腿：把 §16 段 append 到台账末尾（append-only，⛔ 改已有正文）。

用法（权威树根）：
    python3 -B scripts/audit_1002/patch_ledger_batch15_1002.py          # DRY
    python3 -B scripts/audit_1002/patch_ledger_batch15_1002.py --apply  # 落一次，重跑必 raise

闸门：基底 md5/行/字节/CR/段数 → 本节形状⛔ 由我写死条数、从**节文件自己**数（H2 恰为 1）
→ 追加后段数增量＝该形状 → 旧文本必须还是新文本的前缀（append-only 的物理定义）
→ ⛔ `$(` 进正文（bash 会当命令替换）→ 末行换行保住 → 写后逐字节回读。
"""
import hashlib
import os
import sys

TARGET = "doc/审计报告分诊台账-20261002.md"
SECTION = "scripts/audit_1002/ledger_1002_batch15_section.md"
BASE_MD5 = "7dbda739570d407cdc8f8523c3b85781"
BASE_LINES = 1235
BASE_BYTES = 142000
BASE_H2 = 15
BASE_H3 = 52
COLLATERAL = ("§15-8", "§14-7", "§13 ", "cache_filename")


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
    if raw.count(b"\r"):
        raise SystemExit("BASELINE_CR|%d" % raw.count(b"\r"))
    text = raw.decode("utf-8")
    sec_raw = open(SECTION, "rb").read()
    if sec_raw.count(b"\r"):
        raise SystemExit("SECTION_CR|%d" % sec_raw.count(b"\r"))
    sec = sec_raw.decode("utf-8")
    if not sec.endswith("\n"):
        raise SystemExit("SECTION_ENDNL")
    if "$(" in sec:
        raise SystemExit("BASH_COMMAND_SUB_IN_PROSE")
    if sec.startswith("\n"):
        raise SystemExit("SECTION_LEADING_BLANK")

    h2, h3 = heads(sec)
    if h2 != 1:
        raise SystemExit("SECTION_H2|%d(必须恰为 1)" % h2)
    if h3 < 1:
        raise SystemExit("SECTION_H3|%d" % h3)
    if "## §16 " in text:
        raise SystemExit("ALREADY_APPLIED|§16 已在台账里，⛔ 二次追加")
    if md5(text) != BASE_MD5:
        raise SystemExit("BASELINE_MD5|%s(期望%s)" % (md5(text), BASE_MD5))
    if len(text.splitlines()) != BASE_LINES or len(raw) != BASE_BYTES:
        raise SystemExit("BASELINE_SHAPE|行=%d 字节=%d" % (len(text.splitlines()), len(raw)))
    b2, b3 = heads(text)
    if (b2, b3) != (BASE_H2, BASE_H3):
        raise SystemExit("BASELINE_HEADS|%d,%d(期望%s)" % (b2, b3, (BASE_H2, BASE_H3)))

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
