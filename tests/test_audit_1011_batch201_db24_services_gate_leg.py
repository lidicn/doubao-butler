"""批201 DB24 门：`compose_up` / `compose_down` 的 `services` 参数注入（第三期审计报告 第一轮 · medium）。

病灶（现读 `butler/integrations/docker_tools.py:147-148` 与 `:166-167`）＝
同文件里 `project_dir`、`container_name` 两枚入口都装了白名单，`services` 是**第 N+1 个入口**，
原样 `args.extend(services)` 拼进 argv ⇒ `["--profile","evil"]` 变成 flag，
`["-f","/tmp/evil.yml"]` 更狠：docker compose 的多 `-f` 是**合并语义**，等于绕过 project_dir 前缀校验。

修法＝按自家约定补齐（⛔ 自造新政策）：service 名必须 `^[A-Za-z0-9][A-Za-z0-9._-]*$`，
禁以 `-` 开头（那正是 flag 的形状），两腿同闸。
⛔ 业务白名单：服务名随 compose 文件变化，穷举会误伤——「不该被拦的必须通」是反向用例（test_01）。

⚠ 本树跑在 Windows 上时 `Path("/vol1/…").resolve()` 会把 POSIX 路径改写成盘符路径，
⇒ 本门用测试内补丁把 `_validate_project_dir` 换成同判据的桩，⛔ 顺带验它（那是另一枚主题）。
"""
import ast
import asyncio
import pathlib
import unittest

from butler.integrations import docker_tools
from butler.integrations.docker_tools import DockerClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROD = ROOT / "butler" / "integrations" / "docker_tools.py"
PROJECT_DIR = "/vol1/1000/docker/doubao-butler"


def ungated_extend_functions(src: str) -> list[str]:
    """返回「把 services 裸拼进 argv，但同一函数内⛔ 调 _validate_services」的函数名。

    按 AST 归属判，⛔ 全文件计数——定义行 `def _validate_services(services)` 里就带
    `_validate_services(services)` 这串，拿字符串数会把它当一道闸（本批真踩过）。
    """
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        extends = [c for c in ast.walk(node)
                   if isinstance(c, ast.Call)
                   and isinstance(c.func, ast.Attribute) and c.func.attr == "extend"
                   and c.args and getattr(c.args[0], "id", "") == "services"]
        gated = [c for c in ast.walk(node)
                 if isinstance(c, ast.Call)
                 and getattr(c.func, "id", "") == "_validate_services"]
        if extends and not gated:
            bad.append(node.name)
    return bad


class _Capturing(DockerClient):
    """把 `_run` 换成 argv 记录仪：被拒的调用必须一条命令都不发出去。"""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def _run(self, args, timeout=None):
        self.calls.append(list(args))
        return 0, "ok", ""


def _stub_project_dir(value):
    original = docker_tools._validate_project_dir
    docker_tools._validate_project_dir = lambda p: p if p == value else None
    return original


class TestBatch201Db24ServicesGate(unittest.TestCase):

    def setUp(self):
        self._orig = _stub_project_dir(PROJECT_DIR)
        self.addCleanup(setattr, docker_tools, "_validate_project_dir", self._orig)
        self.client = _Capturing()

    def _run(self, coro):
        return asyncio.run(coro)

    # ── 反向：合法 service 必须通 ────────────────────────
    def test_01_legit_services_still_forward(self):
        r = self._run(self.client.compose_up(PROJECT_DIR, services=["butler", "mqtt"]))
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.client.calls[0][-2:], ["butler", "mqtt"],
                         "合法服务名被拦＝闸门拦太宽")

    # ── 正向：三种 flag 形状全部拒，且⛔ 发命令 ──────────
    def test_02_profile_flag_rejected_without_running(self):
        r = self._run(self.client.compose_up(PROJECT_DIR, services=["--profile", "evil"]))
        self.assertFalse(r["ok"])
        self.assertEqual(self.client.calls, [], "被拒的调用仍然发了命令")

    def test_03_second_dash_f_rejected(self):
        r = self._run(self.client.compose_up(PROJECT_DIR, services=["-f", "/tmp/evil.yml"]))
        self.assertFalse(r["ok"])
        self.assertEqual(self.client.calls, [])

    def test_04_compose_down_shares_the_same_gate(self):
        r = self._run(self.client.compose_down(PROJECT_DIR, services=["--help"]))
        self.assertFalse(r["ok"])
        self.assertEqual(self.client.calls, [],
                         "compose_down 同文件同缺陷，两腿必须同闸⛔ 只装一条")

    def test_05_non_string_and_empty_entries_rejected(self):
        for bad in (["butler", 123], [""], [None], ["-"], ["butler; rm -rf /"]):
            with self.subTest(bad=bad):
                r = self._run(self.client.compose_up(PROJECT_DIR, services=bad))
                self.assertFalse(r["ok"], bad)
        self.assertEqual(self.client.calls, [])

    # ── 判据本体的双侧校准 ──────────────────────────────
    def test_06_validator_accepts_and_rejects(self):
        self.assertTrue(hasattr(docker_tools, "_validate_services"),
                        "判据函数不存在⇒这一格没东西可校")
        v = docker_tools._validate_services
        self.assertIsNone(v(["butler", "new-api", "mqtt"]))
        self.assertIsNone(v(None))
        self.assertIsNone(v([]))
        for bad in (["--profile", "evil"], ["-f", "x.yml"], ["-"], [""], [None], [123]):
            self.assertIsNotNone(v(bad), bad)

    # ── 形状尺：⛔ 裸 extend 复活（按 AST 归属，⛔ 字符串计数）──
    def test_07_no_unvalidated_extend_remains(self):
        src = PROD.read_text(encoding="utf-8")
        self.assertEqual(ungated_extend_functions(src), [],
                         "有函数把 services 裸拼进 argv 却没过 _validate_services")

    def test_08_shape_ruler_bites_synthetic(self):
        """尺子自检：合成「裸拼但没挂闸」必须判成违规，否则 test_07 是摆设。"""
        synth = (
            "class C:\n"
            "    async def compose_up(self, services):\n"
            "        args = []\n"
            "        if services:\n"
            "            args.extend(services)\n"
            "        return args\n"
            "    async def compose_down(self, services):\n"
            "        args = []\n"
            "        reason = _validate_services(services)\n"
            "        if services:\n"
            "            args.extend(services)\n"
            "        return args\n")
        self.assertEqual(ungated_extend_functions(synth), ["compose_up"])
        self.assertEqual(ungated_extend_functions(
            "async def ok(self, services):\n"
            "    reason = _validate_services(services)\n"
            "    args = []\n"
            "    args.extend(services)\n"), [])


if __name__ == "__main__":
    unittest.main()
