"""DeskPilot 工具接入测试：客户端 + 工具分发 + schema 注册 + 观察验证器。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from butler.core.tools import TOOL_SCHEMAS, dispatch_tool


DESK_TOOL_NAMES = {
    "desk_system_status", "desk_system_notify", "desk_system_run",
    "desk_volume_get", "desk_volume_set", "desk_volume_mute",
    "desk_windows_list", "desk_windows_activate", "desk_windows_maximize", "desk_windows_close",
    "desk_music_play",
}


class TestSchemaRegistration(unittest.TestCase):
    def test_all_desk_tools_registered(self):
        registered = {s["function"]["name"] for s in TOOL_SCHEMAS}
        missing = DESK_TOOL_NAMES - registered
        self.assertFalse(missing, f"未注册的 DeskPilot 工具: {missing}")

    def test_desk_tools_have_descriptions(self):
        for s in TOOL_SCHEMAS:
            if s["function"]["name"].startswith("desk_"):
                self.assertGreater(len(s["function"]["description"]), 10)

    def test_desk_volume_set_has_range(self):
        for s in TOOL_SCHEMAS:
            if s["function"]["name"] == "desk_volume_set":
                props = s["function"]["parameters"]["properties"]
                self.assertEqual(props["level"]["minimum"], 0)
                self.assertEqual(props["level"]["maximum"], 100)

    def test_total_tool_count(self):
        desk_count = sum(1 for s in TOOL_SCHEMAS if s["function"]["name"].startswith("desk_"))
        self.assertEqual(desk_count, 20)


class FakeDeskPilot:
    def __init__(self):
        self.calls = []

    async def system_status(self):
        self.calls.append("system_status")
        return {"ok": True, "tool": "system_status", "result": {"cpu": 10, "memory": 50}}

    async def system_notify(self, title, message):
        self.calls.append(("system_notify", title, message))
        return {"ok": True, "tool": "system_notify", "result": {"method": "ok"}}

    async def system_run(self, path, args=""):
        self.calls.append(("system_run", path, args))
        return {"ok": True, "tool": "system_run", "result": {"pid": 1234}}

    async def volume_get(self):
        self.calls.append("volume_get")
        return {"ok": True, "tool": "volume_get", "result": {"level": 40, "muted": False}}

    async def volume_set(self, level):
        self.calls.append(("volume_set", level))
        return {"ok": True, "tool": "volume_set", "result": {"level": level}}

    async def volume_toggle_mute(self):
        self.calls.append("volume_toggle_mute")
        return {"ok": True, "tool": "volume_toggle_mute", "result": {"muted": True}}

    async def windows_list(self):
        self.calls.append("windows_list")
        return {"ok": True, "tool": "windows_list", "result": [{"title": "微信", "pid": 100}]}

    async def windows_activate(self, title):
        self.calls.append(("windows_activate", title))
        return {"ok": True, "tool": "windows_activate", "result": {"title": title}}

    async def windows_maximize(self, title):
        self.calls.append(("windows_maximize", title))
        return {"ok": True, "tool": "windows_maximize", "result": {"title": title}}

    async def windows_close(self, title):
        self.calls.append(("windows_close", title))
        return {"ok": True, "tool": "windows_close", "result": {"title": title}}

    async def lxmusic_play_by_keyword(self, keyword):
        self.calls.append(("lxmusic_play_by_keyword", keyword))
        return {"ok": True, "tool": "lxmusic_play_by_keyword", "result": {"keyword": keyword}}


class FakeAgent:
    def __init__(self, deskpilot=None):
        self.deskpilot = deskpilot


class TestDispatch(unittest.TestCase):
    def test_dispatch_no_deskpilot(self):
        agent = FakeAgent(deskpilot=None)
        r = asyncio.get_event_loop().run_until_complete(
            dispatch_tool("desk_volume_set", {"level": 50}, agent)
        )
        obj = json.loads(r)
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["error"], "not_implemented")

    def test_dispatch_system_status(self):
        dpk = FakeDeskPilot()
        agent = FakeAgent(deskpilot=dpk)
        r = asyncio.get_event_loop().run_until_complete(
            dispatch_tool("desk_system_status", {}, agent)
        )
        obj = json.loads(r)
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["result"]["cpu"], 10)

    def test_dispatch_volume_set(self):
        dpk = FakeDeskPilot()
        agent = FakeAgent(deskpilot=dpk)
        r = asyncio.get_event_loop().run_until_complete(
            dispatch_tool("desk_volume_set", {"level": 40}, agent)
        )
        obj = json.loads(r)
        self.assertTrue(obj["ok"])
        self.assertEqual(dpk.calls[0], ("volume_set", 40))

    def test_dispatch_windows_activate(self):
        dpk = FakeDeskPilot()
        agent = FakeAgent(deskpilot=dpk)
        r = asyncio.get_event_loop().run_until_complete(
            dispatch_tool("desk_windows_activate", {"title": "微信"}, agent)
        )
        obj = json.loads(r)
        self.assertTrue(obj["ok"])
        self.assertEqual(dpk.calls[0], ("windows_activate", "微信"))

    def test_dispatch_ssh_removed_from_tools(self):
        dpk = FakeDeskPilot()
        agent = FakeAgent(deskpilot=dpk)
        r = asyncio.get_event_loop().run_until_complete(
            dispatch_tool("desk_ssh_exec", {"command": "df -h"}, agent)
        )
        obj = json.loads(r)
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["error"], "invalid_parameter")

    def test_dispatch_music_play(self):
        dpk = FakeDeskPilot()
        agent = FakeAgent(deskpilot=dpk)
        r = asyncio.get_event_loop().run_until_complete(
            dispatch_tool("desk_music_play", {"keyword": "周杰伦 晴天"}, agent)
        )
        obj = json.loads(r)
        self.assertTrue(obj["ok"])
        self.assertEqual(dpk.calls[0], ("lxmusic_play_by_keyword", "周杰伦 晴天"))

    def test_dispatch_unknown_tool(self):
        dpk = FakeDeskPilot()
        agent = FakeAgent(deskpilot=dpk)
        r = asyncio.get_event_loop().run_until_complete(
            dispatch_tool("desk_nonexistent", {}, agent)
        )
        obj = json.loads(r)
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["error"], "invalid_parameter")

    def test_dispatch_exception_caught(self):
        class BrokenDP:
            async def volume_get(self):
                raise ConnectionError("refused")

        agent = FakeAgent(deskpilot=BrokenDP())
        r = asyncio.get_event_loop().run_until_complete(
            dispatch_tool("desk_volume_get", {}, agent)
        )
        obj = json.loads(r)
        self.assertFalse(obj["ok"])
        self.assertEqual(obj["error"], "unavailable")


class TestDeskPilotClient(unittest.TestCase):
    def test_init(self):
        from butler.integrations.deskpilot import DeskPilotClient
        c = DeskPilotClient("http://1.2.3.4:8765", "tok123")
        self.assertEqual(c.base, "http://1.2.3.4:8765")
        self.assertEqual(c.token, "tok123")
        self.assertEqual(c.timeout, 15.0)

    def test_health_unreachable(self):
        from butler.integrations.deskpilot import DeskPilotClient
        c = DeskPilotClient("http://192.0.2.1:9999", "tok", timeout=1.0)
        r = asyncio.get_event_loop().run_until_complete(c.health())
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "unavailable")

    def test_get_timeout(self):
        from butler.integrations.deskpilot import DeskPilotClient
        c = DeskPilotClient("http://192.0.2.1:9999", "tok", timeout=1.0)
        r = asyncio.get_event_loop().run_until_complete(c.system_status())
        self.assertFalse(r["ok"])
        self.assertIn(r["error"], ("operation_timeout", "unavailable"))


if __name__ == "__main__":
    unittest.main()
