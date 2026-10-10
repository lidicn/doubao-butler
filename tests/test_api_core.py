"""API 层核心端点测试：健康检查、登录状态、404、静态首页。

批187 形制收编：本档原为 pytest 形制（模块级 `def test_*` ＋ `@pytest.fixture`），
`python -m unittest` 收 0 格⇒ 全量回归里一直是 `rc=5 ran=0`（一次都没跑过）。
现改为 `unittest.TestCase`，格数 1:1 保持 4 格，判据内容与原断言逐条对应；
地面真值见 `workorders/readings/1011/b187_pytest_ground_1011.txt`（22:14:36Z，本档 4 格全绿）。
"""
from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from starlette.testclient import TestClient  # noqa: E402


class TestApiCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from butler.app import create_app
        cls.client = TestClient(create_app())

    def test_health_check(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue("status" in data or "ok" in data, f"健康检查 body 里没有状态字段：{data}")

    def test_login_status(self):
        response = self.client.get("/api/login/status")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), dict)

    def test_404_not_found(self):
        response = self.client.get("/api/nonexistent_endpoint")
        self.assertEqual(response.status_code, 404)

    def test_static_files_served(self):
        response = self.client.get("/")
        self.assertIn(response.status_code, (200, 302, 307),
                      f"根路径既没出 WebUI 也没重定向：{response.status_code}")


if __name__ == "__main__":
    unittest.main()
