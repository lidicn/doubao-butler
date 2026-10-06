"""LLM 退避重试测试：验证 429/5xx/网络抖动下的指数退避 + 抖动重试。"""
import asyncio
import unittest
from unittest import mock

import httpx

from butler.config import Settings
from butler.integrations.llm import LLMClient


def _settings(backoff: float = 0.01, retry_max: int = 3) -> Settings:
    s = Settings()
    s.new_api_url = "http://test/v1"
    s.new_api_key = "k"
    s.new_api_model = "m"
    s.llm_retry_max = retry_max
    s.llm_retry_backoff = backoff
    return s


class FakeResponse:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "http://test/v1/chat/completions"),
                response=self,
            )

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, queue):
        self._queue = list(queue)
        self.posts = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        self.posts += 1
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


OK_BODY = {"choices": [{"message": {"content": "ok"}}]}


def _run(client: LLMClient, fc: FakeClient):
    async def run():
        with mock.patch("butler.integrations.llm.httpx.AsyncClient", lambda **k: fc):
            return await client.chat("sys", [{"role": "user", "content": "hi"}])

    text, _ = asyncio.run(run())
    return text


class TestLLMRetry(unittest.TestCase):
    def test_429_retries_then_success(self):
        """429→429→200：重试 2 次后成功，共 3 次请求。"""
        client = LLMClient(_settings())
        fc = FakeClient([FakeResponse(429), FakeResponse(429), FakeResponse(200, OK_BODY)])
        text = _run(client, fc)
        self.assertEqual(text, "ok")
        self.assertEqual(fc.posts, 3)

    def test_429_exhausted_raises(self):
        """持续 429 超过 max_retries：抛 HTTPStatusError，不无限重试。"""
        client = LLMClient(_settings(retry_max=3))
        fc = FakeClient([FakeResponse(429)] * 4)  # 1 次初试 + 3 次重试
        with self.assertRaises(httpx.HTTPStatusError):
            _run(client, fc)
        self.assertEqual(fc.posts, 4)

    def test_400_no_retry(self):
        """4xx 非可重试（如 400 参数错）：立即抛出，不做退避。"""
        client = LLMClient(_settings())
        fc = FakeClient([FakeResponse(400)])
        with self.assertRaises(httpx.HTTPStatusError):
            _run(client, fc)
        self.assertEqual(fc.posts, 1)

    def test_5xx_retries(self):
        """503→200：5xx 同样走退避重试。"""
        client = LLMClient(_settings())
        fc = FakeClient([FakeResponse(503), FakeResponse(200, OK_BODY)])
        text = _run(client, fc)
        self.assertEqual(text, "ok")
        self.assertEqual(fc.posts, 2)

    def test_transport_error_then_success(self):
        """网络瞬态故障→成功：连接错误走退避重试。"""
        client = LLMClient(_settings())
        fc = FakeClient([httpx.ConnectError("boom"), FakeResponse(200, OK_BODY)])
        text = _run(client, fc)
        self.assertEqual(text, "ok")
        self.assertEqual(fc.posts, 2)

    def test_config_defaults(self):
        """Settings 默认值：retry_max=3, backoff=2.0，且可用环境变量覆盖。"""
        s = Settings.load()
        self.assertEqual(s.llm_retry_max, 3)
        self.assertEqual(s.llm_retry_backoff, 2.0)
        import os
        os.environ["LLM_RETRY_MAX"] = "5"
        os.environ["LLM_RETRY_BACKOFF"] = "1.5"
        try:
            s2 = Settings.load()
            self.assertEqual(s2.llm_retry_max, 5)
            self.assertEqual(s2.llm_retry_backoff, 1.5)
        finally:
            os.environ.pop("LLM_RETRY_MAX", None)
            os.environ.pop("LLM_RETRY_BACKOFF", None)


if __name__ == "__main__":
    unittest.main()
