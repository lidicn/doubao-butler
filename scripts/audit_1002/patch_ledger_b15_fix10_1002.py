r"""批15 文书更正（一次性）：用**改过的节文件**整段替换台账里那段 §16。

为什么要它：§16-10 那条复跑腿我落盘时写成「在权威树跑」，现读证据（`workorders/readings/1003b15/ledger_1610_state.txt`）：
  - 权威树 `python3 -B scripts/audit_1002/verify_agents_1002.py` ⇒ `No such file or directory`，rc=2
  - 权威树⛔ `doc/审计报告/`（那把尺要读 4 份报告的 EOF 与合并表正文）
⇒ 那句话是错的。改法⛔ 再抄一遍散文（抄一遍就多一处会漂移的真源）：**节文件是唯一真源**，
本件把台账里从 `## §16 ` 起的那一段整体换成节文件原文，之前的字节必须逐字节不动。

用法（权威树根）：
    python3 -B scripts/audit_1002/patch_ledger_b15_fix10_1002.py          # DRY
    python3 -B scripts/audit_1002/patch_ledger_b15_fix10_1002.py --apply  # 落一次，重跑必 raise
"""
import hashlib
import sys

TARGET = "doc/审计报告分诊台账-20261002.md"
SECTION = "scripts/audit_1002/ledger_1002_batch15_section.md"
BASE_MD5 = "1c164b570a389b537418b53269882aff"
BASE_LINES = 1426
BASE_BYTES = 159928
BASE_H2 = 16
BASE_H3 = 63
HEAD = "## §16 "
SENTINEL = "——⚠ 只在 E 盘工作副本跑，见下"
KEEP = ("### §16-10", "### §16-9", "### §15-8", "§14-7", "cache_filename")


def md5(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def heads(t):
    ls = t.splitlines()
    return (len([l for l in ls if l.startswith("## ")]),
            len([l for l in ls if l.startswith("### ")]))


def main():
    apply = "--apply" in sys.argv[1:]
    raw = open(TARGET, "rb").read()
    if raw.count(b"\r"):
        raise SystemExit("BASELINE_CR|%d" % raw.count(b"\r"))
    text = raw.decode("utf-8")
    sec_raw = open(SECTION, "rb").read()
    if sec_raw.count(b"\r"):
        raise SystemExit("SECTION_CR|%d" % sec_raw.count(b"\r"))
    sec = sec_raw.decode("utf-8")
    if not sec.startswith(HEAD) or not sec.endswith("\n"):
        raise SystemExit("SECTION_SHAPE|%s|%s" % (sec[:8], sec.endswith("\n")))
    if SENTINEL not in sec:
        raise SystemExit("SECTION_MISSING_FIX|%s" % SENTINEL)
    if "$(" in sec:
        raise SystemExit("BASH_COMMAND_SUB_IN_PROSE")

    if SENTINEL in text:
        raise SystemExit("ALREADY_APPLIED|%s" % TARGET)
    if md5(text) != BASE_MD5:
        raise SystemExit("BASELINE_MD5|%s(期望%s)" % (md5(text), BASE_MD5))
    if len(text.splitlines()) != BASE_LINES or len(raw) != BASE_BYTES:
        raise SystemExit("BASELINE_SHAPE|行=%d 字节=%d" % (len(text.splitlines()), len(raw)))
    if heads(text) != (BASE_H2, BASE_H3):
        raise SystemExit("BASELINE_HEADS|%s(期望%s)" % (heads(text), (BASE_H2, BASE_H3)))
    if text.count(HEAD) != 1:
        raise SystemExit("HEAD_HITS|%d(必须恰为 1)" % text.count(HEAD))

    idx = text.index(HEAD)
    prefix = text[:idx]
    new = prefix + sec
    if not new.startswith(prefix):
        raise SystemExit("PREFIX_CHANGED")          # §16 之前的字节逐字节不动
    if not new.endswith(sec):
        raise SystemExit("TAIL_NOT_SECTION")
    if text[idx:] == sec:
        raise SystemExit("NO_CHANGE|节文件与台账那段已相同")
    for must in (SENTINEL, "复跑归 E"):
        if must not in new:
            raise SystemExit("MUST_PRESENT|%s" % must)
    for probe in KEEP:
        if new.count(probe) < 1:
            raise SystemExit("COLLATERAL_LOST|%s" % probe)
    if heads(new) != (BASE_H2, BASE_H3):
        raise SystemExit("HEADS_AFTER|%s(期望%s)" % (heads(new), (BASE_H2, BASE_H3)))
    if new.count("\r"):
        raise SystemExit("CR_INTRODUCED")
    if not new.endswith("\n"):
        raise SystemExit("ENDNL_LOST")

    payload = new.encode("utf-8")
    print("%s |%s 行 %d->%d 字节 %d->%d 段 %s md5 %s->%s（§16 之前 %d 字节未动）"
          % ("APPLY" if apply else "DRY", TARGET, len(text.splitlines()), len(new.splitlines()),
             len(raw), len(payload), heads(new), BASE_MD5, md5(new), len(prefix.encode("utf-8"))))
    if apply:
        with open(TARGET, "wb") as fh:
            fh.write(payload)
        after = open(TARGET, "rb").read()
        if after != payload:
            raise SystemExit("WRITE_VERIFY")
        if not after.decode("utf-8").endswith(sec):
            raise SystemExit("TAIL_VERIFY|台账尾段 != 节文件")
        print("APPLIED|%s|%s|%d 行|%d 字节|段 %s|TAIL_EQ_SECTION|True"
              % (TARGET, md5(new), len(new.splitlines()), len(payload), heads(new)))
    else:
        print("DRY_ONLY|未落盘")


main()
