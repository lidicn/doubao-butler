"""批14 文书腿：把 §15 段 append 到台账末尾（append-only，⛔ 改已有正文）。

用法（权威树根）：
    python3 -B scripts/audit_1002/patch_ledger_batch14_1002.py          # DRY
    python3 -B scripts/audit_1002/patch_ledger_batch14_1002.py --apply  # 落一次，重跑必 raise

闸门：基底 md5/行/字节/CR/段数 → 追加后段数只增不减且增量＝本节形状 → 旧文本必须还是新文本的前缀
（append-only 的物理定义）→ ⛔ `$(` 进正文（bash 会当命令替换）→ 末行换行保住。
"""
import hashlib
import os
import sys

TARGET = "doc/审计报告分诊台账-20261002.md"
SECTION = "scripts/audit_1002/ledger_1002_batch14_section.md"
BASE_MD5 = "6e73d742201054573a1c3d7b4020c654"
BASE_LINES = 1090
BASE_BYTES = 130351
BASE_H2 = 14
BASE_H3 = 43
SECTION_SHAPE = (1, 9)  # (H2, H3)——现读自 ledger_1002_batch14_section.md


def sha(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def heads(text):
    h2 = len([l for l in text.splitlines() if l.startswith("## ")])
    h3 = len([l for l in text.splitlines() if l.startswith("### ")])
    return h2, h3


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
    if (h2, h3) != SECTION_SHAPE:
        raise SystemExit("SECTION_SHAPE|%d,%d(期望%s)" % (h2, h3, SECTION_SHAPE))
    if "## §15 " in text:
        raise SystemExit("ALREADY_APPLIED|§15 已在台账里，⛔ 二次追加")
    if sha(text) != BASE_MD5:
        raise SystemExit("BASELINE_MD5|%s(期望%s)" % (sha(text), BASE_MD5))
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
    if (n2, n3) != (BASE_H2 + SECTION_SHAPE[0], BASE_H3 + SECTION_SHAPE[1]):
        raise SystemExit("HEADS_AFTER|%d,%d" % (n2, n3))
    if new.count("\r"):
        raise SystemExit("CR_INTRODUCED")
    if not new.endswith("\n"):
        raise SystemExit("ENDNL_LOST")
    for probe in ("§14-6", "§13-6", "§12-5-2"):
        if new.count(probe) < text.count(probe):
            raise SystemExit("COLLATERAL|%s" % probe)

    payload = new.encode("utf-8")
    print("%s |%s 行 %d->%d 字节 %d->%d 段 %d,%d->%d,%d md5 %s->%s"
          % ("APPLY" if apply else "DRY", TARGET, len(text.splitlines()), len(new.splitlines()),
             len(raw), len(payload), b2, b3, n2, n3, BASE_MD5, sha(new)))
    if apply:
        with open(TARGET, "wb") as fh:
            fh.write(payload)
        if open(TARGET, "rb").read() != payload:
            raise SystemExit("WRITE_VERIFY")
        print("APPLIED|%s|%s|%d 行|%d 字节|段 %d,%d"
              % (TARGET, sha(new), len(new.splitlines()), len(payload), n2, n3))
    else:
        print("DRY_ONLY|未落盘")


main()
