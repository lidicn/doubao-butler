"""WO-ME-224-R10 配套测试：JSON / 冒号保形 / 带引号键名 三形脱敏 + 已遮形状不回归。

被测树由环境变量 `BUTLER_LOGGING_SETUP` 选择（口径写进格子，不靠猜）：
  未设   -> 盘上现座 butler/logging_setup.py（R10 未装机，4 个 R10-target 格【应当红】，
            红即现网漏口台账，不是测试坏了）
  已设   -> importlib 按路径加载候选副本（候选树验收：应 13 pass + 1 skip）

分母锚点（声明）：total = 对本文件 AST 数 FunctionDef/AsyncFunctionDef 且 name.startswith('test_')；
collectable = pytest 'collected N items'；ran = unittest 'Ran N'。全部同步用例（已知
harness 缺陷：协程进 TestCase 会『收到但从不 await』⇒ 静默判 PASS，见 test_01/test_02 双探针）。

不改上午的 tests/test_log_masking_shapes_short.py（它钉的是盘上约定：`token=***`；
R10 装机后其 test_01 的冒号断言会翻红——那是预期中的对账点，见回执）。
判据对账（PM 裁定·第4轮·3 → 14:31Z 改写「裁 A ＋并」）：先前那句"作废 DEV1 shapes 门 `test_shape_09`"是**坏判据**（作废它并不能让本门转绿；互斥两侧里红的是本文件 `test_25`），已撑销。现行口径＝`test_25` 翻面为"Bearer 后真值必须遮掉且保形"，与 `test_shape_09` 同向；两格在 R11a 下同绿、在未装机基底下同红。
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
STARS = "***"


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


def _sentinel(length: int) -> str:
    v = ("SENT" + uuid.uuid4().hex)[:length]
    assert len(v) == length
    return v


def _capture(msg: str, *args) -> str:
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(MODULE.MaskingFilter())
    # A-1（PM 裁定·第4轮·3）：此门原先只挂 Filter、不挂 MaskingFormatter ⇒ Formatter 侧
    # 脱敏在本门里物理不可见（假门）。handler 必须挂被测树的 MaskingFormatter；
    # candidate 树没有它 = 门测不到被测对象，直接红，不许静默退回裸 Formatter。
    formatter = MODULE.MaskingFormatter if TREE == "candidate" else getattr(MODULE, "MaskingFormatter", None)
    h.setFormatter((formatter or logging.Formatter)("%(message)s"))
    lg = logging.getLogger("r10_" + uuid.uuid4().hex[:8])
    lg.handlers = [h]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    lg.info(msg, *args)
    return buf.getvalue()


class TestMaskingJSONShapes(unittest.TestCase):

    # ---------- 树自证与量具自检 ----------

    def test_00_provenance_eats_the_right_tree(self):
        """先证明探针吃到的就是声称的那棵树，且区分盘上 / 仓内 tmp 候选副本。"""
        self.assertEqual(os.path.basename(MODULE_FILE), "logging_setup.py")
        self.assertTrue(os.path.realpath(MODULE_FILE).startswith(REPO_ROOT + os.sep),
                        f"被测文件不在仓内（宿主/容器树串了？）：{MODULE_FILE}")
        self.assertTrue(hasattr(MODULE, "MaskingFilter"))
        under_tmp = os.path.realpath(MODULE_FILE).startswith(TMP_ROOT + os.sep)
        if TREE == "deployed-disk":
            self.assertEqual(os.path.realpath(MODULE_FILE), DISK_MODULE,
                             f"盘上模式却加载了 {MODULE_FILE}")
            self.assertFalse(under_tmp, "盘上模式禁止吃 tmp 副本")
        elif TREE == "candidate":
            self.assertTrue(under_tmp,
                            f"candidate 模式必须显式指向仓内 tmp 候选副本，实到 {MODULE_FILE}")
            self.assertEqual(os.environ.get(ENV_VAR) and os.path.realpath(os.environ[ENV_VAR]),
                             os.path.realpath(MODULE_FILE), "env 路径与实际加载不一致")
        else:  # pragma: no cover
            self.fail(f"未知树标记 {TREE}")

    def test_01_selfcheck_negative_probe(self):
        """故意错的断言必须 FAIL——证这套断言机制不会静默吞红（async 盲区反证同款）。"""
        class _Canary(unittest.TestCase):
            def test_deliberately_wrong(self):
                self.assertEqual(2, 3)

        result = unittest.TestResult()
        unittest.TestLoader().loadTestsFromTestCase(_Canary).run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.failures), 1,
                         "canary 没有变红：本文件的断言机制不可信，整张卡不得验收")
        self.assertEqual(len(result.errors), 0)

    def test_02_ast_anchor_no_async_test_defs(self):
        """分母锚点自检：Test* 类体内 test_* 方法全为同步（锚点=类体成员，不含嵌套 canary）。"""
        with open(os.path.abspath(__file__), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        methods = [
            n.name
            for c in ast.walk(tree)
            if isinstance(c, ast.ClassDef) and c.name.startswith("Test")
            for n in c.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")
        ]
        asyncish = [
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("test_")
        ]
        self.assertEqual(asyncish, [], f"async test_ 会静默判 PASS：{asyncish}")
        self.assertEqual(len(methods), 14,
                         "锚点数漂移：新增/删格必须同步改本判据与回执三个数（total 同锚点）")

    # ---------- R10-target：现网漏口（盘上红 = 台账；候选绿 = 修复达成） ----------

    def test_10_json_space_masked(self):
        v = _sentinel(8)
        out = _capture('{"api_key": "%s", "room": "living"}' % v)
        self.assertNotIn(v, out, f"JSON 带空格形状泄漏：{out}")
        self.assertIn('"api_key": "***"', out, f"JSON 形状被改写：{out}")
        self.assertIn('"room": "living"', out, f"误伤非敏感字段：{out}")
        self.assertEqual(out.count("*"), 3)

    def test_11_json_nospace_masked(self):
        v = _sentinel(12)
        out = _capture('{"new_api_key":"%s"}' % v)
        self.assertNotIn(v, out, f"JSON 紧凑形状泄漏：{out}")
        self.assertIn('"new_api_key":"***"', out, f"紧凑 JSON 形状被改写：{out}")
        self.assertEqual(out.count("*"), 3)

    def test_12_quoted_key_colon_masked(self):
        v = _sentinel(12)
        out = _capture("'ha_token': '%s'" % v)
        self.assertNotIn(v, out, f"带引号键名形状泄漏：{out}")
        self.assertIn("'ha_token': '***'", out, f"引号配对被破坏：{out}")
        self.assertEqual(out.count("*"), 3)

    def test_13_colon_preserves_separator(self):
        v = _sentinel(12)
        out = _capture("token: %s" % v)
        self.assertNotIn(v, out, f"冒号形状泄漏：{out}")
        self.assertIn("token: ***", out,
                      f"R10 要求保留 `:` 分隔符（盘上现座归一为 `=`——装机后此格由红转绿）：{out}")
        self.assertEqual(out.count("*"), 3)

    # ---------- 稳定格：装机前后输出必须逐字节不变（防修好一片挨坏一片） ----------

    def test_20_plain_kv_stable(self):
        v = _sentinel(12)
        out = _capture(f"api_key={v}")
        self.assertNotIn(v, out)
        self.assertIn("api_key=***", out)
        self.assertEqual(out.count("*"), 3)

    def test_21_dict_positional_stable(self):
        v = _sentinel(12)
        out = _capture("cfg %s", {"api_key": v})
        self.assertNotIn(v, out, f"dict 位置参数泄漏：{out}")
        self.assertIn("***", out)
        self.assertEqual(out.count("*"), 3)

    def test_22_kwargs_mapping_stable(self):
        v = _sentinel(12)
        out = _capture("x=%(api_key)s", {"api_key": v})
        self.assertNotIn(v, out, f"kwargs 映射泄漏：{out}")
        self.assertIn("x=***", out)

    def test_23_placeholders_survive(self):
        """WO-DB-102 不变量：后随 %s 占位符一个不能被遮/吃。"""
        v = _sentinel(12)
        out = _capture(f"emit token={v} user=%s ok=%s", "alice", True)
        self.assertNotIn(v, out, f"内联值泄漏：{out}")
        self.assertIn("token=***", out)
        self.assertIn("user=alice", out, f"占位符被吃：{out}")
        self.assertIn("ok=True", out, f"后续占位符被吃：{out}")

    def test_24_benign_line_untouched(self):
        out = _capture("普通中文日志 %s", "ok")
        self.assertIn("普通中文日志 ok", out)
        self.assertNotIn("*", out, "误遮：良性中文行不该出现任何星号")

    def test_25_bearer_value_must_be_masked(self):
        """改判 A（PM 裁定·第4轮·3 改写，14:31Z「并」）：本格原钉"Bearer 后真值仍在位"，
        与 DEV1 shapes 门 `test_shape_09` 互斥 ⇒ 作废 `test_shape_09` 那条判据已撑销，
        台账翻面成断言：`Authorization: Bearer <值>` 必须遮掉值，且保形（`Bearer` 一词原样 + `***`）。
        反证口径：同一把门喂未装机基底 ⇒ 本格【应当红】（读数见 Q-t5 第5轮卡 §2）。"""
        v = _sentinel(12)
        out = _capture("Authorization: Bearer " + v)
        self.assertNotIn(v, out, "Bearer 之后的真值必须被遮掉")
        self.assertIn("Authorization: Bearer ***", out, "掩码须保形：只遮值，`Bearer` 一词与分隔符原样")

    # ---------- 常红记账格：不得并入通过 ----------

    def test_30_bare_value_skip(self):
        """cell 6（续）：裸值无键名锚点，按设计漏——显式 skip，禁止计入『通过』。"""
        v = _sentinel(12)
        out = _capture("bearer %s", v)
        if v not in out:
            self.fail("裸值已被遮蔽：现座/候选行为已变，skip 台账过期，改断言并更新回执。")
        self.skipTest("按设计漏：MaskingFilter 只锚定键名，裸值无锚点（WO-ME-224 cell 6 常红项）。")


if __name__ == "__main__":
    unittest.main()
