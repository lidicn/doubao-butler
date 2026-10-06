#!/usr/bin/env python3
r"""批19 台账追加器（append-only，一次性）。往 `doc/审计报告分诊台账-20261002.md` 的 EOF 加 §20。

闸的顺序＝先证"没做过第二遍"，再证"基底还是我量过的那一份"，再证"要加的这段还是我写的那一份"，
最后 WRITE_VERIFY 反证写进去了。任一闸不过就 raise 且⛔ 动笔（raise 在写之前＝重跑安全）。

一次性亲证：先在 `/tmp/b19_ledger_dry` 的复制树上跑一遍（第二遍必须 ALREADY_APPEND），才碰现树。

用法：python3 -B scripts/audit_1002/patch_ledger_batch19_1002.py [repo_root]
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

REPO = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
LEDGER = REPO / "doc/审计报告分诊台账-20261002.md"
SECTION = REPO / "scripts/audit_1002/ledger_1002_batch19_section.md"

MARKER = "## §20 批19 · "

# —— 追加前基底（2026-10-02T21:06:05Z 现读，权威树 HEAD fee501a）——
BASE_MD5 = "e645894774d28097f591ba046da2d430"
BASE_LINES = 1969
BASE_BYTES = 214675
BASE_H2 = 19
BASE_H3 = 99

# —— 本节文件（追加前自量 2026-10-02T21:08:12Z；文书里那句"md5 钉在闸里"指的就是这三枚）——
SECTION_MD5 = "70ec26a57aea7e614ed41bc3d5f91cc9"
SECTION_LINES = 133
SECTION_BYTES = 20593
SECTION_H2 = 1
SECTION_H3 = 12


def die(why):
    raise SystemExit("PATCH_FAIL|" + why)


def binread(p):
    if not p.is_file():
        die("MISSING|%s" % p)
    return p.read_bytes()


def cr_count(b):
    return b.count(b"\r")


def head_counts(text, prefix):
    return sum(1 for l in text.split("\n") if l.startswith(prefix))


def main():
    led_b = binread(LEDGER)
    sec_b = binread(SECTION)

    # 1) 一次性：已经在册就直接崩，⛔ 追加第二遍
    if MARKER in led_b.decode("utf-8"):
        die("ALREADY_APPEND|%s" % MARKER)

    # 2) 基底还是不是量过的那一份
    if cr_count(led_b):
        die("LEDGER_CR!=0(%d)" % cr_count(led_b))
    if not led_b.endswith(b"\n") or led_b.endswith(b"\n\n"):
        die("LEDGER_TAIL_NL(单 \\n 结尾才符合 §8–§19 追加形状)")
    if len(led_b) != BASE_BYTES:
        die("LEDGER_BYTES %d != %d" % (len(led_b), BASE_BYTES))
    if led_b.count(b"\n") != BASE_LINES:
        die("LEDGER_LINES %d != %d" % (led_b.count(b"\n"), BASE_LINES))
    got_md5 = hashlib.md5(led_b).hexdigest()
    if got_md5 != BASE_MD5:
        die("LEDGER_MD5 %s != %s" % (got_md5, BASE_MD5))

    # 3) 要加的这段：形状＋指纹
    if cr_count(sec_b):
        die("SECTION_CR!=0(%d)" % cr_count(sec_b))
    if not sec_b.endswith(b"\n") or sec_b.endswith(b"\n\n"):
        die("SECTION_TAIL_NL")
    if len(sec_b) != SECTION_BYTES:
        die("SECTION_BYTES %d != %d" % (len(sec_b), SECTION_BYTES))
    if hashlib.md5(sec_b).hexdigest() != SECTION_MD5:
        die("SECTION_MD5(本节又被改过，先重取自量再同步本闸)")
    sec_t = sec_b.decode("utf-8")
    sec_ls = sec_t.split("\n")
    if head_counts(sec_t, "## ") != SECTION_H2:
        die("SECTION_H2 %d != %d(⛔ 一段整节只许一枚 `^## `)" % (head_counts(sec_t, "## "), SECTION_H2))
    if head_counts(sec_t, "### ") != SECTION_H3:
        die("SECTION_H3 %d != %d" % (head_counts(sec_t, "### "), SECTION_H3))
    if any(l.startswith("## §") and not l.startswith("## §20 ") for l in sec_ls):
        die("SECTION_CROSSSHEET(本节里出现了别节的 `^## §`)")
    # 小节号连号：§20-0 … §20-11，缺号＝我改标题时把编号打断了
    nums = []
    for l in sec_ls:
        if l.startswith("### §20-"):
            tail = l[len("### §20-"):]
            n = ""
            for ch in tail:
                if ch.isdigit():
                    n += ch
                else:
                    break
            nums.append(int(n) if n else -1)
    if nums != list(range(SECTION_H3)):
        die("SECTION_SUBHEADS %r != 0..%d" % (nums, SECTION_H3 - 1))
    # 本节必须真的引用了权威树才有的路径（防我把 E 盘专属目录当权威树写进台账）
    if "doc/审计报告/" in sec_t and "E 盘" not in sec_t:
        die("SECTION_PATH_CLAIM(引用 doc/审计报告/ 却没写明它在 E 盘)")

    # 4) 施加：只在 EOF 加一段，中间隔一枚空行（§18→§19 同形制）
    new_b = led_b + b"\n" + sec_b
    if not new_b.startswith(led_b):
        die("NOT_APPEND_ONLY")

    led_t = led_b.decode("utf-8")
    if led_t not in sec_t and not new_b.decode("utf-8").startswith(led_t):
        die("PREFIX_LOST(旧文本必须是新文本的前缀)")

    LEDGER.write_bytes(new_b)

    # 5) WRITE_VERIFY：写回盘上再读一遍，四把独立尺
    back = binread(LEDGER)
    bt = back.decode("utf-8")
    want_bytes = BASE_BYTES + 1 + SECTION_BYTES
    want_lines = BASE_LINES + 1 + SECTION_LINES
    checks = [
        ("cr0", cr_count(back) == 0),
        ("bytes", len(back) == want_bytes),
        ("lines", back.count(b"\n") == want_lines),
        ("h2", head_counts(bt, "## ") == BASE_H2 + SECTION_H2),
        ("h3", head_counts(bt, "### ") == BASE_H3 + SECTION_H3),
        ("marker_once", bt.count(MARKER) == 1),
        ("prefix", bt.startswith(led_t)),
        ("tail_nl", back.endswith(b"\n") and not back.endswith(b"\n\n")),
        ("section_present", sec_t in bt),
    ]
    bad = [k for k, ok in checks if not ok]
    if bad:
        die("WRITE_VERIFY|%s" % ",".join(bad))
    print("APPLIED|ledger=%s" % LEDGER.name)
    print("  before md5=%s lines=%d bytes=%d h2=%d h3=%d" % (BASE_MD5, BASE_LINES, BASE_BYTES, BASE_H2, BASE_H3))
    print("  section md5=%s lines=%d bytes=%d" % (SECTION_MD5, SECTION_LINES, SECTION_BYTES))
    print("  after md5=%s lines=%d bytes=%d h2=%d h3=%d CR=%d" % (
        hashlib.md5(back).hexdigest(), want_lines, want_bytes,
        head_counts(bt, "## "), head_counts(bt, "### "), cr_count(back)))
    print("  sep_blank_lines_added=1 all_checks=9/9")


if __name__ == "__main__":
    main()
