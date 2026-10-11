"""批201 DB25 门：`docker_tools.py` 的短路死代码（第三期审计报告 第一轮 · low）。

病灶原文（现读 `butler/integrations/docker_tools.py:108`）：

    time.sleep(2) if False else None  # 不 sleep，让调用方观察

`if False` 让 `time.sleep` 永不求值⇒这一枚 NameError 是**惰性**的（本文件⛔ `import time`），
所以它不崩、只是永远不生效——正是台账里「看着像代码的注释」那一族。修法＝整行删掉，
保留下一行的 `inspect`（那才是真正在做的「验证容器状态」）。

⚠ 报告在只读副本 `db-patched` 上打补丁、原仓库未改（报告第 7 行自己写的）⇒
本门验的是**本树现状**，⛔ 拿报告里的「已修」当依据。

尺子校准两格（形状尺⛔ 只跑真文件）：应放行合成源码⇒0 命中；应咬合成源码⇒恰好 1 命中。
"""
import ast
import pathlib
import unittest

PROD = pathlib.Path(__file__).resolve().parents[1] / "butler" / "integrations" / "docker_tools.py"

DEAD_TERNARY = "if False else None"


def dead_ternary_lines(src: str) -> list[int]:
    """返回「短路死代码」行号；判据＝行内含 `if False else None`。"""
    return [i for i, ln in enumerate(src.splitlines(), 1) if DEAD_TERNARY in ln]


def unimported_module_refs(src: str, module: str) -> list[str]:
    """模块名在本文件被**属性访问**用到，却从未 `import` ⇒ 返回用到的行文本。"""
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    if module in imported:
        return []
    used = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == module):
            used.append(f"{node.lineno}")
    return used


class TestBatch201Db25DeadTernary(unittest.TestCase):

    # ── 生产面 ────────────────────────────────────────────
    def test_01_no_dead_shortcircuit_ternary_in_prod(self):
        src = PROD.read_text(encoding="utf-8")
        self.assertEqual(
            dead_ternary_lines(src), [],
            "docker_tools.py 仍有 `if False else None` 短路死代码（该行永不生效，"
            "且它引用的 time 从未 import）")

    def test_02_time_attribute_use_requires_import(self):
        src = PROD.read_text(encoding="utf-8")
        self.assertEqual(
            unimported_module_refs(src, "time"), [],
            "现读：`time.` 被用到但本文件⛔ `import time`⇒NameError 只是被 `if False` 压住")

    def test_03_restart_still_inspects_status_after_removal(self):
        """删死代码⛔ 顺手把真在做事的 inspect 腿删掉：那一句必须还在。"""
        src = PROD.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "restart")
        body_src = ast.get_source_segment(src, fn) or ""
        self.assertIn("inspect", body_src,
                      "restart() 里丢了 `inspect` 状态复核腿")
        self.assertIn('{{.State.Status}}', body_src,
                      "restart() 的 inspect 少了输出模板，状态会读成空串")

    # ── 尺子校准（两侧都要）────────────────────────────────
    def test_04_ruler_bites_synthetic(self):
        synth = ("import asyncio\n"
                 "async def f():\n"
                 "    time.sleep(2) if False else None\n"
                 "    return 1\n")
        self.assertEqual(dead_ternary_lines(synth), [3], "尺子咬不住合成病灶⇒它是个摆设")
        self.assertEqual(unimported_module_refs(synth, "time"), ["3"],
                         "尺子漏判未导入模块引用")

    def test_05_ruler_passes_clean_synthetic(self):
        synth = ("import asyncio\n"
                 "import time\n"
                 "async def f():\n"
                 "    await asyncio.sleep(2)\n"
                 "    return 1\n")
        self.assertEqual(dead_ternary_lines(synth), [])
        self.assertEqual(unimported_module_refs(synth, "time"), [])

    def test_06_judge_is_not_a_tautology(self):
        """判据⛔ 恒真：把病灶原文塞进去必须变红。"""
        self.assertTrue(dead_ternary_lines("    time.sleep(2) if False else None\n"))
        self.assertFalse(dead_ternary_lines("    time.sleep(2)\n"))


if __name__ == "__main__":
    unittest.main()
