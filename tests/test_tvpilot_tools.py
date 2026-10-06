"""TVPilot 工具分发测试：验证 dispatch_tool 正确路由到 tvpilot 客户端并返回结构化 JSON。"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="butler_test_tvp_")
os.environ["DATA_DIR"] = _TMP
os.environ["TTS_DIR"] = _TMP

from butler.core.tools import dispatch_tool


class FakeTVPilot:
    def __init__(self):
        self.calls = []

    async def foreground(self):
        self.calls.append("foreground")
        return {"ok": True, "tool": "foreground", "result": {"package": "com.tvcam.mytv"}, "cost_ms": 50}

    async def keyevent(self, key):
        self.calls.append(("keyevent", key))
        return {"ok": True, "tool": "keyevent", "result": {"key": key}, "cost_ms": 30}

    async def launch_app(self, package, activity=None):
        self.calls.append(("launch_app", package))
        return {"ok": True, "tool": "launch_app", "result": {"package": package, "foreground": True}, "cost_ms": 2000}

    async def input_text(self, text):
        self.calls.append(("input_text", text))
        return {"ok": True, "tool": "input_text", "result": {"text": text, "length": len(text)}, "cost_ms": 80}

    async def tap(self, x, y):
        self.calls.append(("tap", x, y))
        return {"ok": True, "tool": "tap", "result": {"x": x, "y": y}, "cost_ms": 40}

    async def swipe(self, x1, y1, x2, y2, duration_ms=300):
        self.calls.append(("swipe", x1, y1, x2, y2, duration_ms))
        return {"ok": True, "tool": "swipe", "result": {"x1": x1, "y1": y1}, "cost_ms": 60}

    async def screenshot(self, width=480):
        self.calls.append(("screenshot", width))
        return {"ok": True, "tool": "screenshot", "result": {"bytes": 12345, "png_base64": "iVBOR..."}, "cost_ms": 300}


def _agent(tvp=None):
    a = mock.Mock()
    a.tvpilot = tvp
    return a


class TestTVPilotDispatch(unittest.TestCase):
    def test_foreground(self):
        tvp = FakeTVPilot()
        result = asyncio.run(dispatch_tool("tv_foreground", {}, _agent(tvp)))
        d = json.loads(result)
        self.assertTrue(d["ok"])
        self.assertEqual(d["result"]["package"], "com.tvcam.mytv")
        self.assertEqual(tvp.calls, ["foreground"])

    def test_keyevent_home(self):
        tvp = FakeTVPilot()
        result = asyncio.run(dispatch_tool("tv_keyevent", {"key": "KEYCODE_HOME"}, _agent(tvp)))
        d = json.loads(result)
        self.assertTrue(d["ok"])
        self.assertEqual(tvp.calls[0], ("keyevent", "KEYCODE_HOME"))

    def test_launch_app(self):
        tvp = FakeTVPilot()
        result = asyncio.run(dispatch_tool("tv_launch_app", {"package": "com.fongmi.android.tv"}, _agent(tvp)))
        d = json.loads(result)
        self.assertTrue(d["ok"])
        self.assertTrue(d["result"]["foreground"])

    def test_input_text(self):
        tvp = FakeTVPilot()
        result = asyncio.run(dispatch_tool("tv_input_text", {"text": "hello"}, _agent(tvp)))
        d = json.loads(result)
        self.assertTrue(d["ok"])
        self.assertEqual(d["result"]["length"], 5)

    def test_tap(self):
        tvp = FakeTVPilot()
        result = asyncio.run(dispatch_tool("tv_tap", {"x": 540, "y": 180}, _agent(tvp)))
        d = json.loads(result)
        self.assertTrue(d["ok"])
        self.assertEqual(tvp.calls[0], ("tap", 540, 180))

    def test_swipe(self):
        tvp = FakeTVPilot()
        result = asyncio.run(dispatch_tool("tv_swipe", {"x1": 100, "y1": 500, "x2": 100, "y2": 200}, _agent(tvp)))
        d = json.loads(result)
        self.assertTrue(d["ok"])

    def test_screenshot(self):
        tvp = FakeTVPilot()
        result = asyncio.run(dispatch_tool("tv_screenshot", {}, _agent(tvp)))
        d = json.loads(result)
        self.assertTrue(d["ok"])
        self.assertIn("png_base64", d["result"])

    def test_no_tvpilot_returns_error(self):
        """agent 没有 tvpilot 时返回结构化错误，不崩溃。"""
        result = asyncio.run(dispatch_tool("tv_foreground", {}, _agent(None)))
        d = json.loads(result)
        self.assertFalse(d["ok"])
        self.assertEqual(d["error"], "not_implemented")

    def test_tvpilot_exception_caught(self):
        """tvpilot 方法抛异常时返回 tv_unreachable，不中断 ReAct。"""
        tvp = mock.Mock()
        async def boom():
            raise ConnectionError("refused")
        tvp.foreground = boom
        result = asyncio.run(dispatch_tool("tv_foreground", {}, _agent(tvp)))
        d = json.loads(result)
        self.assertFalse(d["ok"])
        self.assertEqual(d["error"], "tv_unreachable")


class TestTVPilotSchema(unittest.TestCase):
    def test_all_tvpilot_tools_registered(self):
        from butler.core.tools import TOOL_SCHEMAS
        names = {t["function"]["name"] for t in TOOL_SCHEMAS}
        for expected in ("tv_foreground", "tv_keyevent", "tv_launch_app",
                         "tv_input_text", "tv_tap", "tv_swipe", "tv_screenshot"):
            self.assertIn(expected, names, f"{expected} 未注册到 TOOL_SCHEMAS")


if __name__ == "__main__":
    unittest.main()
