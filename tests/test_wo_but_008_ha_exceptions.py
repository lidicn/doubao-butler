"""WO-BUT-008 回归测试：ha.py 异常类定义 + call_service_strict 异常传播。

未修前：HAError/HAEntityNotFound/HAUnauthorized/HATimeout 未定义 → NameError
        → 被外层 except 吞掉 → 静默失效。
修后：4 个异常类已定义 → 404 抛 HAEntityNotFound，可被断言捕获。
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestHAExceptions(unittest.TestCase):
    """判据 #1：4 个异常类在 ha.py 中定义且可 import。"""

    def test_exception_classes_exist(self):
        from butler.integrations.ha import HAError, HAEntityNotFound, HAUnauthorized, HATimeout
        self.assertTrue(issubclass(HAEntityNotFound, HAError))
        self.assertTrue(issubclass(HAUnauthorized, HAError))
        self.assertTrue(issubclass(HATimeout, HAError))
        e = HAEntityNotFound("test", status_code=404)
        self.assertEqual(e.status_code, 404)
        self.assertEqual(str(e), "test")

    def test_call_service_strict_404_raises_entity_not_found(self):
        """判据 #2：httpx 返回 404 → 抛 HAEntityNotFound（不是 NameError、不是被吞）。"""
        from butler.integrations.ha import HAClient, HAEntityNotFound

        class FakeSettings:
            ha_token = "test-token"
            ha_url = "http://ha.example:8123"

        client = HAClient(FakeSettings())
        mock_response = AsyncMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.content = b'{}'
        mock_response.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("butler.integrations.ha.httpx.AsyncClient", return_value=mock_client):
            with self.assertRaises(HAEntityNotFound) as ctx:
                asyncio.get_event_loop().run_until_complete(
                    client.call_service_strict("light", "turn_on", {"entity_id": "light.test"})
                )
            self.assertEqual(ctx.exception.status_code, 404)

    def test_call_service_strict_401_raises_unauthorized(self):
        from butler.integrations.ha import HAClient, HAUnauthorized

        class FakeSettings:
            ha_token = "test-token"
            ha_url = "http://ha.example:8123"

        client = HAClient(FakeSettings())
        mock_response = AsyncMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_response.content = b''
        mock_response.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("butler.integrations.ha.httpx.AsyncClient", return_value=mock_client):
            with self.assertRaises(HAUnauthorized) as ctx:
                asyncio.get_event_loop().run_until_complete(
                    client.call_service_strict("light", "turn_on", {})
                )
            self.assertEqual(ctx.exception.status_code, 401)

    def test_no_token_raises_ha_error(self):
        from butler.integrations.ha import HAClient, HAError

        class FakeSettings:
            ha_token = ""
            ha_url = "http://ha.example:8123"

        client = HAClient(FakeSettings())
        with self.assertRaises(HAError) as ctx:
            asyncio.get_event_loop().run_until_complete(
                client.call_service_strict("light", "turn_on", {})
            )
        self.assertIn("token", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
