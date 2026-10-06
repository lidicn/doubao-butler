#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批 13 台账节：把 `scripts/audit_1002/ledger_1002_batch13_section.md` **追加**进分诊台账。

纪律（与 §12／§13 那两笔同一形制）：
- append-only：脚本 assert「旧文本是新文本的前缀」，⛔ 改写既有任何一行。
- 施加前的基线五格（md5／行／字节／CR／`^## `＋`^### `）现读并钉死，对不上就拒跑；
  施加**后**的字节数与段数⛔ 写进台账正文（那是会被下一节作废的自指数），只指两处＝
  本笔 commit 正文的 `APPLIED|` 行，和 `workorders/readings/1002b13/ledger_after.txt`。
- 一次性：追加过一次后基线就变了 ⇒ 重跑必 raise；另加一条「`## §14 ` 已在册就拒跑」的显式闸。
"""
import argparse
import hashlib
import sys

LEDGER = "doc/审计报告分诊台账-20261002.md"
SECTION = "scripts/audit_1002/ledger_1002_batch13_section.md"

BASE_MD5 = "305ec8958530ec36ecdadaa2388b23be"
BASE_LINES = 970
BASE_BYTES = 114500
BASE_CR = 0
BASE_H2 = 13
BASE_H3 = 35


def md5(b):
    return hashlib.md5(b).hexdigest()


def cr_count(raw):
    # CR 只二进制数：Git Bash 里 `grep -c $'\r'` 的 CR 会被吞成空模式＝匹配全部行
    return raw.count(b"\r\n")


def shape(text, raw):
    return (len(text.splitlines()), len(raw), cr_count(raw),
            sum(1 for l in text.splitlines() if l.startswith("## ")),
            sum(1 for l in text.splitlines() if l.startswith("### ")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    raw = open(LEDGER, "rb").read()
    text = raw.decode("utf-8")
    lines, nbytes, cr, h2, h3 = shape(text, raw)
    got = md5(raw)

    if got != BASE_MD5:
        raise SystemExit("BASELINE_MISMATCH|%s|want=%s|got=%s ⇒ 台账不是我预期的那一版，拒跑"
                         % (LEDGER, BASE_MD5, got))
    if (lines, nbytes, cr, h2, h3) != (BASE_LINES, BASE_BYTES, BASE_CR, BASE_H2, BASE_H3):
        raise SystemExit("BASELINE_SHAPE|行=%d(%d) 字节=%d(%d) CR=%d(%d) H2=%d(%d) H3=%d(%d)"
                         % (lines, BASE_LINES, nbytes, BASE_BYTES, cr, BASE_CR, h2, BASE_H2,
                            h3, BASE_H3))
    if "\n## §14 " in "\n" + text:
        raise SystemExit("ALREADY_APPLIED|§14 已在册（重跑＝追加第二份同号节）")

    sec_raw = open(SECTION, "rb").read()
    sec = sec_raw.decode("utf-8")
    if not sec.startswith("## §14 "):
        raise SystemExit("SECTION_HEAD|%s" % sec.splitlines()[0][:60])
    if not sec.endswith("\n"):
        raise SystemExit("SECTION_ENDNL")
    sec_h2 = sum(1 for l in sec.splitlines() if l.startswith("## "))
    sec_h3 = sum(1 for l in sec.splitlines() if l.startswith("### "))
    if (sec_h2, sec_h3) != (1, 8):
        raise SystemExit("SECTION_SHAPE|H2=%d(须1) H3=%d(须8)" % (sec_h2, sec_h3))
    if "\r" in sec_raw.decode("utf-8", "ignore"):
        raise SystemExit("SECTION_CR|%d" % cr_count(sec_raw))
    for banned in ("$(", "```python\n$"):
        if banned in sec:
            raise SystemExit("SECTION_SHELL_INJECTION|%s" % banned)

    new = text.rstrip("\n") + "\n\n" + sec
    if not new.startswith(text):
        raise SystemExit("NOT_APPEND_ONLY|旧文本不是新文本的前缀")
    if not new.endswith(sec):
        raise SystemExit("SUFFIX_MISMATCH|追加内容与节件字节不一致")

    nlines, nbytes2, ncr, nh2, nh3 = shape(new, new.encode("utf-8"))
    if nh2 != BASE_H2 + 1 or nh3 != BASE_H3 + sec_h3:
        raise SystemExit("HEADINGS|H2=%d(须%d) H3=%d(须%d)"
                         % (nh2, BASE_H2 + 1, nh3, BASE_H3 + sec_h3))
    if nlines <= lines or nbytes2 <= nbytes or ncr != 0 or not new.endswith("\n"):
        raise SystemExit("SHAPE|行=%d 字节=%d CR=%d 末行换行=%s" % (nlines, nbytes2, ncr, new.endswith("\n")))

    print("DRY |%s 行 %d->%d 字节 %d->%d 段 %d->%d/%d->%d"
          % (LEDGER, lines, nlines, nbytes, nbytes2, h2, nh2, h3, nh3))
    print("DRY |section md5=%s 字节=%d H3=%d" % (md5(sec_raw), len(sec_raw), sec_h3))

    if not args.apply:
        print("DRY only，未写盘。加 --apply 才落。")
        return 0

    with open(LEDGER, "wb") as fh:
        fh.write(new.encode("utf-8"))
    raw2 = open(LEDGER, "rb").read()
    txt2 = raw2.decode("utf-8")
    l2, b2, c2, h22, h32 = shape(txt2, raw2)
    print("APPLIED|%s|md5=%s|行=%d|字节=%d|CR=%d|末行换行=%s|^## =%d|^### =%d"
          % (LEDGER, md5(raw2), l2, b2, c2, txt2.endswith("\n"), h22, h32))
    return 0


if __name__ == "__main__":
    sys.exit(main())
