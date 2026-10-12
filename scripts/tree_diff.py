#!/usr/bin/env python3
"""E 盘镜像 vs 权威部署树：.py 逐文件 md5 对账（纪律 1 的可复跑尺）。

用法（在 NAS 上跑）：
    python3 scripts/tree_diff.py --e-list /tmp/e_list.txt
E 侧清单在 Windows/Git Bash 里现生成（同一算法，两把尺不吃各自的默认值）：
    cd /e/NAS/doubao-butler && find butler -name "*.py" -type f | sort | while read -r f; do
      raw=$(md5sum < "$f" | cut -d' ' -f1); nrm=$(tr -d '\\r' < "$f" | md5sum | cut -d' ' -f1)
      printf '%s %s %s\\n' "$f" "$raw" "$nrm"; done > /tmp/e_list.txt
清单每行三列：路径 raw_md5 归一换行后 md5。EOL 归一是必须的——同一棵树里 LF/CRLF 混装是常态，
不归一会把"换行血统不同"读成"内容漂移"（09-30 实测：62 枚真漂移里只 1 枚是纯换行差）。
本脚本只读，不写任何被比对的文件。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def scan(walk_dir: str, prefix_from: str) -> dict[str, tuple[str, str]]:
    """键一律相对 prefix_from 生成 ⇒ 两侧清单必须用同一把前缀，否则 COMMON 恒为 0。"""
    out: dict[str, tuple[str, str]] = {}
    for dirpath, dirnames, filenames in os.walk(walk_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, prefix_from).replace("\\", "/")
            with open(p, "rb") as f:
                b = f.read()
            out[rel] = (hashlib.md5(b).hexdigest(),
                        hashlib.md5(b.replace(b"\r\n", b"\n")).hexdigest())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--e-list", required=True, help="E 盘侧三列清单：path raw_md5 norm_md5")
    ap.add_argument("--full", action="store_true", help="打印全量点名清单")
    args = ap.parse_args()

    nas = scan(os.path.join(REPO_ROOT, "butler"), REPO_ROOT)
    e: dict[str, tuple[str, str]] = {}
    with open(args.e_list, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) == 3:
                e[parts[0]] = (parts[1], parts[2])
            elif parts:
                print("BAD_LINE %r" % line.rstrip("\n"), file=sys.stderr)

    only_e = sorted(set(e) - set(nas))
    only_n = sorted(set(nas) - set(e))
    common = sorted(set(e) & set(nas))
    raw_diff = [p for p in common if e[p][0] != nas[p][0]]
    eol_only = [p for p in raw_diff if e[p][1] == nas[p][1]]
    real = [p for p in raw_diff if e[p][1] != nas[p][1]]

    if not common and e and nas:
        # 两侧都有文件却零交集 ⇒ 一定是键的前缀不一致（09-30 第一版就这么假绿过一次），
        # REAL_DRIFT=0 在这种情况下没有任何含义。
        print("SELF_CHECK=PREFIX_MISMATCH（两侧非空但 COMMON=0，判据作废）", file=sys.stderr)
        return 2
    print("SELF_CHECK=COMMON_NONZERO rc_will_be_meaningful=1")
    print("REPO=%s E_PY=%d NAS_PY=%d COMMON=%d" % (REPO_ROOT, len(e), len(nas), len(common)))
    print("IDENTICAL_BYTES=%d EOL_ONLY=%d REAL_DRIFT=%d ONLY_E=%d ONLY_NAS=%d"
          % (len(common) - len(raw_diff), len(eol_only), len(real), len(only_e), len(only_n)))
    for tag, items in (("ONLY_E", only_e), ("ONLY_NAS", only_n)):
        print("--- %s 全量 %d ---%s" % (tag, len(items),
              "" if args.full else "（截断 12，加 --full 看全量）"))
        for p in (items if args.full else items[:12]):
            print("  %s %s" % (tag, p))
    print("--- REAL_DRIFT 全量 %d ---%s" % (len(real),
          "" if args.full else "（截断 12，加 --full 看全量）"))
    for p in (real if args.full else real[:12]):
        print("  D %s %s -> %s" % (p, e[p][1][:8], nas[p][1][:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
