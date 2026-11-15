# -*- coding: utf-8 -*-
"""批135（§9 任务2，先红后码）：订阅 af/automation/fired|failed 走 MQTT（DCD 2026-10-08 采纳，替代 HTTP 轮询获知自动化结果）。

验收（执行计划 §9.1-2 原文）：AF 发布 fired → DB 订阅收到 → 告知用户；失败事件带 `error` 可读。
契约：ADM联动主题注册表与消息契约.md §1.2 行 57-58——
  fired  {trace_id, ts, automation_id, ref}
  failed {trace_id, ts, automation_id, ref, error}

2026-11-15 补：通知腿曾误 import `butler.notifier.router.get_router`（该函数不存在），
fe 从没发出去且只落 WARNING。本档锁死：入口必须是 `butler.notify.singleton.get_router`，
按 `NotifyRouter.notify(channel, text, **kwargs)`（async）调用，router=None/通道未送达都不许假绿。
"""
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from butler.bus import topics
from butler.bus.af_events import handle_af_automation_event

REPO = Path(__file__).resolve().parent.parent
APP_PY = REPO / "butler" / "app.py"
AF_EVENTS_PY = REPO / "butler" / "bus" / "af_events.py"


class TestTopicsRegistered(unittest.TestCase):
    def test_fired_failed_constants(self):
        self.assertEqual(topics.SUB_AF_AUTOMATION_FIRED, "af/automation/fired")
        self.assertEqual(topics.SUB_AF_AUTOMATION_FAILED, "af/automation/failed")

    def test_both_in_sub_topics(self):
        self.assertIn(topics.SUB_AF_AUTOMATION_FIRED, topics.SUB_TOPICS)
        self.assertIn(topics.SUB_AF_AUTOMATION_FAILED, topics.SUB_TOPICS)


class TestImportPoint(unittest.TestCase):
    """① 入口包名必须正确：notify.singleton，不是 notifier.router。"""

    def test_uses_notify_singleton(self):
        src = AF_EVENTS_PY.read_text(encoding="utf-8")
        self.assertIn("from butler.notify.singleton import get_router", src)
        self.assertNotIn("from butler.notifier.router import get_router", src)


class TestHandler(unittest.TestCase):
    def _run(self, topic, payload, notify=None):
        return asyncio.run(handle_af_automation_event(topic, payload, notify=notify))

    def test_fired_notifies_user(self):
        notes = []
        res = self._run(
            topics.SUB_AF_AUTOMATION_FIRED,
            {"trace_id": "t-1", "ts": 1.0, "automation_id": "a-42", "ref": "晨间灯光"},
            notify=lambda title, body: notes.append((title, body)),
        )
        self.assertEqual(res, "notified")
        self.assertEqual(len(notes), 1)
        title, body = notes[0]
        self.assertIn("a-42", body)
        self.assertIn("晨间灯光", body)
        self.assertIn("t-1", body)

    def test_failed_error_is_readable(self):
        notes = []
        res = self._run(
            topics.SUB_AF_AUTOMATION_FAILED,
            {"trace_id": "t-2", "ts": 1.0, "automation_id": "a-7", "ref": "浇花", "error": "device timeout"},
            notify=lambda title, body: notes.append((title, body)),
        )
        self.assertEqual(res, "notified")
        _, body = notes[0]
        self.assertIn("device timeout", body)

    def test_missing_trace_id_dropped(self):
        notes = []
        res = self._run(
            topics.SUB_AF_AUTOMATION_FIRED,
            {"ts": 1.0, "automation_id": "a-1"},
            notify=lambda title, body: notes.append((title, body)),
        )
        self.assertEqual(res, "dropped_invalid")
        self.assertEqual(notes, [])

    def test_non_dict_dropped(self):
        notes = []
        res = self._run(
            topics.SUB_AF_AUTOMATION_FAILED, "not-an-object",
            notify=lambda title, body: notes.append((title, body)),
        )
        self.assertEqual(res, "dropped_invalid")
        self.assertEqual(notes, [])

    def test_failed_without_error_still_notifies_with_fallback(self):
        notes = []
        res = self._run(
            topics.SUB_AF_AUTOMATION_FAILED,
            {"trace_id": "t-3", "automation_id": "a-3", "ref": "r"},
            notify=lambda title, body: notes.append((title, body)),
        )
        self.assertEqual(res, "notified")
        self.assertIn("未提供", notes[0][1])

    def test_notify_exception_does_not_raise(self):
        def boom(title, body):
            raise RuntimeError("bark down")
        res = self._run(
            topics.SUB_AF_AUTOMATION_FIRED,
            {"trace_id": "t-4", "automation_id": "a-4", "ref": "r"},
            notify=boom,
        )
        self.assertEqual(res, "notify_failed")

    def test_async_notify_stub_is_awaited(self):
        # 生产 _default_notify 是 async：注入协程也必须被 await，不能留下未 awaited 告警。
        seen = []

        async def anotify(title, body):
            seen.append((title, body))

        res = self._run(
            topics.SUB_AF_AUTOMATION_FIRED,
            {"trace_id": "t-5", "automation_id": "a-5", "ref": "r"},
            notify=anotify,
        )
        self.assertEqual(res, "notified")
        self.assertEqual(len(seen), 1)


