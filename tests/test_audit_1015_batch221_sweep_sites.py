# -*- coding: utf-8 -*-
"""批221 第三档：全仓清扫的 24 枚裸铸形逐枚钉行为。

RED 判据：改前这些站点要么冒未捕获的 `ValueError`／`TypeError`（HTTP 侧冒 500），
要么把显式 `null`／`"false"` 读成与调用方本意相反的值（DB120/137/139 同族）。

落点名单由 `test_audit_1015_batch221_tree_cast_gate.py` 现读给出，本档只钉行为形状。
⚠ 自愿放弃的格（只有形状闸、没有行为格）：`api/tts_routes.py:237/238/445` 与
`api/openai_routes.py:64` 四枚 `bool(body.get(...))` 在 POST 处理链深处，
桩到能跑等于重写一遍 runtime；`decision/engine.py:118` 的 `now_hour` 要跑整条
`evaluate` 链（冷却／存储／动作路由）才能到达——这五枚由 AST 闸点名 ⇒
本档⛔ 拿源码字符串比对冒充行为验证。
"""
import asyncio
import types
import unittest
from urllib.parse import urlencode

from butler.param import ParamError


def _request(query=None, path=None, body=None):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/__probe",
        "headers": [],
        "query_string": urlencode(query or {}).encode("ascii"),
        "client": ("127.0.0.1", 1),
        "scheme": "http",
        "path_params": dict(path or {}),
    }
    return Request(scope)


class RouteParamMixin:
    def _patch(self, mod, **attrs):
        """把站点周围的守卫／外存换成桩件，只留「入参解析」这一腿在跑。"""
        self._saved = []
        for name, value in attrs.items():
            self._saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)

    def tearDown(self):
        for mod, name, value in getattr(self, "_saved", []):
            setattr(mod, name, value)
        self._saved = []

    def _status(self, resp):
        return resp.status_code


class TestCronTaskLimit(RouteParamMixin, unittest.TestCase):
    def test_negative_and_garbage_limit_raise_instead_of_passing_through(self):
        import butler.api.cron_task_routes as mod

        calls = []
        executor = types.SimpleNamespace(
            get_execution_log=lambda skill, limit: calls.append(limit) or [],
            get_failure_patterns=lambda skill, limit: calls.append(limit) or [],
        )
        self._patch(mod, guard=lambda request: None,
                    get_runtime=lambda: types.SimpleNamespace(cron_task_executor=executor))
        for handler, bad in ((mod.get_logs, "-1"), (mod.get_logs, "abc"),
                             (mod.get_failure_patterns, "-1")):
            with self.assertRaises(ParamError, msg="%s(limit=%r)" % (handler.__name__, bad)):
                asyncio.run(handler(_request({"limit": bad})))

    def test_valid_limit_reaches_the_executor(self):
        """正对照：⛔ 把「一律拒」读成修好了。"""
        import butler.api.cron_task_routes as mod

        calls = []
        executor = types.SimpleNamespace(
            get_execution_log=lambda skill, limit: calls.append(limit) or [],
            get_failure_patterns=lambda skill, limit: calls.append(limit) or [])
        self._patch(mod, guard=lambda request: None,
                    get_runtime=lambda: types.SimpleNamespace(cron_task_executor=executor))
        resp = asyncio.run(mod.get_logs(_request({"limit": "5"})))
        self.assertEqual(self._status(resp), 200)
        self.assertEqual(calls, [5])


class TestNotificationRoutes(RouteParamMixin, unittest.TestCase):
    def _mod(self):
        import butler.api.notification_routes as mod
        seen = {}

        store = types.SimpleNamespace(
            list_notifications=lambda **k: seen.update(list_kwargs=k) or [],
            get_unread_count=lambda: 0,
            mark_read=lambda nid: seen.update(read=nid),
        )
        self._patch(mod, guard=lambda request: None, notification_store=store)
        return mod, seen

    def test_list_negative_limit_raises(self):
        """DB122 同形：`LIMIT -1` 在 SQLite 里是无上限。"""
        mod, _ = self._mod()
        with self.assertRaises(ParamError):
            asyncio.run(mod.notification_list(_request({"limit": "-1"})))

    def test_list_negative_offset_raises(self):
        mod, _ = self._mod()
        with self.assertRaises(ParamError):
            asyncio.run(mod.notification_list(_request({"offset": "-1"})))

    def test_list_valid_page_reaches_the_store(self):
        mod, seen = self._mod()
        resp = asyncio.run(mod.notification_list(_request({"limit": "10", "offset": "2"})))
        self.assertEqual(self._status(resp), 200)
        self.assertEqual(seen["list_kwargs"]["limit"], 10)

    def test_read_non_numeric_id_raises_instead_of_where_id_zero(self):
        """DB138 族：改前 `int(...get("id", 0))` 把坏 id 静默变成 `WHERE id = 0`，
        查不到却回 200，与「记录不存在」同形。"""
        mod, _ = self._mod()
        with self.assertRaises(ParamError):
            asyncio.run(mod.notification_read(_request(path={"id": "abc"})))

    def test_read_zero_id_rejected(self):
        mod, _ = self._mod()
        with self.assertRaises(ParamError):
            asyncio.run(mod.notification_read(_request(path={"id": "0"})))

    def test_read_valid_id_marks(self):
        mod, seen = self._mod()
        resp = asyncio.run(mod.notification_read(_request(path={"id": "7"})))
        self.assertEqual(self._status(resp), 200)
        self.assertEqual(seen["read"], 7)


