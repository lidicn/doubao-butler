import asyncio
import tempfile
import unittest
from pathlib import Path

from butler.agent_collab import AgentCollaborationManager


class TestDelegateTask(unittest.TestCase):
    def _new_mgr(self):
        tmp = tempfile.mkdtemp()
        mgr = AgentCollaborationManager(Path(tmp) / "data")
        mgr.register_agent(
            "butler", "豆包管家", "family_assistant",
            capabilities=["dialog"], rooms=[], members=[],
        )
        mgr.register_agent(
            "agent_kevin", "凯撒", "kids_assistant",
            capabilities=["study"], rooms=[], members=["Kevin"],
        )
        return mgr

    def test_inline_handler_immediate(self):
        """内联 handler 同步返回 → delegate_task 立即拿到响应。"""
        async def run():
            mgr = self._new_mgr()

            def handler(msg):
                return f"OK:{msg.content}"

            mgr.register_handler("agent_kevin", handler)
            result = await mgr.delegate_task("butler", "讲个故事", target_agent="agent_kevin")
            return result

        result = asyncio.run(run())
        self.assertTrue(result["ok"])
        self.assertEqual(result["response"], "OK:讲个故事")

    def test_async_response_waits(self):
        """异步响应：delegate_task 应等待，直到外部 respond() 才返回。"""
        async def run():
            mgr = self._new_mgr()
            async def responder(msg):
                # 模拟异步 agent：稍后给出响应
                await asyncio.sleep(0.2)
                mgr.respond(msg.id, "agent_kevin", "故事:小王子")
                return None

            mgr.register_handler("agent_kevin", responder)
            result = await mgr.delegate_task("butler", "讲个故事", target_agent="agent_kevin")
            return result

        result = asyncio.run(run())
        self.assertTrue(result["ok"])
        self.assertEqual(result["response"], "故事:小王子")

    def test_timeout(self):
        """无响应时超时返回 ok=False / error=response_timeout。"""
        async def run():
            mgr = self._new_mgr()
            # 注册 handler 但不响应（模拟 agent 无响应）
            mgr.register_handler("agent_kevin", lambda msg: None)
            result = await mgr.delegate_task("butler", "无响应任务", target_agent="agent_kevin",
                                             timeout_sec=1)
            return result

        result = asyncio.run(run())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "response_timeout")

    def test_timeout_short(self):
        """自定义短超时（通过 AgentMessage.timeout_sec 生效路径）。"""
        async def run():
            mgr = self._new_mgr()
            mgr.register_handler("agent_kevin", lambda msg: None)
            msg = await mgr.send_message(
                "butler", "agent_kevin", "task", "快任务",
                requires_response=True, timeout_sec=1,
            )
            ev = mgr._response_events.get(msg.id)
            self.assertIsNotNone(ev)
            try:
                await asyncio.wait_for(ev.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass
            return mgr.respond(msg.id, "agent_kevin", "ok") or True

        ok = asyncio.run(run())
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
