"""批187（P1-「从未跑过的测试」一条腿）：三档 pytest 形制的测试模块改挂 unittest，并钉住它们真跑。

现读定性（`workorders/readings/1011/b187_pytest_ground_1011.txt`，22:14:36Z 逐格读数）：
`tests/test_api_core.py`／`tests/test_agent_skill.py`／`tests/test_nas_ops_tools.py` 三档在全量回归里
一直是 `rc=5 ran=0`——不是它们红，而是 **unittest 一格也收不到**：类名以 `Test` 开头却⛔ 继承
`unittest.TestCase`，方法体里全是裸 `assert`，`TestNewApiClient` 还吃 pytest 的 `tmp_path` fixture，
`TestDispatch` 的 7 格挂 `@pytest.mark.asyncio`。所以这三档 50 格从本园子有回归尺以来**一次都没跑过**。

借 pytest（本机 9.1.1 在场，⛔ 不进回归运行器）取了一次地面真值：`3 failed, 47 passed`。
三条红都在 `TestDispatch` 的 docker 腿上，失败消息 `object dict can't be used in 'await' expression`
（`butler/tools/desk_pilot.py:239-249` 是 `await dk.ps/restart/logs/compose_up`），
点开生产侧真身 `butler/integrations/docker_tools.py:77 :98 :113 :131`——四枚全是 `async def`：
**红的是桩件（`FakeDocker` 写成了同步），⛔ 生产码**。本节把 `FakeDocker` 的四条腿改 `async def`，
并加 ⑥⑦ 两格把「真身是协程」钉住：再有人把桩件写成同步，本门当场红，⛔ 让「假桩件」伪装成「产品坏」。

形制约束（沿用批182 那次同族收编的口径）：格数 1:1 保留（api_core 4／agent_skill 28／nas_ops 18，
合计 50＝pytest 地面真值的 `47+3`），⛔ 借改形制之机删格或加格；判据格全为 `unittest.TestCase`
**同步**方法（协程进 TestCase＝收到但从不 await，静默判 PASS），async 腿一律 `asyncio.run(...)` 包。
pytest 地面真值里那 47 格绿在本节转换后必须仍为绿；3 格红改桩件后转绿——这是本节口径覆盖 22:14:36Z
那份读数的地方，原读数⛔ 再被当成「当前状态」引用。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import os
import re
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

TARGETS = {                      # 模块名 → 转换后必须被收集到的格数（＝pytest 地面真值逐格点开的数）
    "tests.test_api_core": 4,
    "tests.test_agent_skill": 28,
    "tests.test_nas_ops_tools": 18,
}
DOCKER_METHODS = ("ps", "restart", "logs", "compose_up")


def _tree(rel: str) -> ast.Module:
    return ast.parse(_text(rel))


def _text(rel: str) -> str:
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _docstring_spans(tree: ast.AST) -> set:
    spans = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            spans.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return spans


def _code_only(src: str, tree: ast.AST) -> str:
    """剥掉所有 docstring 之后的纯代码文本。

    本节口径覆盖 22:20:08Z 那次红读数的地方：形状尺原本扫整份源码，被我自己写在 docstring 里
    的 `@pytest.mark.asyncio` 字样（形制说明，⛔ 装饰器）判成『还有 pytest 形状』＝散文自命中。
    ⛔ 为此把说明改掉（那等于把码/文拗成尺子的形状）⇒ 改尺子：形状判据只看代码体。
    尺子仍会咬真装饰器，两侧校准在 test_02。
    """
    lines = src.split("\n")
    spans = _docstring_spans(tree)
    return "\n".join("" if i + 1 in spans else lines[i] for i in range(len(lines)))


def _shape(rel: str) -> dict:
    """源码形状：谁继承 TestCase、谁还挂着 pytest 的形状、有没有协程判据格。"""
    tree = _tree(rel)
    src = _text(rel)
    classes, module_defs, async_defs = [], [], []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases]
            cases = [n.name for n in node.body
                     if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
            bad = [n.name for n in node.body
                   if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("test_")]
            classes.append((node.name, bases, cases, bad))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                module_defs.append(node.name)
                if isinstance(node, ast.AsyncFunctionDef):
                    async_defs.append(node.name)
    return {"classes": classes, "module_test_defs": module_defs,
            "async_module_defs": async_defs, "src": src, "code": _code_only(src, tree)}


def _pytest_shape_hits(code: str) -> list:
    """形状判据本体：test_02（合成件校准）与 test_03（三档现读）共用这一条，⛔ 两腿各写一遍。"""
    hits = []
    if re.search(r"^\s*(?:import pytest|from pytest)\b", code, re.M):
        hits.append("还 import pytest")
    if "@pytest." in code:
        hits.append("还有 @pytest.mark／fixture")
    return hits


_SYNTH_DOCSTRING_PROSE = (
    '"""这一档原来是 pytest 形制，挂着 @pytest.mark.asyncio 装饰器——只是说明，代码体里并没有。"""\n'
    "import unittest\n\n\n"
    "class TestShape(unittest.TestCase):\n"
    "    def test_a(self):\n        self.assertEqual(1, 1)\n"
)


_SYNTH_PYTEST_ONLY = (
    "import pytest\n\n\n"
    "class TestShape:\n"
    "    def test_a(self):\n        assert 1 == 1\n\n"
    "    @pytest.mark.asyncio\n"
    "    async def test_b(self):\n        assert 2 == 2\n"
)
_SYNTH_UNITTEST = (
    "import unittest\n\n\n"
    "class TestShape(unittest.TestCase):\n"
    "    def test_a(self):\n        self.assertEqual(1, 1)\n"
)


class TestBatch187PytestShape(unittest.TestCase):
    # ---------- 自证与校准 ----------

    def test_00_canary_assertion_mechanism_bites(self):
        class _Canary(unittest.TestCase):
            def test_deliberately_wrong(self):
                self.assertEqual(2, 3)

        res = unittest.TestResult()
        unittest.TestLoader().loadTestsFromTestCase(_Canary).run(res)
        self.assertEqual(len(res.failures), 1, "canary 没红：本文件断言机制不可信，整卡不得验收")
        self.assertEqual(res.testsRun, 1)

    def test_01_ast_anchor_ten_sync_cases(self):
        s = _shape(os.path.join("tests", os.path.basename(__file__)))
        sync_cases = [n for c in s["classes"] for n in c[2]]
        async_cases = [n for c in s["classes"] for n in c[3]]
        self.assertEqual(len(sync_cases), 10,
                         f"锚点漂移：本门格数应现读为 10，现为 {len(sync_cases)}（增删格必须同步改判据与台账）")
        self.assertEqual(async_cases, [], f"本门⛔ 协程判据格（会静默判 PASS）：{async_cases}")
        self.assertEqual(s["module_test_defs"], [], "本门⛔ 模块级 test_*（discover 会静默收下）")

    def test_02_shape_ruler_is_calibrated_both_ways(self):
        """形状尺两侧校准：合成 pytest 形制必须被认成『没挂上 TestCase』，合成 unittest 形制必须放行。"""
        bad = ast.parse(_SYNTH_PYTEST_ONLY)
        good = ast.parse(_SYNTH_UNITTEST)

        def unmounted(tree):
            out = []
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    if "unittest.TestCase" not in [ast.unparse(b) for b in node.bases]:
                        out.append(node.name)
            return out

        self.assertEqual(unmounted(bad), ["TestShape"], "尺子放行了 pytest 形制的合成件⇒ 假绿")
        self.assertEqual(unmounted(good), [], "尺子咬了 unittest 形制的合成件⇒ 假红")
        self.assertEqual([f.name for n in bad.body if isinstance(n, ast.ClassDef)
                          for f in n.body if isinstance(f, ast.AsyncFunctionDef)], ["test_b"],
                         "尺子看不见协程判据格")

        # 剥 docstring 这条腿的两侧校准：真装饰器必须咬住，散文里提字样必须放行
        prose_tree = ast.parse(_SYNTH_DOCSTRING_PROSE)
        self.assertEqual(_pytest_shape_hits(_code_only(_SYNTH_DOCSTRING_PROSE, prose_tree)), [],
                         "尺子咬了『只在 docstring 里提到 @pytest.mark』的合成件⇒ 假红")
        self.assertIn("还 import pytest", _pytest_shape_hits(_SYNTH_PYTEST_ONLY),
                      "尺子放行了真 import pytest 的合成件⇒ 假绿")
        self.assertIn("还有 @pytest.mark／fixture", _pytest_shape_hits(_SYNTH_PYTEST_ONLY),
                      "尺子放行了真 @pytest.mark 装饰器的合成件⇒ 假绿")

    # ---------- 三档的形状腿 ----------

    def test_03_no_target_module_keeps_pytest_shape(self):
        problems = []
        for rel in ("tests/test_api_core.py", "tests/test_agent_skill.py", "tests/test_nas_ops_tools.py"):
            s = _shape(rel)
            for hit in _pytest_shape_hits(s["code"]):
                problems.append(f"{rel}:{hit}")
            for cls, bases, _cases, async_cases in s["classes"]:
                if cls.startswith("Test") and "unittest.TestCase" not in bases:
                    problems.append(f"{rel}:{cls} 没继承 unittest.TestCase")
                if async_cases:
                    problems.append(f"{rel}:{cls} 有协程判据格 {async_cases}")
            if s["module_test_defs"]:
                problems.append(f"{rel}:还有模块级 test_* {s['module_test_defs']}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_04_cell_counts_are_pinned_and_reconcile_with_ground_truth(self):
        total = 0
        rows = {}
        for rel, want in zip(("tests/test_api_core.py", "tests/test_agent_skill.py",
                              "tests/test_nas_ops_tools.py"), (4, 28, 18)):
            got = sum(len(c[2]) for c in _shape(rel)["classes"])
            rows[rel] = got
            self.assertEqual(got, want, f"{rel} 格数 {got}！= 钉住的 {want}（⛔ 借改形制删格或加格）")
            total += got
        self.assertEqual(total, 50, f"三档合计 {total}，pytest 地面真值是 47+3=50")

    def test_05_every_target_cell_is_collected_by_unittest(self):
        loader = unittest.TestLoader()
        for mod, want in TARGETS.items():
            m = __import__(mod, fromlist=["*"])
            got = loader.loadTestsFromModule(m).countTestCases()
            self.assertEqual(got, want, f"{mod} 被 unittest 收到 {got} 格，应为 {want}（rc=5 的病根）")

    # ---------- 桩件与真身 ----------

    def test_06_docker_client_real_methods_are_coroutines(self):
        from butler.integrations.docker_tools import DockerClient
        not_async = [n for n in DOCKER_METHODS
                     if not inspect.iscoroutinefunction(getattr(DockerClient, n, None))]
        self.assertEqual(not_async, [],
                         f"真身 {not_async} 不是协程⇒ 要么生产码改了形制、要么 `_dispatch_docker` 的 await 是错的")

    def test_07_fake_docker_matches_the_real_shape(self):
        from tests.test_nas_ops_tools import FakeDocker
        not_async = [n for n in DOCKER_METHODS
                     if not inspect.iscoroutinefunction(getattr(FakeDocker, n, None))]
        self.assertEqual(not_async, [],
                         f"桩件 {not_async} 是同步的⇒ dispatch 里 `await dk.X()` 会抛 "
                         f"`object dict can't be used in 'await' expression`，那三条 docker 腿会假装成产品缺陷")

    def test_08_docker_dispatch_cells_pass_on_the_disk_face(self):
        from butler.tools.desk_pilot import _dispatch_docker
        from tests.test_nas_ops_tools import FakeAgent, FakeDocker
        agent = FakeAgent(docker=FakeDocker())
        for tool, args in (("docker_ps", {}), ("docker_restart", {"container": "new-api"}),
                           ("docker_compose_up", {"project_dir": "/vol1/1000/docker/x"})):
            payload = asyncio.run(_dispatch_docker(agent, tool, args))
            obj = __import__("json").loads(payload)
            self.assertTrue(obj.get("ok"), f"{tool} 走不通：{obj}")
            if tool == "docker_restart":
                self.assertEqual(obj["result"]["container"], "new-api")

    # ---------- 端到端：真跑一遍运行器 ----------

    def test_09_runner_gets_zero_failures_and_nonzero_counts(self):
        rows = []
        for mod, want in TARGETS.items():
            p = subprocess.run([sys.executable, "-m", "unittest", mod], cwd=REPO_ROOT,
                               capture_output=True, text=True)
            ran = re.search(r"^Ran (\d+) test", p.stderr, re.M)
            rows.append((mod, p.returncode, int(ran.group(1)) if ran else 0))
        bad = [r for r in rows if r[1] != 0 or r[2] == 0]
        self.assertEqual(bad, [], f"运行器读数不符（rc=5 即『一格没跑』的病根）：{rows}")
        self.assertEqual([r[2] for r in rows], [4, 28, 18], f"逐档格数漂移：{rows}")


if __name__ == "__main__":
    unittest.main()
