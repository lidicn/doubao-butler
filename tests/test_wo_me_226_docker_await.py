"""WO-ME-226 回归卡：Docker 运维工具链必须 await 到底（Q-t6，2026-09-21）

被修缺陷：`butler/core/tools.py::_dispatch_docker` 是同步 def，却直接调用
`DockerClient.ps/restart/logs/compose_up` 这 4 个 `async def` ⇒ 拿到的是协程对象，
`json.dumps` 抛 TypeError ⇒ 工具永远回 `internal_error / Object of type coroutine is not JSON serializable`，
且 `subprocess.run` 一次都没被调用（docker CLI 从未被 spawn）。

本卡两类格子：
  · 契约格（同步）：形状判据 —— 方法仍是协程、`_dispatch_docker` 必须是协程函数、
    源码级「4 个调用点全部 await + 唯一调用点 await」（防以后被人把 await 摘掉）。
  · 运行格（async def）：真跑一遍工具链，断言拿到了 docker 自己的语义而不是协程序列化错。

⚠ 本卡故意带一格自我否定探针 `test_zz_self_negative_probe`（断 `2 == 3`）：**它必须 FAIL**。
  若它在某次运行里 PASS ⇒ 说明这张卡的 async 格根本没被 await（仓里已知的静默判绿通道），
  整卡验收作废。`QT6_SELFNEGATIVE=0` 只把**探针那一格**转成 skip（跳过单列，不并入通过数），
  用来取「其余格全绿」的第二组读数。

⛔ 不要用 `python tests/test_wo_me_226_docker_await.py` 直跑：`unittest.main` 只收 `TestCase`
  子类，本卡的运行格是 pytest 风格类 ⇒ 会被**静默不收**（5 格绿、7 格蒸发）。请走：
    · pytest：`python -m pytest tests/test_wo_me_226_docker_await.py -o addopts=""`
    · 仓内权威 harness：`python workorders/tools/stdlib_test_runner.py tests.test_wo_me_226_docker_await`
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import unittest
import warnings

from butler.core import tools as T
from butler.integrations import docker_tools as DT

DK_METHODS = ("ps", "restart", "logs", "compose_up")
COROUTINE_MSG = "Object of type coroutine is not JSON serializable"
FAKE_PS_OUT = "a1b2c3d4e5f6|butler|Up 3 days|butler:latest|8080/tcp\nf6e5d4c3b2a1|new-api|Up 2 hours|newapi:latest|\n"


def _fake_proc(rc, out, err):
    class P:
        returncode, stdout, stderr = rc, out, err
    return P


class _Agent:
    """只需要 .docker 这一个挂载点，形状同 butler/app.py:273 的注入。"""

    def __init__(self):
        self.docker = DT.DockerClient("/var/run/docker.sock")


class TestDockerAwaitContract(unittest.TestCase):
    """契约格（同步）。这些格子在修前的树上必须红 —— 它们编码的是「修完以后」的形状。"""

    def test_docker_client_methods_are_coroutine_functions(self):
        for m in DK_METHODS:
            self.assertTrue(inspect.iscoroutinefunction(getattr(DT.DockerClient, m)),
                            f"DockerClient.{m} 不再是协程 ⇒ 本卡的 await 判据失去对象，需重写")

    def test_dispatch_docker_is_coroutine_function(self):
        self.assertTrue(inspect.iscoroutinefunction(T._dispatch_docker),
                        "_dispatch_docker 仍是同步 def ⇒ WO-ME-226 未修，四个 dk.* 调用点必然漏 await")

    def test_every_docker_call_site_is_awaited(self):
        src = inspect.getsource(T._dispatch_docker)
        bare = [ln.strip() for ln in src.splitlines()
                if "dk." in ln and "await" not in ln and not ln.strip().startswith(("#", '"""'))]
        self.assertEqual([], bare, f"存在未 await 的调用点：{bare}")
        awaited = [m for m in DK_METHODS if f"await dk.{m}(" in src]
        self.assertEqual(sorted(DK_METHODS), sorted(awaited),
                         f"4 个可达方法要逐个见到 await，实得 {awaited}")

    def test_single_caller_awaits_dispatch(self):
        src = inspect.getsource(T.dispatch_tool)
        self.assertIn("return await _dispatch_docker(", src,
                      "调用点没 await ⇒ 把同一个 bug 往上搬一层（WO-ME-226 §4 V3）")

    def test_compose_down_is_still_dead_code(self):
        """登记用：`compose_down` 既不在 TOOL_SCHEMAS 也不在 dispatch 分支 ⇒ 现网不可达。
        若哪天有人把它接上，本格变红，逼他重新过一次 await 判据。"""
        names = {s["function"]["name"] for s in T.TOOL_SCHEMAS if s.get("function", {}).get("name", "").startswith("docker_")}
        self.assertNotIn("docker_compose_down", names)
        self.assertNotIn("docker_compose_down", inspect.getsource(T._dispatch_docker))


