"""第二十轮 D10（DCD 20261009 裁丙）：百度 TTS token 腿不得从发声路径抛穿。

现读缺陷：`synthesize` 的 `token = await self._get_token()` 在 try 之外（旧 :43），
且 `_get_token` 的 `data["access_token"]`（旧 :36）无守护——填了密钥而百度返回错误响应时，
KeyError/异常直接从播报链抛出。裁丙＝硬化不删不接：失败返回 None + error 日志。
"""
import unittest
from unittest.mock import patch

from butler.core.tts_baidu import BaiduTTS


class TestBaiduTokenLeg(unittest.IsolatedAsyncioTestCase):
    def _tts(self) -> BaiduTTS:
        return BaiduTTS(api_key="k", secret_key="s")

    async def test_token_failure_returns_none_and_logs_not_raises(self):
        tts = self._tts()
        boom = KeyError("access_token")
        with patch.object(tts, "_get_token", side_effect=boom), \
             self.assertLogs("butler.core.tts_baidu", level="ERROR") as cm:
            result = await tts.synthesize("测试")
        self.assertIsNone(result)
        self.assertIn("token", " ".join(cm.output).lower())

    async def test_token_success_still_reaches_request_path(self):
        # 正对照：token 腿通了才会去碰合成请求——硬化没把活路径阉掉
        tts = self._tts()
        reached = {}

        async def fake_post(*a, **k):
            raise AssertionError("stop-after-marker")

        async def fake_token():
            reached["token"] = True
            return "tok"

        with patch.object(tts, "_get_token", fake_token):
            import butler.core.tts_baidu as m
            with patch.object(m.aiohttp, "ClientSession", fake_post):
                try:
                    await tts.synthesize("测试")
                except AssertionError as e:
                    self.assertEqual(str(e), "stop-after-marker")
        self.assertTrue(reached.get("token"))


if __name__ == "__main__":
    unittest.main()
