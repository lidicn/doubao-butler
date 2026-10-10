"""M3 操作模板技能测试：tool_sequence 引擎 + 轨迹转草稿 + 意图匹配 + Agent 命中。

批187 形制收编：本档原为 pytest 形制（`class Test*` 不继承 `unittest.TestCase`＋裸 `assert`），
`python -m unittest` 收 0 格⇒ 全量回归里一直是 `rc=5 ran=0`（一次都没跑过）。
现改为 `unittest.TestCase`，七族 28 格 1:1 保留，⛔ 借改形制删格；复合断言拆成两句只是让失败
消息指得到是哪半条，判据内容不变。地面真值见 `workorders/readings/1011/b187_pytest_ground_1011.txt`
（22:14:36Z，本档 28 格全绿）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from butler.agent_skill import (  # noqa: E402
    match_dialog_skill, extract_params_from_text, trace_to_skill_draft,
    _parameterize, _infer_keywords,
)
from butler.skills.engines.tool_sequence.engine import ToolSequenceEngine, _render, _check_result  # noqa: E402
from butler.skills.runner_types import SkillContext, SkillResult  # noqa: E402

DISPATCH_TARGET = "butler.skills.engines.tool_sequence.engine.dispatch_tool"


# ── _render 占位符渲染 ──

class TestRender(unittest.TestCase):
    def test_simple_param(self):
        self.assertEqual(_render("{{keyword}}", {"keyword": "流浪地球"}), "流浪地球")

    def test_no_param(self):
        self.assertEqual(_render("hello", {}), "hello")

    def test_missing_param_keeps_placeholder(self):
        self.assertEqual(_render("{{missing}}", {}), "{{missing}}")

    def test_dict_render(self):
        self.assertEqual(_render({"a": "{{x}}", "b": 1}, {"x": "v"}), {"a": "v", "b": 1})

    def test_list_render(self):
        self.assertEqual(_render(["{{x}}", 2], {"x": "v"}), ["v", 2])


# ── _check_result ──

class TestCheckResult(unittest.TestCase):
    def test_ok_json(self):
        ok, err = _check_result(json.dumps({"ok": True, "result": "done"}))
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_error_json(self):
        ok, err = _check_result(json.dumps({"ok": False, "error": "invalid_parameter"}))
        self.assertFalse(ok)
        self.assertIn("invalid_parameter", err)

    def test_plain_text_ok(self):
        ok, _ = _check_result("操作完成")
        self.assertTrue(ok)

    def test_plain_text_error_keyword(self):
        ok, _ = _check_result("连接超时，不可达")
        self.assertFalse(ok)

    def test_empty(self):
        ok, _ = _check_result("")
        self.assertFalse(ok)


# ── ToolSequenceEngine ──

class FakeAgent:
    """模拟 Agent，记录 dispatch_tool 调用。"""
    def __init__(self):
        self.calls = []


class FakeRT:
    def __init__(self, agent):
        self.agent = agent


def _make_ctx(skill, payload=None, agent=None):
    return SkillContext(
        skill=skill, source="test", payload=payload or {},
        rt=FakeRT(agent) if agent else None,
    )


def _patch_dispatch(results_map=None, fail_tool=None):
    """返回一个 mock dispatch_tool，按工具名返回预设结果。"""
    results_map = results_map or {}

    async def _fake_dispatch(tool, args, agent):
        if hasattr(agent, "calls"):
            agent.calls.append((tool, args))
        if fail_tool and tool == fail_tool:
            return json.dumps({"ok": False, "error": "simulated_failure"})
        return results_map.get(tool, json.dumps({"ok": True, "result": "done"}))
    return _fake_dispatch


class TestToolSequenceEngine(unittest.TestCase):
    def test_empty_steps(self):
        engine = ToolSequenceEngine()
        ctx = _make_ctx({"brain": {"steps": []}})
        r = asyncio.run(engine.run(ctx))
        self.assertFalse(r.ok)
        self.assertIn("steps 为空", r.error)

    def test_single_step_success(self):
        agent = FakeAgent()
        skill = {"brain": {"steps": [{"tool": "tv_go_home", "args": {}}], "success_text": "已回桌面"}}
        ctx = _make_ctx(skill, agent=agent)
        engine = ToolSequenceEngine()
        with patch(DISPATCH_TARGET, _patch_dispatch()):
            r = asyncio.run(engine.run(ctx))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.text, "已回桌面")
        self.assertEqual(len(agent.calls), 1)
        self.assertEqual(agent.calls[0][0], "tv_go_home")

    def test_param_rendering(self):
        agent = FakeAgent()
        skill = {"brain": {"steps": [
            {"tool": "tv_search_play", "args": {"keyword": "{{keyword}}"}},
        ], "success_text": "播放《{{keyword}}》"}}
        ctx = _make_ctx(skill, payload={"keyword": "流浪地球"}, agent=agent)
        engine = ToolSequenceEngine()
        with patch(DISPATCH_TARGET, _patch_dispatch()):
            r = asyncio.run(engine.run(ctx))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(agent.calls[0][1], {"keyword": "流浪地球"})
        self.assertIn("流浪地球", r.text)

    def test_step_failure_returns_error(self):
        agent = FakeAgent()
        skill = {"brain": {"steps": [
            {"tool": "tv_go_home", "args": {}},
            {"tool": "tv_foreground", "args": {}},
        ]}}
        ctx = _make_ctx(skill, agent=agent)
        engine = ToolSequenceEngine()
        with patch(DISPATCH_TARGET, _patch_dispatch(fail_tool="tv_go_home")):
            r = asyncio.run(engine.run(ctx))
        self.assertFalse(r.ok)
        self.assertIn("simulated_failure", r.error)
        self.assertEqual(r.meta.get("failed_step"), 0)
        self.assertEqual(len(agent.calls), 1)

    def test_no_runtime(self):
        skill = {"brain": {"steps": [{"tool": "x", "args": {}}]}}
        ctx = SkillContext(skill=skill, rt=None)
        engine = ToolSequenceEngine()
        r = asyncio.run(engine.run(ctx))
        self.assertFalse(r.ok)
        self.assertIn("runtime", r.error)


# ── 意图匹配 ──

def _skill(sid, keywords, engine="tool_sequence", status="enabled", approval="approved"):
    return {
        "id": sid, "name": sid, "source": "agent", "status": status,
        "approval": approval,
        "brain": {"engine": engine, "intent_keywords": keywords, "steps": [{"tool": "x", "args": {}}]},
    }


class TestMatchDialogSkill(unittest.TestCase):
    def test_match_keyword(self):
        skills = [_skill("s1", ["飞牛TV", "搜索播放"])]
        m = match_dialog_skill("在飞牛TV上搜索播放流浪地球", skills)
        self.assertIsNotNone(m)
        self.assertEqual(m["id"], "s1")

    def test_no_match(self):
        skills = [_skill("s1", ["飞牛TV"])]
        self.assertIsNone(match_dialog_skill("今天天气怎么样", skills))

    def test_skip_disabled(self):
        skills = [_skill("s1", ["飞牛TV"], status="disabled")]
        self.assertIsNone(match_dialog_skill("在电视上放", skills))

    def test_skip_pending_approval(self):
        skills = [_skill("s1", ["飞牛TV"], approval="pending_review")]
        self.assertIsNone(match_dialog_skill("在电视上放", skills))

    def test_skip_non_tool_sequence(self):
        skills = [_skill("s1", ["飞牛TV"], engine="llm_text")]
        self.assertIsNone(match_dialog_skill("在电视上放", skills))

    def test_best_score_wins(self):
        skills = [
            _skill("s1", ["电视"]),
            _skill("s2", ["电视", "飞牛TV", "搜索播放"]),
        ]
        m = match_dialog_skill("在飞牛TV上搜索播放流浪地球", skills)
        self.assertEqual(m["id"], "s2")


# ── 参数提取 ──

class TestExtractParams(unittest.TestCase):
    def test_keyword_after_play(self):
        skill = _skill("s1", [])
        skill["brain"]["params"] = ["keyword"]
        p = extract_params_from_text("播放流浪地球", skill)
        self.assertEqual(p.get("keyword"), "流浪地球")

    def test_keyword_after_watch(self):
        skill = _skill("s1", [])
        skill["brain"]["params"] = ["keyword"]
        p = extract_params_from_text("我想看狂飙", skill)
        self.assertEqual(p.get("keyword"), "狂飙")

    def test_no_params_in_skill(self):
        skill = _skill("s1", [])
        self.assertEqual(extract_params_from_text("播放流浪地球", skill), {})


# ── _parameterize ──

class TestParameterize(unittest.TestCase):
    def test_extract_string_param(self):
        steps = [{"tool": "tv_search_play", "args": {"keyword": "流浪地球"}}]
        out, params = _parameterize(steps)
        self.assertEqual(out[0]["args"]["keyword"], "{{keyword}}")
        self.assertIn("keyword", params)

    def test_keep_fixed_values(self):
        steps = [{"tool": "tv_launch_app", "args": {"package": "com.trim.tv"}}]
        out, params = _parameterize(steps)
        self.assertEqual(out[0]["args"]["package"], "com.trim.tv")
        self.assertEqual(params, [])


# ── _infer_keywords ──

class TestInferKeywords(unittest.TestCase):
    def test_search_play_tools(self):
        steps = [{"tool": "tv_search_play", "args": {}}]
        kws = _infer_keywords("在电视上放流浪地球", steps)
        self.assertTrue(any("飞牛TV" in k or "搜索播放" in k for k in kws), kws)

    def test_switch_channel_tools(self):
        steps = [{"tool": "switch_channel", "args": {}}]
        kws = _infer_keywords("换到湖南卫视", steps)
        self.assertTrue(any("换台" in k or "频道" in k for k in kws), kws)


if __name__ == "__main__":
    unittest.main()