class TestDefaultNotifyRouter(unittest.IsolatedAsyncioTestCase):
    """② router=None 不炸且留可读告警；③ router 存在时按真实签名调通（打桩）。"""

    async def test_router_none_named_not_silent(self):
        with mock.patch("butler.notify.singleton.get_router", return_value=None):
            with self.assertLogs("butler.bus.af_events", level="WARNING") as cm:
                res = await handle_af_automation_event(
                    topics.SUB_AF_AUTOMATION_FIRED,
                    {"trace_id": "t-none", "automation_id": "a", "ref": "r"},
                )
        self.assertEqual(res, "notify_failed")
        self.assertTrue(any("notify router" in line for line in cm.output), cm.output)

    async def test_router_present_calls_bark_notify(self):
        calls = []

        class FakeRouter:
            async def notify(self, channel, text, **kwargs):
                calls.append((channel, text, kwargs))
                return SimpleNamespace(results={"bark": SimpleNamespace(ok=True, error=None)})

        with mock.patch("butler.notify.singleton.get_router", return_value=FakeRouter()):
            res = await handle_af_automation_event(
                topics.SUB_AF_AUTOMATION_FIRED,
                {"trace_id": "t-ok", "automation_id": "a-9", "ref": "晨灯"},
            )
        self.assertEqual(res, "notified")
        self.assertEqual(len(calls), 1)
        channel, text, kwargs = calls[0]
        self.assertEqual(channel, "bark")
        self.assertIn("a-9", text)
        self.assertIn("t-ok", text)
        self.assertEqual(kwargs.get("title"), "自动化完成")

    async def test_bark_channel_failure_is_not_green(self):
        class FakeRouter:
            async def notify(self, channel, text, **kwargs):
                return SimpleNamespace(results={"bark": SimpleNamespace(ok=False, error="mode_blocked")})

        with mock.patch("butler.notify.singleton.get_router", return_value=FakeRouter()):
            res = await handle_af_automation_event(
                topics.SUB_AF_AUTOMATION_FAILED,
                {"trace_id": "t-fail", "automation_id": "a", "ref": "r", "error": "boom"},
            )
        self.assertEqual(res, "notify_failed")


class TestAppWiring(unittest.TestCase):
    """app.py `_consume` 必须有两枚分支且委托给 af_events（源码形状尺，⛔ 只测函数不测接线）。"""

    @classmethod
    def setUpClass(cls):
        cls.src = APP_PY.read_text(encoding="utf-8")

    def test_branches_delegate(self):
        self.assertIn("elif topic in (SUB_AF_AUTOMATION_FIRED, SUB_AF_AUTOMATION_FAILED):", self.src)
        self.assertIn("handle_af_automation_event(topic, payload)", self.src)

    def test_delegation_is_awaited(self):
        self.assertIn("await handle_af_automation_event(topic, payload)", self.src)


if __name__ == "__main__":
    unittest.main()
