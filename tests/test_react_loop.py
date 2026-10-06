"""ReAct 循环测试：预算/逃生/tracer 集成/错误码解析/观察器。"""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="butler_test_react_")
os.environ["DATA_DIR"] = _TMP
os.environ["TTS_DIR"] = _TMP

from butler.agent_trace import AgentTracer
from butler.config import Settings
from butler.integrations.llm import LLMClient, _extract_error_code


def _settings() -> Settings:
    s = Settings()
    s.new_api_url = "http://test/v1"
    s.new_api_key = "k"
    s.new_api_model = "m"
    s.llm_retry_max = 1
    s.llm_retry_backoff = 0.01
    return s


def _llm_message(content: str = "", tool_calls: list | None = None) -> dict:
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


def _tool_call(name: str, args: dict, call_id: str = "tc_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": __import__("json").dumps(args)},
    }


class TestExtractErrorCode(unittest.TestCase):
    def test_dict_with_error_code(self):
        self.assertEqual(_extract_error_code({"error_code": "operation_timeout"}), "operation_timeout")

    def test_dict_ok_false(self):
        self.assertEqual(_extract_error_code({"ok": False, "error": "tv_unreachable"}), "tv_unreachable")

    def test_dict_ok_true(self):
        self.assertEqual(_extract_error_code({"ok": True, "result": "done"}), "")

    def test_json_string_with_error(self):
        import json
        s = json.dumps({"ok": False, "error_code": "invalid_parameter"})
        self.assertEqual(_extract_error_code(s), "invalid_parameter")

    def test_plain_string_match(self):
        self.assertEqual(_extract_error_code("换台失败：operation_timeout"), "operation_timeout")

    def test_no_error(self):
        self.assertEqual(_extract_error_code("已切换到湖南卫视"), "")

    def test_non_string_non_dict(self):
        self.assertEqual(_extract_error_code(123), "")


class TestReactLoop(unittest.TestCase):
    def setUp(self):
        self.llm = LLMClient(_settings())

    def test_no_tool_calls_returns_content(self):
        """LLM 直接回复（无 tool_calls）→ 返回 content，tracer 记录 final。"""
        tracer = AgentTracer(user_text="你好", member="test")
        async def noop(n, a):
            return ""
        with mock.patch.object(self.llm, "_raw", return_value=_llm_message(content="你好！")):
            result = asyncio.run(self.llm.chat_with_tools(
                "sys", [{"role": "user", "content": "你好"}], [],
                noop, tracer=tracer, tool_sleep=0,
            ))
        self.assertEqual(result, "你好！")
        self.assertEqual(tracer.status, "ok")
        self.assertTrue(any(s.role == "final" for s in tracer.steps))

    def test_tool_call_then_final(self):
        """一轮工具调用后 LLM 给出最终回复。"""
        tracer = AgentTracer(user_text="换台", member="test")
        calls = [
            _llm_message(tool_calls=[_tool_call("switch_channel", {"channel": "CCTV1"})]),
            _llm_message(content="好的，已换到CCTV1"),
        ]
        async def executor(n, a):
            return "已切换"
        with mock.patch.object(self.llm, "_raw", side_effect=calls):
            result = asyncio.run(self.llm.chat_with_tools(
                "sys", [{"role": "user", "content": "换到CCTV1"}], [],
                executor, tracer=tracer, tool_sleep=0,
            ))
        self.assertEqual(result, "好的，已换到CCTV1")
        self.assertEqual(tracer.tool_calls, 1)
        self.assertTrue(any(s.role == "action" and s.tool == "switch_channel" for s in tracer.steps))
        self.assertTrue(any(s.role == "observation" for s in tracer.steps))

    def test_max_iter_triggers_escape(self):
        """超出步数上限 → escape_fn 被调用，tracer status=max_iter。"""
        tracer = AgentTracer(user_text="死循环", member="test")
        escaped = []

        def escape_fn(reason):
            escaped.append(reason)
            return "已停止"

        # 每轮都返回 tool_calls，永不终止
        def always_tools(*a, **kw):
            return _llm_message(tool_calls=[_tool_call("get_current_time", {})])

        async def tick(n, a):
            return "tick"
        with mock.patch.object(self.llm, "_raw", side_effect=always_tools):
            result = asyncio.run(self.llm.chat_with_tools(
                "sys", [{"role": "user", "content": "x"}], [],
                tick, tracer=tracer, escape_fn=escape_fn,
                max_iter=3, tool_sleep=0,
            ))
        self.assertEqual(result, "已停止")
        self.assertEqual(tracer.status, "max_iter")
        self.assertEqual(len(escaped), 1)
        self.assertIn("3步", escaped[0])

    def test_time_budget_triggers_escape(self):
        """时间预算耗尽 → escape。"""
        tracer = AgentTracer(user_text="超时", member="test")

        def slow_tool(*a, **kw):
            import time; time.sleep(0.15)
            return _llm_message(tool_calls=[_tool_call("get_current_time", {})])

        async def tick(n, a):
            return "tick"
        with mock.patch.object(self.llm, "_raw", side_effect=slow_tool):
            result = asyncio.run(self.llm.chat_with_tools(
                "sys", [{"role": "user", "content": "x"}], [],
                tick, tracer=tracer,
                max_iter=10, time_budget=0.1, tool_sleep=0,
            ))
        self.assertEqual(tracer.status, "timeout")
        self.assertIn("超时", result)

    def test_observe_fn_called(self):
        """观察器在工具调用后被调用，结果回灌。"""
        tracer = AgentTracer(user_text="验证", member="test")
        observations = []

        def observe_fn(tool, args, result):
            observations.append((tool, result))
            return "前台验证：mytv 正在运行"

        calls = [
            _llm_message(tool_calls=[_tool_call("switch_channel", {"channel": "CCTV1"})]),
            _llm_message(content="完成"),
        ]
        async def executor(n, a):
            return "已切换"
        with mock.patch.object(self.llm, "_raw", side_effect=calls):
            asyncio.run(self.llm.chat_with_tools(
                "sys", [{"role": "user", "content": "x"}], [],
                executor, tracer=tracer, observe_fn=observe_fn, tool_sleep=0,
            ))
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0][0], "switch_channel")
        self.assertTrue(any("observe" in s.tool for s in tracer.steps))

    def test_error_code_recorded_in_trace(self):
        """工具返回结构化错误 → observation 记录 error_code。"""
        tracer = AgentTracer(user_text="错误", member="test")
        calls = [
            _llm_message(tool_calls=[_tool_call("switch_channel", {"channel": "XXX"})]),
            _llm_message(content="换台失败了"),
        ]
        async def executor(n, a):
            return '{"ok": false, "error_code": "tv_unreachable"}'
        with mock.patch.object(self.llm, "_raw", side_effect=calls):
            asyncio.run(self.llm.chat_with_tools(
                "sys", [{"role": "user", "content": "x"}], [],
                executor,
                tracer=tracer, tool_sleep=0,
            ))
        obs_steps = [s for s in tracer.steps if s.role == "observation"]
        self.assertTrue(any(s.error_code == "tv_unreachable" for s in obs_steps))

    def test_no_tracer_no_crash(self):
        """不传 tracer 时正常工作（向后兼容）。"""
        async def noop(n, a):
            return ""
        with mock.patch.object(self.llm, "_raw", return_value=_llm_message(content="hi")):
            result = asyncio.run(self.llm.chat_with_tools(
                "sys", [{"role": "user", "content": "x"}], [],
                noop, tool_sleep=0,
            ))
        self.assertEqual(result, "hi")


if __name__ == "__main__":
    unittest.main()
