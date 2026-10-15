"""批221 第二遍：全仓裸铸形门槛尺（AST 版，⛔ grep 形状）。

报告第五期 §三 的 44 条里，参数／载荷裸铸形一族第一遍只扫了**报告点名的落点**。
本尺把同一把尺套到整棵 `butler/` 上：只要还有 `int(request…get( ))`／`bool(body.get( ))`／
`float(payload.get( ))` 这种形制，就地红——⛔ 用「报告没写它」当免检理由。

形制判定走 AST（不是文本 grep）：
 - 注释与 docstring 里的改前形制**天然不吃**（第一遍用的文本尺会误报 `param.py` 自己的说明行）；
 - 只看 `int|float|bool` 的**第一个实参子树**里出现 `.get()`、且 `.get` 的接收者根名在
   `request|body|d|payload` 里的调用（`str()`／`len()` 这类不崩的函数⛔ 算命中）。

两把校准尺（判例：形状尺⛔ 只跑真文件，必须配放行＋咬合两侧）：
 - 合成「应咬」源码 ⇒ 恰好 4 枚命中；
 - 合成「应放行」源码（走 param 助手＋注释里带同样字面）⇒ 0 枚命中。
"""
from __future__ import annotations

import ast
import io
import pathlib
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
PKG = REPO / "butler"

CASTS = ("int", "float", "bool")
ROOTS = ("request", "body", "d", "payload")


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    return ""


def bare_casts(src: str):
    """返回该源码里裸铸形调用的行号列表。"""
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id in CASTS):
            continue
        if not node.args:
            continue
        # 实参整棵子树里找 `.get()`：`float(payload.get("confidence") or 0.0)` 这种
        # 加了兜底梯的形制同属一族（`or` 只挡 None/空串，挡不住 `"abc"`⇒照崩）。
        inner_is_get = False
        for sub in ast.walk(node.args[0]):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "get"
                    and _root_name(sub.func.value) in ROOTS):
                inner_is_get = True
                break
        if inner_is_get:
            hits.append(node.lineno)
    return sorted(hits)


def scan_tree(root: pathlib.Path):
    out = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        src = io.open(str(path), encoding="utf-8", errors="replace").read()
        try:
            hits = bare_casts(src)
        except SyntaxError:
            continue
        if hits:
            out[str(path.relative_to(root)).replace("\\", "/")] = hits
    return out


BITE_FIXTURE = '''
async def list_rows(request, body, payload):
    limit = int(request.query_params.get("limit", 50))
    stream = bool(body.get("stream"))
    conf = float(payload.get("confidence", 0.5))
    ladder = float(payload.get("confidence") or 0.0)
    return limit, stream, conf, ladder
'''

SPARE_FIXTURE = '''
"""改前形制 `int(request.path_params.get("id", "0"))` 写在说明里不算命中。"""


async def list_rows(request, body, payload):
    # int(request.query_params.get("limit", 50))  ← 注释里的旧形制不算
    limit = query_num(request, "limit", default=50, minimum=1, maximum=500)
    stream = body_bool(body, "stream", default=False)
    conf = to_float(payload.get("confidence"), "confidence", default=0.5)
    room = str(body.get("room", ""))          # str() 不会崩，⛔ 算命中
    keys = len(payload.get("k") or [])        # 非铸形函数，⛔ 算命中
    return limit, stream, conf, room, keys
'''


class TreeCastGateTest(unittest.TestCase):

    def test_ruler_bites_the_bare_form(self):
        self.assertEqual(bare_casts(BITE_FIXTURE), [3, 4, 5, 6],
                         "本尺恒假⇒全仓 0 命中毫无意义")

    def test_ruler_spares_the_helper_and_the_prose(self):
        self.assertEqual(bare_casts(SPARE_FIXTURE), [],
                         "助手形制或注释里的字面被读成命中⇒尺恒红")

    def test_ruler_finds_a_planted_file_in_a_temp_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "m.py").write_text(BITE_FIXTURE, encoding="utf-8")
            got = scan_tree(root)
            self.assertEqual(got, {"m.py": [3, 4, 5, 6]}, got)

    def test_whole_tree_has_no_bare_cast(self):
        if not PKG.is_dir():
            self.skipTest(f"源码树不在位：{PKG}")
        got = scan_tree(PKG)
        total = sum(len(v) for v in got.values())
        detail = "\n".join(f"  {k}:{v}" for k, v in sorted(got.items()))
        self.assertEqual(got, {}, "全仓还剩 %d 枚裸铸形：\n%s" % (total, detail))


if __name__ == "__main__":
    unittest.main()
