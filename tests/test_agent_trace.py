"""AgentTracer 测试：轨迹记录、落库、查询。"""
import os
import tempfile
import time
import unittest

# 必须在 import butler 之前设置 DATA_DIR
_TMP = tempfile.mkdtemp(prefix="butler_test_trace_")
os.environ["DATA_DIR"] = _TMP
os.environ["TTS_DIR"] = _TMP

from butler.agent_trace import AgentTracer
from butler.store.db import get_conn, close


class TestAgentTracer(unittest.TestCase):
    def setUp(self):
        close()  # 确保每个测试用干净连接

    def tearDown(self):
        close()

    def test_create_and_record_steps(self):
        t = AgentTracer(user_text="测试", member="lidicn")
        self.assertEqual(t.status, "running")
        self.assertTrue(len(t.trace_id) == 16)

        t.thought("我需要换台")
        t.action("switch_channel", {"channel": "湖南卫视"})
        t.observation("switch_channel", "已切换到湖南卫视", "", 120)
        t.final("好的，已换到湖南卫视")

        self.assertEqual(len(t.steps), 4)
        self.assertEqual(t.steps[0].role, "thought")
        self.assertEqual(t.steps[1].role, "action")
        self.assertEqual(t.steps[1].tool, "switch_channel")
        self.assertEqual(t.steps[2].role, "observation")
        self.assertEqual(t.steps[3].role, "final")

    def test_finish_persists_to_db(self):
        t = AgentTracer(user_text="换台测试", member="lidicn", source="test")
        t.thought("换台")
        t.action("switch_channel", {"channel": "CCTV1"})
        t.observation("switch_channel", "ok")
        t.final("done")
        t.tool_calls = 1
        t.llm_calls = 2
        t.finish("ok")

        row = AgentTracer.get_trace(t.trace_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["user_text"], "换台测试")
        self.assertEqual(row["member"], "lidicn")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["tool_calls"], 1)
        self.assertEqual(row["llm_calls"], 2)
        self.assertEqual(row["step_count"], 4)
        self.assertEqual(len(row["steps"]), 4)

    def test_list_traces(self):
        for i in range(3):
            t = AgentTracer(user_text=f"测试{i}", member="lidicn", source="test")
            t.finish("ok")
            time.sleep(0.01)

        rows = AgentTracer.list_traces(limit=10, member="lidicn")
        self.assertGreaterEqual(len(rows), 3)
        # 按 ts 降序
        self.assertGreaterEqual(rows[0]["ts"], rows[-1]["ts"])

    def test_escape_recorded(self):
        t = AgentTracer(user_text="逃生测试", member="lidicn")
        t.escape("超时")
        t.finish("timeout", "超时")

        row = AgentTracer.get_trace(t.trace_id)
        self.assertEqual(row["status"], "timeout")
        self.assertEqual(row["error"], "超时")
        self.assertEqual(row["steps"][-1]["role"], "escape")

    def test_error_finish(self):
        t = AgentTracer(user_text="异常测试", member="lidicn")
        t.finish("error", "connection refused")

        row = AgentTracer.get_trace(t.trace_id)
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error"], "connection refused")


if __name__ == "__main__":
    unittest.main()