class TestDockerDispatchRuns:
    """运行格：**pytest 风格类**（不继承 unittest.TestCase）。

    为什么不放进 TestCase：本仓已知通道 —— `async def test_*` 写进 `unittest.TestCase`
    会被"收到但从不 await"⇒ 静默判 PASS。放进普通 `Test*` 类，协程才真的被 await
    （pytest-asyncio auto 模式 / workorders/tools/stdlib_test_runner.py 的 _AsyncAdapter）。
    修前：每格拿到协程序列化错或直接 TypeError；修后：拿到 docker 自己的语义。
    """

    def setup_method(self):
        self.agent = _Agent()
        self.spawned = []
        self._real_run = DT.subprocess.run

    def teardown_method(self):
        DT.subprocess.run = self._real_run

    def _stub(self, rc=0, out=FAKE_PS_OUT, err=""):
        def _f(cmd, *a, **k):
            self.spawned.append(list(cmd))
            return _fake_proc(rc, out, err)
        DT.subprocess.run = _f

    async def test_docker_ps_reaches_cli_and_serializes(self):
        self._stub(rc=0)
        r = await T._dispatch_docker(self.agent, "docker_ps", {"all": True})
        d = json.loads(r)
        assert d["ok"], d
        assert d["tool"] == "docker_ps"
        assert d["result"]["count"] == 2
        assert [c["name"] for c in d["result"]["containers"]] == ["butler", "new-api"]
        assert len(self.spawned) == 1, "docker CLI 必须被真正 spawn"
        # docker_tools.py::_run_sync 拼的是 ["docker"] + args
        assert self.spawned[0][:4] == ["docker", "ps", "-a", "--format"], self.spawned[0]

    async def test_docker_logs_surfaces_daemon_error(self):
        self._stub(rc=1, out="", err="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.")
        d = json.loads(await T._dispatch_docker(self.agent, "docker_logs", {"container": "butler", "tail": 5}))
        assert d["ok"] is False
        assert "Cannot connect to the Docker daemon" in d["message"]
        assert COROUTINE_MSG not in json.dumps(d, ensure_ascii=False)

    async def test_no_never_awaited_warning(self):
        self._stub(rc=0)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await T._dispatch_docker(self.agent, "docker_ps", {})
        bad = [str(x.message) for x in w if "never awaited" in str(x.message)]
        assert bad == [], f"仍有协程被丢弃：{bad}"

    async def test_entry_point_dispatch_tool_docker_ps(self):
        self._stub(rc=0)
        d = json.loads(await T.dispatch_tool("docker_ps", {}, self.agent))
        assert d["ok"], d
        assert d["result"]["count"] == 2

    async def test_unknown_docker_tool_stays_synchronous_shape(self):
        d = json.loads(await T._dispatch_docker(self.agent, "docker_compose_down", {}))
        assert d["ok"] is False
        assert d["error"] == "invalid_parameter"
        assert len(self.spawned) == 0, "未知工具不该 spawn 任何命令"

    async def test_missing_client_still_reports_not_implemented(self):
        class NoDocker:
            docker = None
        d = json.loads(await T._dispatch_docker(NoDocker(), "docker_ps", {}))
        assert d["error"] == "not_implemented"

    # ── 自我否定探针（本格必须 FAIL；它 PASS ⇒ 整卡作废）──────────────
    # 静态写在类体里、不动态挂载：动态 attach 会让 stdlib_test_runner 的
    # `ast_total == suite_size + lost` 对账崩掉（ast 数不到运行期注入的那格），
    # 于是 rc=1 的原因是"卡片形状"而不是"被测代码"，这把尺子就废了。
    async def test_zz_self_negative_probe(self):
        """WO-ME-226 §4 V2：故意错的断。它 PASS ⇒ async 格没被 await（静默判绿通道）。"""
        if os.environ.get("QT6_SELFNEGATIVE", "1") == "0":
            raise unittest.SkipTest("QT6_SELFNEGATIVE=0：本格按口径跳过（取「其余格全绿」的第二组读数）")
        assert 2 == 3, "PROBE: 这条必须 FAIL；PASS 即说明 harness 从不 await 协程"


if __name__ == "__main__":
    raise SystemExit(
        "本卡禁止 unittest.main 直跑（运行格是 pytest 风格类，会被静默不收）。"
        "改走: pytest -o addopts=\"\"  或  workorders/tools/stdlib_test_runner.py")
