# -*- coding: utf-8 -*-
"""批221：`ParamError` 在装配层统一映射成 400（⛔ 冒 500）。

判据来源：`doc/审计报告/DB_第五期_33轮审计汇总报告_20261015.md` §八-④——
「补丁是行为变更（500 → 400）」，报告把这一步列成待拍板项：站点若改用会抛的尺子，
装配层必须有一条收得住的路，否则把「客户端给错参」升级成「服务端崩」。
`butler/app.py` 的 `exception_handlers` 原本只有 `{Exception: 500}` 一条。
"""
import unittest

from butler.param import ParamError


class TestAppParam400(unittest.TestCase):
    def _client(self):
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from butler.app import create_app

        app = create_app()

        async def bad_param(request):
            raise ParamError("limit", "不得小于 0")

        async def other_boom(request):
            raise RuntimeError("boom")

        app.router.routes.insert(0, Route("/__param_probe", bad_param, methods=["GET"]))
        app.router.routes.insert(1, Route("/__other_probe", other_boom, methods=["GET"]))
        # raise_server_exceptions=False：其余异常走 ServerErrorMiddleware 那条 500 腿，
        # 默认形制会把异常重新抛回测试进程，读不到状态码。
        return TestClient(app, raise_server_exceptions=False)

    def test_param_error_becomes_400_and_names_the_param(self):
        client = self._client()
        resp = client.get("/__param_probe")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("limit", resp.json()["error"])

    def test_unrelated_exception_still_becomes_500(self):
        """正对照：⛔ 把两条处理器读成同一条——只有 ParamError 走 400。"""
        client = self._client()
        self.assertEqual(client.get("/__other_probe").status_code, 500)


if __name__ == "__main__":
    unittest.main()
