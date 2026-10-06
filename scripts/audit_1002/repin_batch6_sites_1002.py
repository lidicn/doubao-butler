"""只读重钉：批10（裁②）把 notify/router.py 与 skills/runner.py 各推了 11/13 行，
SITES 里钉的两枚 `notify` 落到别处 ⇒ 批6 门判 DRIFT/UNKNOWN。本探针把每枚名字的全部
Call 落点摆出来，并带上包围函数与接收者写法，由我逐枚点开确认是不是原来那枚。"""
from __future__ import annotations

import ast
import pathlib
import sys

REPO = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
TARGETS = [
    ("notify/router.py", 309, "notify"),
    ("skills/runner.py", 411, "notify"),
]


def enclosing(tree, node):
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.p_ = parent
    cur = node
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return f"{'async ' if isinstance(cur, ast.AsyncFunctionDef) else ''}def {cur.name}:{cur.lineno}"
        cur = getattr(cur, "p_", None)
    return "<module>"


def recv(c):
    f = c.func
    if isinstance(f, ast.Attribute):
        v = f.value
        s = ast.unparse(v) if not isinstance(v, ast.Call) else ast.unparse(v)[:40] + "()"
        return f"{s}.{f.attr}"
    return ast.unparse(f)


for rel, old, name in TARGETS:
    p = REPO / "butler" / rel
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Attribute) and n.func.attr == name)
                 or (isinstance(n.func, ast.Name) and n.func.id == name))]
    print(f"FILE|{rel}|old_pin={old}|calls_named_{name}={len(hits)}|lines={len(src.splitlines())}")
    for h in hits:
        print(f"  CAND|{rel}:{h.lineno}|delta={h.lineno - old:+d}|recv={recv(h)}|in={enclosing(tree, h)}")
        line = src.splitlines()[h.lineno - 1].strip()
        print(f"       |{line[:90]}")
