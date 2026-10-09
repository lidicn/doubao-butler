"""批136 · PR-C2（执行计划 §八 模块2）：memory_agent 四面鉴权头收编 homesdk.auth。

0.3.2 解阻口径：本批落地时 `require_auth_headers(peer, *, token_key=)` 已在 wheel 里现读
（`auth.py` 签名腿＋`token_for` docstring 写明「同一 peer 多枚不同 token 的仓」＝DB 的
mcp/butler/app 三面）。计划原文的「0.3.2 未发前此面先保留 DB 自己的 bearer_headers」豁免
**到此失效**——homesdk 红线：库已提供的能力⛔ 手写第二份，验收是两条 grep 归零（硬线，不是建议）。

三族腿：
A. 形状尺先证会咬（负控＝把改前那三种裸拼装样喂给它，命中名单逐枚点名，⛔ 只断言「实际文件 0」）。
B. 计划 PR-C2 验收两条 grep 在生产文件上＝0，且 import 了 require_auth_headers／require_basic_headers、
   用了 token_key=（三面隔离的正腿——不显式给键会顺着 LEGACY 表串面）。
C. 行为腿（os.environ clear=True 全隔离，测试值＝肉眼假的 test 串，⛔ 触任何真凭据）：
   env 现读赢过 Settings 旧值、三面各取各键互不串、缺凭据逐面抛 CredentialMissing（面名＋键名都点）、
   Basic 走 legacy 键成套、缺一半视同没有。

盲点（自报）：行为腿验的是**镜像宿主**装的 0.3.2；现网容器生效仍挂合并窗（码面≠进程面，
承台账 §92 那条口径）。`homesdk.auth` 键优先级（HOMESDK_/TOKEN_ 形制）归库自己的测试，本批⛔ 重测库。
"""
from __future__ import annotations

import base64
import os
import pathlib
import re
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from butler.integrations.memory_agent import CredentialMissing, MemoryAgentClient  # noqa: E402

SRC = (REPO / "butler" / "integrations" / "memory_agent.py").read_text(encoding="utf-8")


def _ma_env(**sets):
    """快照形制：只摘掉所有 MEMORY_AGENT/HOMESDK_ 凭据键，其余 env（PATH 等）原样保留。
    早前用 clear=True 把 PATH 一起清了，惰性 import 的 trio 当场踩空＝测试自己的洞。"""
    base = {k: v for k, v in os.environ.items()
            if "MEMORY_AGENT" not in k and not k.startswith("HOMESDK_")}
    base.update({k: v for k, v in sets.items() if v is not None})
    return mock.patch.dict(os.environ, base, clear=True)

OLD_SHAPE_SAMPLES = [
    ('return {"Authorization": "Bearer " + token}', ["Bearer-literal", "Authorization-literal"]),
    ('return {"Authorization": "Basic " + b64}', ["Bearer-literal", "Authorization-literal"]),
    ('h["Authorization"] = tok', ["Authorization-literal"]),
]


def handcrafted_hits(text: str) -> list[str]:
    """裸凭据拼装的两条形制。命中的是**写法**不是值，⛔ 拿值当判据。"""
    hits = []
    if '"Bearer "' in text or "'Bearer '" in text or '"Basic "' in text or "'Basic '" in text:
        hits.append("Bearer-literal")
    if '"Authorization"' in text or "'Authorization'" in text:
        hits.append("Authorization-literal")
    return hits


class ShapeRulerTest(unittest.TestCase):
    """A：尺必须先咬得动改前那几种写法。"""

    def test_ruler_bites_old_shapes(self):
        for sample, want in OLD_SHAPE_SAMPLES:
            with self.subTest(sample=sample):
                self.assertEqual(handcrafted_hits(sample), want)

    def test_production_has_no_handcrafted_headers(self):
        self.assertEqual(handcrafted_hits(SRC), [], f"仍有裸拼装：{handcrafted_hits(SRC)}")

    def test_plan_acceptance_greps_zero(self):
        # PR-C2 验收原文两条（对 memory_agent.py）：
        self.assertEqual(re.findall(r"Authorization.*Bearer", SRC), [])
        self.assertEqual(re.findall(r"basic_auth|bearer_headers", SRC), [])

    def test_uses_homesdk_auth_with_token_key(self):
        self.assertIn("from homesdk.auth import", SRC)
        for name in ("require_auth_headers", "require_basic_headers"):
            self.assertIn(name, SRC)
        self.assertIn("token_key=", SRC)
        for gone in ("def bearer_headers", "def basic_auth", "def _token_for"):
            self.assertNotIn(gone, SRC)


class BehaviorTest(unittest.TestCase):
    """C：四面都由 homesdk 现读 env；缺凭据 fail-closed 的语义逐面点名。"""

    def _client(self):
        st = SimpleNamespace(memory_agent_mcp_url="http://127.0.0.1:1/mcp",
                             memory_agent_token="settings-stale-must-not-win")
        return MemoryAgentClient(st)

    def test_mcp_header_env_live_read(self):
        with _ma_env(MEMORY_AGENT_TOKEN="tok-mcp-env"):
            h = self._client()._headers()
        self.assertEqual(h.get("Authorization"), "Bearer tok-mcp-env")
        self.assertIn("Content-Type", h)

    def test_surfaces_isolated_by_token_key(self):
        c = self._client()
        with _ma_env(MEMORY_AGENT_TOKEN="t1",
                     MEMORY_AGENT_BUTLER_TOKEN="t2",
                     MEMORY_AGENT_APP_TOKEN="t3"):
            self.assertEqual(c._headers()["Authorization"], "Bearer t1")
            self.assertEqual(c._butler_headers()["Authorization"], "Bearer t2")
            self.assertEqual(c._app_token_headers()["Authorization"], "Bearer t3")

    def test_missing_token_fail_closed_per_surface(self):
        c = self._client()
        with _ma_env():
            for meth, surface, key in (
                (c._headers, "mcp", "MEMORY_AGENT_TOKEN"),
                (c._butler_headers, "butler", "MEMORY_AGENT_BUTLER_TOKEN"),
                (c._app_token_headers, "app", "MEMORY_AGENT_APP_TOKEN"),
            ):
                with self.subTest(surface=surface):
                    with self.assertRaises(CredentialMissing) as cm:
                        meth()
                    self.assertEqual(cm.exception.surface, surface)
                    self.assertEqual(cm.exception.env_key, key)

    def test_basic_headers_legacy_keys(self):
        exp = "Basic " + base64.b64encode("u4:p4".encode("utf-8")).decode("ascii")
        with _ma_env(MEMORY_AGENT_USER="u4", MEMORY_AGENT_PASS="p4"):
            h = self._client()._ma_basic()
        self.assertEqual(h["Authorization"], exp)

    def test_basic_missing_half_fail_closed(self):
        with _ma_env(MEMORY_AGENT_USER="u4"):
            with self.assertRaises(CredentialMissing) as cm:
                self._client()._ma_basic()
        self.assertEqual(cm.exception.surface, "basic")

    def test_vision_leg_still_guarded(self):
        # 收编后实时识别腿必须仍走那把严格 Basic（⛔ 退化成匿名请求）
        self.assertIn("self._ma_basic()", SRC)


if __name__ == "__main__":
    unittest.main()
