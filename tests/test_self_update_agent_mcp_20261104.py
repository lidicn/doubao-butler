"""2026-11-04 自更新改造门：① 去掉版本号二次确认；② 开发者 agent MCP 通道（system_update/check/status）。

改动依据：出资人令「把这个输入版本号都设定移除了。新增加开发者 agent 升级 DB 的通道（不用点 webui）」。
本门钉两件事：工具入册 + MCP 触发器真的写请求标记（未启用/坏 ref 拒）。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from butler.mcp import schema_gen  # noqa: E402
from butler.mcp import server as mcp_server  # noqa: E402
from butler.api import update_routes as ur  # noqa: E402


class _Settings:
    def __init__(self, enabled=True, branch="main"):
        self.self_update_enabled = enabled
        self.update_branch = branch
        self.update_repo_url = "https://github.com/lidicn/doubao-butler.git"


class _RT:
    def __init__(self, s):
        self.settings = s


class ToolRegistration(unittest.TestCase):
    def test_01_three_tools_registered(self):
        names = schema_gen.get_tool_names()
        for t in ("system_update", "system_update_check", "system_update_status"):
            self.assertIn(t, names)

    def test_02_dispatch_has_branches(self):
        src = pathlib.Path(mcp_server.__file__).read_text(encoding="utf-8")
        for t in ("system_update", "system_update_check", "system_update_status"):
            self.assertIn('name == "%s"' % t, src, "MCP 分发缺 %s 分支" % t)


class ToolBehavior(unittest.TestCase):
    def _ctx(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        files = {
            ur.PREVIEW_NAME: tmp / ur.PREVIEW_NAME,
            ur.MARKER_NAME: tmp / ur.MARKER_NAME,
            ur.RESULT_NAME: tmp / ur.RESULT_NAME,
        }
        patch = mock.patch.object(ur, "_path", side_effect=lambda name: files[name])
        return files, patch

    def test_03_system_update_writes_marker(self):
        srv = mcp_server.MCPServer()
        files, patch = self._ctx()
        with patch:
            res = srv._tool_system_update(_RT(_Settings()), {"ref": "main"})
        self.assertFalse(res.get("isError"), res)
        self.assertTrue(files[ur.MARKER_NAME].exists())
        marker = json.loads(files[ur.MARKER_NAME].read_text(encoding="utf-8"))
        self.assertEqual(marker["ref"], "main")
        self.assertEqual(marker["requested_by"], "agent:mcp")

    def test_04_disabled_refused(self):
        srv = mcp_server.MCPServer()
        files, patch = self._ctx()
        with patch:
            res = srv._tool_system_update(_RT(_Settings(enabled=False)), {})
        self.assertTrue(res.get("isError"))
        self.assertFalse(files[ur.MARKER_NAME].exists())

    def test_05_bad_ref_refused(self):
        srv = mcp_server.MCPServer()
        files, patch = self._ctx()
        with patch:
            res = srv._tool_system_update(_RT(_Settings()), {"ref": "../../etc"})
        self.assertTrue(res.get("isError"))
        self.assertFalse(files[ur.MARKER_NAME].exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
