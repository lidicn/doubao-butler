"""批139（§八#4，DCD 20261006）：调 MA/AF 失败统一带 ADM_ERR_* 码，不假绿。

契约 v2.0 §7.2/§B 落点：客户端调对端失败的结果形状 = {ok:false, code, message}，
日志/异常文本带码。本批覆盖两枚码的接线（计划点名项）：
  - ADM_ERR_UPSTREAM_TIMEOUT：对端不可达/超时（httpx.ConnectTimeout、aiohttp 超时/ClientError）
  - ADM_ERR_AUTH_REQUIRED：401/403 或缺凭据（CredentialMissing）
⛔ 新的 ad-hoc 前缀（手写 "timeout"/"unauthorized" 当码用）——一律 import homesdk.adm.errors。

负控：_NO_CODE_SAMPLES（旧形状：失败无码）必须被形状尺判为"缺码"。
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx

import butler.af_bridge as af_mod
import butler.integrations.memory_agent as ma_mod
from butler.integrations.memory_agent import MemoryAgentClient
from homesdk.adm.errors import ADM_ERR_AUTH_REQUIRED, ADM_ERR_UPSTREAM_TIMEOUT

MA_SRC = open(ma_mod.__file__, encoding="utf-8").read()
AF_SRC = open(af_mod.__file__, encoding="utf-8").read()
_NO_CODE_SAMPLES = [
    'return {"ok": False, "error": str(e)}',
    'logger.warning("af_bridge answer failed: %s", e)',
]


def _ma_env(**sets):
    """PATH 安全快照：摘 MEMORY_AGENT*/HOMESDK* 键，其余 env 原样保留（批136 同式）。"""
    base = {k: v for k, v in os.environ.items()
            if "MEMORY_AGENT" not in k and not k.startswith("HOMESDK_")}
    base.update({"MEMORY_AGENT_URL": "http://127.0.0.1:9",
                 **{k: v for k, v in sets.items() if v is not None}})
    return mock.patch.dict(os.environ, base, clear=True)


def _client() -> MemoryAgentClient:
    return MemoryAgentClient(SimpleNamespace(memory_agent_mcp_url="http://127.0.0.1:1/mcp"))


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        raise httpx.HTTPStatusError("boom", request=None, response=self)  # 不该被用到


def _fake_httpx(**behaviour):
    """AsyncClient 替身：get/post 按 behaviour 返回 resp 或抛给定异常实例。"""
    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            v = behaviour.get("get")
            if isinstance(v, Exception):
                raise v
            return v

        async def post(self, url, **kw):
            v = behaviour.get("post")
            if isinstance(v, Exception):
                raise v
            return v
    return _C


class ShapeRuler(unittest.TestCase):
    def test_ruler_bites_no_code_shape(self):
        # 负控：旧"失败无码"形状必须被同一个"code 在场"判据拒掉。
        for sample in _NO_CODE_SAMPLES:
            self.assertNotIn('"code"', sample)

    def test_both_modules_import_contract_codes(self):
        for src, rel in ((MA_SRC, "memory_agent.py"), (AF_SRC, "af_bridge.py")):
            self.assertIn("from homesdk.adm.errors import", src, rel)
            self.assertIn(ADM_ERR_UPSTREAM_TIMEOUT, src, rel)
            self.assertIn(ADM_ERR_AUTH_REQUIRED, src, rel)

    def test_memory_agent_results_carry_code(self):
        self.assertGreaterEqual(MA_SRC.count('"code": ADM_ERR_'), 3,
                                "MA 失败结果 dict 应普遍带码落点（§7.2/§B）")


class MemoryAgentCodeLegs(unittest.IsolatedAsyncioTestCase):
    async def test_butler_fetch_timeout_carries_upstream_code(self):
        with _ma_env(MEMORY_AGENT_BUTLER_TOKEN="t9"):
            with mock.patch.object(ma_mod.httpx, "AsyncClient",
                                   _fake_httpx(get=httpx.ConnectTimeout("ma down"))):
                res = await _client()._butler_fetch("/api/members")
        self.assertFalse(res["ok"])
        self.assertEqual(res.get("code"), ADM_ERR_UPSTREAM_TIMEOUT, res)

    async def test_butler_fetch_401_carries_auth_code(self):
        with _ma_env(MEMORY_AGENT_BUTLER_TOKEN="t9"):
            with mock.patch.object(ma_mod.httpx, "AsyncClient",
                                   _fake_httpx(get=_FakeResp(401))):
                res = await _client()._butler_fetch("/api/members")
        self.assertFalse(res["ok"])
        self.assertEqual(res.get("code"), ADM_ERR_AUTH_REQUIRED, res)

    async def test_butler_fetch_missing_credential_carries_auth_code(self):
        with _ma_env():  # 无 butler token：构造头即 CredentialMissing，不外发
            res = await _client()._butler_fetch("/api/members")
        self.assertFalse(res["ok"])
        self.assertEqual(res.get("code"), ADM_ERR_AUTH_REQUIRED, res)

    async def test_analyze_room_live_missing_basic_carries_auth_code(self):
        with _ma_env():  # 无 USER/PASS
            res = await _client().analyze_room_live()
        self.assertFalse(res["ok"])
        self.assertEqual(res.get("code"), ADM_ERR_AUTH_REQUIRED, res)


class _FakeAiohttp:
    ClientError = __import__("aiohttp").ClientError

    def __init__(self, behaviour):
        self._behaviour = behaviour

    def ClientTimeout(self, **kw):
        return None

    def ClientSession(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _mk(self, verb):
        v = self._behaviour.get(verb)
        if isinstance(v, Exception):
            raise v

        class _Ctx:
            def __init__(self, resp):
                self.resp = resp

            async def __aenter__(self):
                return self.resp

            async def __aexit__(self, *exc):
                return False
        return _Ctx(v)

    def get(self, url, **kw):
        return self._mk("get")

    def post(self, url, **kw):
        return self._mk("post")


class _FakeAFResp:
    def __init__(self, status):
        self.status = status

    async def text(self):
        return "denied"

    async def json(self):
        return {"ok": True}


class AfBridgeCodeLegs(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved_pending = dict(af_mod._pending)
        af_mod._pending.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        af_mod._pending.clear()
        af_mod._pending.update(self._saved_pending)

    async def test_answer_timeout_logs_upstream_code(self):
        af_mod._pending["a1"] = {"room": "客厅", "prompt": "?", "prompt_ts": time.time()}
        aio = _FakeAiohttp({"post": asyncio.TimeoutError("af down")})
        with mock.patch.object(af_mod, "aiohttp", aio):
            with self.assertLogs("butler.af_bridge", level="ERROR") as cap:
                ok = await af_mod.on_user_reply("客厅", "好的")
        self.assertFalse(ok)
        self.assertIn(ADM_ERR_UPSTREAM_TIMEOUT, "\n".join(cap.output))

    async def test_answer_401_logs_auth_code(self):
        af_mod._pending["a1"] = {"room": "客厅", "prompt": "?", "prompt_ts": time.time()}
        aio = _FakeAiohttp({"post": _FakeAFResp(401)})
        with mock.patch.object(af_mod, "aiohttp", aio):
            with self.assertLogs("butler.af_bridge", level="ERROR") as cap:
                ok = await af_mod.on_user_reply("客厅", "好的")
        self.assertFalse(ok)
        self.assertIn(ADM_ERR_AUTH_REQUIRED, "\n".join(cap.output))

    async def test_poll_auth_failure_names_code_in_exception(self):
        aio = _FakeAiohttp({"get": _FakeAFResp(403)})
        with mock.patch.object(af_mod, "aiohttp", aio):
            with self.assertRaises(RuntimeError) as cm:
                await af_mod._poll_once()
        self.assertIn(ADM_ERR_AUTH_REQUIRED, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
