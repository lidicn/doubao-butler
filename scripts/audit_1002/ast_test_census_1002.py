"""可复跑的 AST 测试定义普查（台账 §9-3／§10-5 那句「AST 里的 test 定义总数」的尺本体）。

⛔ `grep -c '^def test'`：那把粗尺漏 `async def`（分母 153 vs 真值 160 那次教训）。
本尺按 AST 数 `FunctionDef`／`AsyncFunctionDef` 且名以 `test` 开头，并分「类内」与「模块级」两口径：
模块级那批不经 `unittest discover` 收（不 import pytest 的会被静默收下、一条不跑），
所以 `ast_total != discover 的 Ran=` 是**预期**，差额＝模块级＋宿主缺包不可导入的类内方法。
"""
from __future__ import annotations

import ast
import pathlib
import sys


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    root = pathlib.Path(args[0] if args else "tests")
    if not root.is_dir():
        raise SystemExit(f"目录不存在：{root}")
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    in_class = 0
    module_level = 0
    per_file: list[tuple[str, int, int]] = []
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            raise SystemExit(f"解析失败 {p}: {e}")
        cls_nodes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                cls_nodes.update(id(m) for m in node.body)
        n_cls = n_mod = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                if id(node) in cls_nodes:
                    n_cls += 1
                else:
                    n_mod += 1
        in_class += n_cls
        module_level += n_mod
        if n_cls or n_mod:
            per_file.append((p.relative_to(root).as_posix(), n_cls, n_mod))
    total = in_class + module_level
    show_all = "--show-all" in argv
    print(f"FILES_SCANNED|{len(files)}")
    print(f"AST_TOTAL={total}")
    print(f"IN_CLASS={in_class}")
    print(f"MODULE_LEVEL={module_level}")
    for name, n_cls, n_mod in per_file:
        if show_all:
            print(f"FILE|{name}|in_class={n_cls}|module_level={n_mod}")
        elif n_mod:
            print(f"MODULE_LEVEL_FILE|{name}|in_class={n_cls}|module_level={n_mod}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
