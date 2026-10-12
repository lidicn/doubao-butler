#!/usr/bin/env python3
"""生成 tree_diff.py 吃的三列清单（path raw_md5 归一md5），仓库根自动判定。

同一段算法在两棵树上都能跑 ⇒ ⛔ 手抄 shell 循环（shell 会吞 \\r、md5sum 会加 * 前缀）。
用法：
    python3 scripts/gen_e_list.py                # 输出到 stdout
    python3 scripts/gen_e_list.py > /tmp/e_list.txt
    python3 scripts/gen_e_list.py --root "E:\\NAS\\doubao-butler"
只读。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT, help="树根（默认＝本脚本所在仓库的上一级）")
    ap.add_argument("--sub", default="butler", help="扫描的子目录")
    args = ap.parse_args()
    walk = os.path.join(args.root, args.sub)
    if not os.path.isdir(walk):
        print("FAIL 目录不存在：%s" % walk, file=sys.stderr)
        return 3
    rows = []
    for dirpath, dirnames, filenames in os.walk(walk):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, args.root).replace("\\", "/")
            with open(p, "rb") as f:
                b = f.read()
            rows.append((rel, hashlib.md5(b).hexdigest(),
                         hashlib.md5(b.replace(b"\r\n", b"\n")).hexdigest()))
    rows.sort()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    for rel, raw, nrm in rows:
        print("%s %s %s" % (rel, raw, nrm))
    print("GENERATED=%d ROOT=%s" % (len(rows), args.root), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
