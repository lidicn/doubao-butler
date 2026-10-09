"""批63 验收：DeskPilot PM 发送 URL 的基址改走 settings（⛔ 烤死 LAN 整串）。

被修面：butler/api/pwa_chat_routes.py 与 butler/tools/desk_pilot.py 各烤一枚
`DESKPILOT_PM_SEND_URL = "http://192.168.2.201:8765/api/v1/pm/send"`，而
settings 里已有 `deskpilot_http_url`（env `DESKPILOT_HTTP_URL`）⇒ 换 DeskPilot
地址时改 settings 对这两条发送路径不生效。
"""
from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import butler.api.pwa_chat_routes as pwa
import butler.config as cfg
import butler.core.tools as core_tools
import butler.tools as tools_pkg
import butler.tools.desk_pilot as dp

LAN = "192.168.2.201"
BASE = "http://dp.test:9999"
EXPECTED = BASE + "/api/v1/pm/send"


class HelperShapeTest(unittest.TestCase):
    def _url(self, mod, base):
        with patch.object(mod, "get_settings", return_value=SimpleNamespace(deskpilot_http_url=base)):
            return mod._deskpilot_pm_send_url()

    def test_pwa_helper_url_tracks_settings_base(self):
        self.assertEqual(self._url(pwa, BASE), EXPECTED)

    def test_desk_pilot_helper_url_tracks_settings_base(self):
        self.assertEqual(self._url(dp, BASE), EXPECTED)

    def test_helpers_trim_trailing_slash(self):
        for mod in (pwa, dp):
            with self.subTest(mod=mod.__name__):
                self.assertEqual(self._url(mod, BASE + "/"), EXPECTED)


class WiringProofTest(unittest.TestCase):
    def _send_src(self, fn):
        return inspect.getsource(fn)

    def test_call_sites_use_helper_and_keep_no_literal(self):
        for fn in (pwa.pwa_chat_send, dp._deskpilot_pm_send):
            src = self._send_src(fn)
            with self.subTest(fn=fn.__name__):
                self.assertIn("_deskpilot_pm_send_url()", src)
                self.assertNotIn(LAN, src)
                self.assertNotIn("DESKPILOT_PM_SEND_URL", src)

    def test_removed_constant_is_not_reexported(self):
        for pkg in (tools_pkg, core_tools):
            with self.subTest(pkg=pkg.__name__):
                self.assertFalse(hasattr(pkg, "DESKPILOT_PM_SEND_URL"))
                self.assertNotIn("DESKPILOT_PM_SEND_URL", getattr(pkg, "__all__", []))

    def test_ruler_bites_positive_control(self):
        # 反证 LAN 常量本身可用：config.py 里仍应命中（合法默认值，本批不改它）
        self.assertIn(LAN, inspect.getsource(cfg))


if __name__ == "__main__":
    unittest.main(verbosity=2)
