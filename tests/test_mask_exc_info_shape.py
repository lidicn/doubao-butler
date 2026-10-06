"""WO-ME-229 A-3 用例：logger.exception() 的 traceback 文本必须被【渲染后】遮掉。

被测树由环境变量 `BUTLER_LOGGING_SETUP` 选择（与 224 json 门同形）：
  未设 -> 盘上现座（镜像 filter 制：`MaskingFilter` 碰不到 record.exc_info 的渲染文本
           ⇒ test_10【应当红】，红即 A-3 泄漏台账，不是测试坏了）
  已设 -> importlib 按路径加载仓内 tmp 候选副本（R11a：`MaskingFormatter` 在 format
           期对最终字符串含 traceback 做遮罩 ⇒ 应绿）
分母锚点：本文件 Test* 类体内 test_* 方法 == 4，全同步（async 进 TestCase = 静默判 PASS）。
"""
from __future__ import annotations

import ast
import importlib.util
import io
import logging
import os
import unittest
import uuid

ENV_VAR = "BUTLER_LOGGING_SETUP"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISK_MODULE = os.path.realpath(os.path.join(REPO_ROOT, "butler", "logging_setup.py"))
TMP_ROOT = os.path.realpath(os.path.join(REPO_ROOT, "tmp"))


def _load_module():
    p = os.environ.get(ENV_VAR)
    if p:
        p = os.path.realpath(os.path.abspath(p))
        spec = importlib.util.spec_from_file_location("butler_logging_setup_candidate", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, "candidate", p
    import butler.logging_setup as m  # noqa: F401  盘上现座
    return m, "deployed-disk", os.path.realpath(getattr(m, "__file__"))


MODULE, TREE, MODULE_FILE = _load_module()


def _sentinel(length: int = 12) -> str:
    v = ("SENT" + uuid.uuid4().hex)[:length]
    assert len(v) == length
    return v


def _render_exception(msg: str, secret: str) -> str:
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(MODULE.MaskingFilter())
    # 与 224 门同款：candidate 树必须挂它自己的 MaskingFormatter，缺了=门假绿，直接错
    formatter = MODULE.MaskingFormatter if TREE == "candidate" else getattr(MODULE, "MaskingFormatter", None)
    h.setFormatter((formatter or logging.Formatter)("%(message)s"))
    lg = logging.getLogger("exc229_" + uuid.uuid4().hex[:8])
    lg.handlers = [h]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    try:
        raise ValueError("Authorization=" + secret)
    except ValueError:
        lg.exception(msg)
    return buf.getvalue()


class TestMaskExcInfoShape(unittest.TestCase):

    def test_00_provenance_eats_the_right_tree(self):
        self.assertEqual(os.path.basename(MODULE_FILE), "logging_setup.py")
        self.assertTrue(os.path.realpath(MODULE_FILE).startswith(REPO_ROOT + os.sep),
                        f"被测文件不在仓内：{MODULE_FILE}")
        self.assertTrue(hasattr(MODULE, "MaskingFilter"))
        under_tmp = os.path.realpath(MODULE_FILE).startswith(TMP_ROOT + os.sep)
        if TREE == "deployed-disk":
            self.assertEqual(os.path.realpath(MODULE_FILE), DISK_MODULE)
            self.assertFalse(under_tmp, "盘上模式禁止吃 tmp 副本")
        elif TREE == "candidate":
            self.assertTrue(under_tmp, f"candidate 必须指向仓内 tmp 候选副本，实到 {MODULE_FILE}")
        else:  # pragma: no cover
            self.fail(f"未知树标记 {TREE}")

    def test_01_selfcheck_negative_probe(self):
        class _Canary(unittest.TestCase):
            def test_deliberately_wrong(self):
                self.assertEqual(2, 3)

        result = unittest.TestResult()
        unittest.TestLoader().loadTestsFromTestCase(_Canary).run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.failures), 1, "canary 没红：断言机制不可信，整卡不得验收")

    def test_02_ast_anchor(self):
        with open(os.path.abspath(__file__), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        methods = [n.name for c in ast.walk(tree) if isinstance(c, ast.ClassDef)
                   and c.name.startswith("Test") for n in c.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name.startswith("test_")]
        asyncish = [n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                    and n.name.startswith("test_")]
        self.assertEqual(asyncish, [], f"async test_ 会静默判 PASS：{asyncish}")
        self.assertEqual(len(methods), 4, "锚点漂移：顶层 Test* 类体内应为 4 格（含嵌套 canary 则 5）")

    def test_10_excinfo_authorization_masked_in_final_render(self):
        v = _sentinel(12)
        out = _render_exception("boom", v)
        self.assertIn("Traceback (most recent call last)", out,
                      "探针没吃到渲染后的 traceback——本格失去意义，先修探针")
        self.assertIn("ValueError:", out, "异常行丢了")
        self.assertNotIn(v, out, f"exc_info 里的凭据值泄漏（A-3 成立）：{out}")
        self.assertIn("Authorization=***", out,
                      f"须保形：键名+`=`在位、只有值被遮：{out}")


if __name__ == "__main__":
    unittest.main()