class TestDecisionAndTaskAndModes(RouteParamMixin, unittest.TestCase):
    def test_decision_list_negative_page_raises(self):
        import butler.api.decision_routes as mod
        store = types.SimpleNamespace(
            list_decisions=lambda **k: [], get_pending_count=lambda: 0)
        self._patch(mod, guard=lambda request: None, decision_store=store)
        with self.assertRaises(ParamError):
            asyncio.run(mod.decision_list(_request({"limit": "-1"})))

    def test_task_list_negative_limit_raises(self):
        import butler.api.task_routes as mod
        self._patch(mod, guard=lambda request: None,
                    task_store=types.SimpleNamespace(list_tasks=lambda **k: []))
        with self.assertRaises(ParamError):
            asyncio.run(mod.task_list(_request({"limit": "-1"})))

    def test_mode_history_negative_limit_raises(self):
        import butler.modes.api as mod
        self._patch(mod, guard=lambda request: None)
        with self.assertRaises(ParamError):
            asyncio.run(mod.mode_history(_request({"limit": "-1"})))

    def test_guard_audit_negative_limit_raises(self):
        import butler.guard.api as mod
        pg = types.SimpleNamespace(get_audit=lambda **k: [])
        self._patch(mod, guard=lambda request: None,
                    get_runtime=lambda: types.SimpleNamespace(push_guard=pg))
        with self.assertRaises(ParamError):
            asyncio.run(mod.guard_audit(_request({"limit": "-1"})))


class TestInboxValidateSoft(unittest.TestCase):
    """MQTT／内部投递载荷：⛔ 崩掉消费链，坏值记一行日志按默认走（DB139 族）。"""

    def _gate(self):
        from butler.bus.inbox import InboxGate
        return InboxGate(rt=None)

    def _speak(self, payload):
        ok, reason, normalized = self._gate().validate(payload, "speak")
        self.assertTrue(ok, reason)
        return normalized

    def test_false_word_for_override_quiet_reads_false(self):
        """改前 `bool(payload.get("override_quiet", False))`：`bool("false") is True`。"""
        n = self._speak({"trace_id": "t-1", "text": "你好", "override_quiet": "false"})
        self.assertFalse(n["override_quiet"], "「不要覆盖静默」被读成「要」＝深夜全屋播报")

    def test_explicit_null_for_override_quiet_reads_false(self):
        n = self._speak({"trace_id": "t-2", "text": "你好", "override_quiet": None})
        self.assertFalse(n["override_quiet"])

    def test_true_word_for_override_quiet_reads_true(self):
        """正对照：⛔ 把宽缓读成一律 False。"""
        n = self._speak({"trace_id": "t-3", "text": "你好", "override_quiet": "true"})
        self.assertTrue(n["override_quiet"])

    def test_garbage_priority_falls_back_to_three(self):
        n = self._speak({"trace_id": "t-4", "text": "你好", "priority": "high"})
        self.assertEqual(n["priority"], 3)

    def test_priority_clamped_to_one_and_five(self):
        self.assertEqual(self._speak({"trace_id": "t-5", "text": "x", "priority": -99})["priority"], 1)
        self.assertEqual(self._speak({"trace_id": "t-6", "text": "x", "priority": 99})["priority"], 5)


class TestDeviceEnabledField(unittest.TestCase):
    """DB137 同族在 `devices.py`：`bool(d.get("enabled", True))` 对显式 `null` 得 False。"""

    def _enabled(self, mapping):
        from butler.devices import Device
        d = {"id": "tv1", "type": "tv"}
        d.update(mapping)
        return Device.from_dict(d).enabled

    def test_explicit_null_keeps_the_default_true(self):
        self.assertTrue(self._enabled({"enabled": None}))

    def test_missing_key_keeps_the_default_true(self):
        self.assertTrue(self._enabled({}))

    def test_false_word_reads_false(self):
        self.assertFalse(self._enabled({"enabled": "false"}))

    def test_true_word_reads_true(self):
        self.assertTrue(self._enabled({"enabled": "true"}))


class TestDialogFaceConfidence(unittest.TestCase):
    """DB109/141 族在 `core/dialog.py`：外部 face 事件的 `confidence` 非数字崩整条链路。"""

    def _run(self, payload):
        from butler.core.dialog import DialogManager
        seen = []

        async def on_face(name, confidence):
            seen.append((name, confidence))

        asyncio.run(DialogManager.on_event(types.SimpleNamespace(on_face=on_face),
                                           "home/face", payload))
        return seen

    def test_non_numeric_confidence_does_not_raise(self):
        self.assertEqual(self._run({"name": "mom", "confidence": "abc"}), [("mom", 0.0)])

    def test_numeric_confidence_still_parsed(self):
        """正对照：⛔ 宽缓成「一律 0」。"""
        self.assertEqual(self._run({"name": "mom", "confidence": "0.87"}), [("mom", 0.87)])


if __name__ == "__main__":
    unittest.main()
