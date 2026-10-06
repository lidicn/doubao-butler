"""M3 操作模板技能测试：tool_sequence 引擎 + 轨迹转草稿 + 意图匹配 + Agent 命中。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from butler.agent_skill import (
    match_dialog_skill, extract_params_from_text, trace_to_skill_draft,
    _parameterize, _infer_keywords,
)
from butler.skills.engines.tool_sequence.engine import ToolSequenceEngine, _render, _check_result
from butler.skills.runner_types import SkillContext, SkillResult


# ── _render 占位符渲染 ──

class TestRender:
    def test_simple_param(self):
        assert _render("{{keyword}}", {"keyword": "流浪地球"}) == "流浪地球"

    def test_no_param(self):
        assert _render("hello", {}) == "hello"

    def test_missing_param_keeps_placeholder(self):
        assert _render("{{missing}}", {}) == "{{missing}}"

    def test_dict_render(self):
        assert _render({"a": "{{x}}", "b": 1}, {"x": "v"}) == {"a": "v", "b": 1}

    def test_list_render(self):
        assert _render(["{{x}}", 2], {"x": "v"}) == ["v", 2]


# ── _check_result ──

class TestCheckResult:
    def test_ok_json(self):
        ok, err = _check_result(json.dumps({"ok": True, "result": "done"}))
        assert ok and err == ""

    def test_error_json(self):
        ok, err = _check_result(json.dumps({"ok": False, "error": "invalid_parameter"}))
        assert not ok and "invalid_parameter" in err

    def test_plain_text_ok(self):
        ok, err = _check_result("操作完成")
        assert ok

    def test_plain_text_error_keyword(self):
        ok, err = _check_result("连接超时，不可达")
        assert not ok

    def test_empty(self):
        ok, _ = _check_result("")
        assert not ok


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
    from unittest.mock import AsyncMock
    results_map = results_map or {}
    async def _fake_dispatch(tool, args, agent):
        if hasattr(agent, "calls"):
            agent.calls.append((tool, args))
        if fail_tool and tool == fail_tool:
            return json.dumps({"ok": False, "error": "simulated_failure"})
        return results_map.get(tool, json.dumps({"ok": True, "result": "done"}))
    return _fake_dispatch


class TestToolSequenceEngine:
    def test_empty_steps(self):
        import asyncio
        engine = ToolSequenceEngine()
        ctx = _make_ctx({"brain": {"steps": []}})
        r = asyncio.run(engine.run(ctx))
        assert not r.ok and "steps 为空" in r.error

    def test_single_step_success(self):
        import asyncio
        from unittest.mock import patch
        agent = FakeAgent()
        skill = {"brain": {"steps": [{"tool": "tv_go_home", "args": {}}], "success_text": "已回桌面"}}
        ctx = _make_ctx(skill, agent=agent)
        engine = ToolSequenceEngine()
        with patch("butler.skills.engines.tool_sequence.engine.dispatch_tool", _patch_dispatch()):
            r = asyncio.run(engine.run(ctx))
        assert r.ok
        assert r.text == "已回桌面"
        assert len(agent.calls) == 1
        assert agent.calls[0][0] == "tv_go_home"

    def test_param_rendering(self):
        import asyncio
        from unittest.mock import patch
        agent = FakeAgent()
        skill = {"brain": {"steps": [
            {"tool": "tv_search_play", "args": {"keyword": "{{keyword}}"}},
        ], "success_text": "播放《{{keyword}}》"}}
        ctx = _make_ctx(skill, payload={"keyword": "流浪地球"}, agent=agent)
        engine = ToolSequenceEngine()
        with patch("butler.skills.engines.tool_sequence.engine.dispatch_tool", _patch_dispatch()):
            r = asyncio.run(engine.run(ctx))
        assert r.ok
        assert agent.calls[0][1] == {"keyword": "流浪地球"}
        assert "流浪地球" in r.text

    def test_step_failure_returns_error(self):
        import asyncio
        from unittest.mock import patch
        agent = FakeAgent()
        skill = {"brain": {"steps": [
            {"tool": "tv_go_home", "args": {}},
            {"tool": "tv_foreground", "args": {}},
        ]}}
        ctx = _make_ctx(skill, agent=agent)
        engine = ToolSequenceEngine()
        with patch("butler.skills.engines.tool_sequence.engine.dispatch_tool",
                    _patch_dispatch(fail_tool="tv_go_home")):
            r = asyncio.run(engine.run(ctx))
        assert not r.ok
        assert "simulated_failure" in r.error
        assert r.meta.get("failed_step") == 0
        assert len(agent.calls) == 1

    def test_no_runtime(self):
        import asyncio
        skill = {"brain": {"steps": [{"tool": "x", "args": {}}]}}
        ctx = SkillContext(skill=skill, rt=None)
        engine = ToolSequenceEngine()
        r = asyncio.run(engine.run(ctx))
        assert not r.ok and "runtime" in r.error


# ── 意图匹配 ──

def _skill(sid, keywords, engine="tool_sequence", status="enabled", approval="approved"):
    return {
        "id": sid, "name": sid, "source": "agent", "status": status,
        "approval": approval,
        "brain": {"engine": engine, "intent_keywords": keywords, "steps": [{"tool": "x", "args": {}}]},
    }


class TestMatchDialogSkill:
    def test_match_keyword(self):
        skills = [_skill("s1", ["飞牛TV", "搜索播放"])]
        m = match_dialog_skill("在飞牛TV上搜索播放流浪地球", skills)
        assert m is not None and m["id"] == "s1"

    def test_no_match(self):
        skills = [_skill("s1", ["飞牛TV"])]
        assert match_dialog_skill("今天天气怎么样", skills) is None

    def test_skip_disabled(self):
        skills = [_skill("s1", ["飞牛TV"], status="disabled")]
        assert match_dialog_skill("在电视上放", skills) is None

    def test_skip_pending_approval(self):
        skills = [_skill("s1", ["飞牛TV"], approval="pending_review")]
        assert match_dialog_skill("在电视上放", skills) is None

    def test_skip_non_tool_sequence(self):
        skills = [_skill("s1", ["飞牛TV"], engine="llm_text")]
        assert match_dialog_skill("在电视上放", skills) is None

    def test_best_score_wins(self):
        skills = [
            _skill("s1", ["电视"]),
            _skill("s2", ["电视", "飞牛TV", "搜索播放"]),
        ]
        m = match_dialog_skill("在飞牛TV上搜索播放流浪地球", skills)
        assert m["id"] == "s2"


# ── 参数提取 ──

class TestExtractParams:
    def test_keyword_after_play(self):
        skill = _skill("s1", [], )
        skill["brain"]["params"] = ["keyword"]
        p = extract_params_from_text("播放流浪地球", skill)
        assert p.get("keyword") == "流浪地球"

    def test_keyword_after_watch(self):
        skill = _skill("s1", [])
        skill["brain"]["params"] = ["keyword"]
        p = extract_params_from_text("我想看狂飙", skill)
        assert p.get("keyword") == "狂飙"

    def test_no_params_in_skill(self):
        skill = _skill("s1", [])
        p = extract_params_from_text("播放流浪地球", skill)
        assert p == {}


# ── _parameterize ──

class TestParameterize:
    def test_extract_string_param(self):
        steps = [{"tool": "tv_search_play", "args": {"keyword": "流浪地球"}}]
        out, params = _parameterize(steps)
        assert out[0]["args"]["keyword"] == "{{keyword}}"
        assert "keyword" in params

    def test_keep_fixed_values(self):
        steps = [{"tool": "tv_launch_app", "args": {"package": "com.trim.tv"}}]
        out, params = _parameterize(steps)
        assert out[0]["args"]["package"] == "com.trim.tv"
        assert params == []


# ── _infer_keywords ──

class TestInferKeywords:
    def test_search_play_tools(self):
        steps = [{"tool": "tv_search_play", "args": {}}]
        kws = _infer_keywords("在电视上放流浪地球", steps)
        assert any("飞牛TV" in k or "搜索播放" in k for k in kws)

    def test_switch_channel_tools(self):
        steps = [{"tool": "switch_channel", "args": {}}]
        kws = _infer_keywords("换到湖南卫视", steps)
        assert any("换台" in k or "频道" in k for k in kws)
