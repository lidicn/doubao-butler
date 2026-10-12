"""批197/199 的**注册腿**门（DB-03 §三 那张单，出资人 2026-10-12 19:5x 签「要」）：三枚更新端点必须真的挂进 app。

被 `28310ab` 删掉的是两行（现证：`git show 28310ab -- butler/app.py` 的 `-    update_routes,` 与
`-        *update_routes.routes(),`），而 10-11 那两批门**只判模块本身、判不住注册**⇒「门在绿」⛔ 等于
「功能腿在」（＝本席在 DB-02 回执 D3 自报的那格门缺口）。

口径：验收⛔ 用 `app.routes` 实枚举——那要 import `butler/app.py`，二次 import 会在停摆的写面上开连接
（在册禁令）。⇒ 拆成两腿合证：① `update_routes.routes()` 真对象给出三枚端点（路径＋方法逐一对）；
② `butler/app.py` 的 AST 里，`_build_app()` 的 `routes = [...]` 确有 `*update_routes.routes()` 那枚 spread，
且 import 段确有该模块。负控＝从真文件里摘掉那一行喂同一把尺，必须报缺（第 5 格）。

跑法：`python -B -m unittest tests.test_audit_1012_b205_update_routes_registration`（一套一进程）。
"""
from __future__ import annotations

import ast
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from butler.api import update_routes as ur  # noqa: E402

APP_PY = REPO_ROOT / "butler" / "app.py"
MODULE = "update_routes"
EXPECTED = [
    ("/api/system/update/check", "GET"),
    ("/api/system/update", "POST"),
    ("/api/system/update/status", "GET"),
]


def registered_modules(src: str) -> set[str]:
    """`_build_app()` 里 `routes = [...]` 中 `*<mod>.routes()` 那批 spread 的模块名。"""
    tree = ast.parse(src)
    out: set[str] = set()
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_build_app"):
        for assign in (a for a in ast.walk(fn) if isinstance(a, ast.Assign)):
            for t in assign.targets:
                if not (isinstance(t, ast.Name) and t.id == "routes"):
                    continue
                if not isinstance(assign.value, ast.List):
                    continue
                for el in assign.value.elts:
                    if not isinstance(el, ast.Starred):
                        continue
                    call = el.value
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                        base = call.func.value
                        if isinstance(base, ast.Name) and call.func.attr == "routes":
                            out.add(base.id)
    return out


def api_imports(src: str) -> set[str]:
    """`from butler.api import (...)` 段里的模块名。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module == "butler.api":
            out.update(a.name for a in node.names)
    return out


class UpdateRoutesShape(unittest.TestCase):
    def test_routes_expose_three_endpoints(self):
        got = [(r.path, set(r.methods or [])) for r in ur.routes()]
        self.assertEqual([p for p, _ in got], [p for p, _ in EXPECTED],
                         "端点名单或顺序对不上（⛔ 只注册不验＝假绿）")
        for (path, want), (_gpath, have) in zip(EXPECTED, got):
            self.assertIn(want, have, f"{path} 缺方法 {want}")
            # starlette 对 GET 自动附 HEAD（`Route.__init__`），除它以外⛔ 多开verb
            self.assertEqual(have - {want, "HEAD"}, set(), f"{path} 多出方法")


class RegistrationLeg(unittest.TestCase):
    def setUp(self):
        self.src = APP_PY.read_text(encoding="utf-8")

    def test_app_py_imports_update_routes(self):
        self.assertIn(MODULE, api_imports(self.src),
                      "app.py 的 butler.api import 段里没有 update_routes")

    def test_app_py_registers_update_routes_spread(self):
        self.assertIn(MODULE, registered_modules(self.src),
                      "app.py `_build_app()` 的 routes 清单里没有 `*update_routes.routes()`")


class RulerCalibration(unittest.TestCase):
    """形状尺双向校准：⛔ 只跑真文件的尺（恒绿与恒红都不改变任何读数）。"""

    def setUp(self):
        self.src = APP_PY.read_text(encoding="utf-8")

    def _stripped(self) -> str:
        lines = [ln for ln in self.src.splitlines()
                 if ln.strip() != f"*{MODULE}.routes(),"]
        self.assertEqual(len(lines), len(self.src.splitlines()) - 1,
                         "负控没咬到那一行——摘行前后行数差≠1")
        return "\n".join(lines) + "\n"

    def test_negative_control_removing_the_line_reports_missing(self):
        self.assertNotIn(MODULE, registered_modules(self._stripped()),
                         "摘掉注册行后这把尺仍判「在册」＝恒绿废尺")

    def test_real_file_still_reports_present(self):
        self.assertIn(MODULE, registered_modules(self.src))

    def test_import_line_dropped_reports_missing(self):
        lines = [ln for ln in self.src.splitlines() if ln.strip() != f"{MODULE},"]
        self.assertEqual(len(lines), len(self.src.splitlines()) - 1,
                         "负控没咬到 import 行——摘行前后行数差≠1")
        self.assertNotIn(MODULE, api_imports("\n".join(lines) + "\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
