# -*- coding: utf-8 -*-
"""批135（§9 任务2，先红后码）：订阅 af/automation/fired|failed 走 MQTT（DCD 2026-10-08 采纳，替代 HTTP 轮询获知自动化结果）。

验收（执行计划 §9.1-2 原文）：AF 发布 fired → DB 订阅收到 → 告知用户；失败事件带 `error` 可读。
契约：ADM联动主题注册表与消息契约.md §1.2 行 57-58——
  fired  {trace_id, ts, automation_id, ref}
  failed {trace_id, ts, automation_id, ref, error}
"""
import unittest
from pathlib import Path

from butler.bus import topics
from butler.bus.af_events import handle_af_automation_event

APP_PY = Path(__file__).resolve().parent.parent / "butler" / "app.py"


class TestTopicsRegistered(unittest.TestCase):
    def test_fired_failed_constants(self):
        self.assertEqual(topics.SUB_AF_AUTOMATION_FIRED, "af/automation/fired")
        self.assertEqual(topics.SUB_AF_AUTOMATION_FAILED, "af/automation/failed")

    def test_both_in_sub_topics(self):
        self.assertIn(topics.SUB_AF_AUTOMATION_FIRED, topics.SUB_TOPICS)
        self.assertIn(topics.SUB_AF_AUTOMATION_FAILED, topics.SUB_TOPICS)


class TestHandler(unittest.TestCase):
    def test_fired_notifies_user(self):
        notes = []
        res = handle_af_automation_event(
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
        res = handle_af_automation_event(
            topics.SUB_AF_AUTOMATION_FAILED,
            {"trace_id": "t-2", "ts": 1.0, "automation_id": "a-7", "ref": "浇花", "error": "device timeout"},
            notify=lambda title, body: notes.append((title, body)),
        )
        self.assertEqual(res, "notified")
        _, body = notes[0]
        self.assertIn("device timeout", body)

    def test_missing_trace_id_dropped(self):
        notes = []
        res = handle_af_automation_event(
            topics.SUB_AF_AUTOMATION_FIRED,
            {"ts": 1.0, "automation_id": "a-1"},
            notify=lambda title, body: notes.append((title, body)),
        )
        self.assertEqual(res, "dropped_invalid")
        self.assertEqual(notes, [])

    def test_non_dict_dropped(self):
        notes = []
        res = handle_af_automation_event(
            topics.SUB_AF_AUTOMATION_FAILED, "not-an-object",
            notify=lambda title, body: notes.append((title, body)),
        )
        self.assertEqual(res, "dropped_invalid")
        self.assertEqual(notes, [])

    def test_failed_without_error_still_notifies_with_fallback(self):
        notes = []
        res = handle_af_automation_event(
            topics.SUB_AF_AUTOMATION_FAILED,
            {"trace_id": "t-3", "automation_id": "a-3", "ref": "r"},
            notify=lambda title, body: notes.append((title, body)),
        )
        self.assertEqual(res, "notified")
        self.assertIn("未提供", notes[0][1])

    def test_notify_exception_does_not_raise(self):
        def boom(title, body):
            raise RuntimeError("bark down")
        res = handle_af_automation_event(
            topics.SUB_AF_AUTOMATION_FIRED,
            {"trace_id": "t-4", "automation_id": "a-4", "ref": "r"},
            notify=boom,
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


if __name__ == "__main__":
    unittest.main()
