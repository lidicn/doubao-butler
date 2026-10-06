"""工具序列引擎：按预定义步骤依次调用 Agent 工具，支持 {{param}} 占位符参数化。

这是 M3「ReAct 轨迹沉淀为技能」的执行引擎。成功的多步 ReAct 序列（如
"启动飞牛TV → 搜索 → 输入 → 播放"）被提取为静态步骤序列，命中时直接参数化执行，
不再逐步 LLM 规划，省 token 且更稳定。失败时回退 ReAct（由 Agent.run 处理）。

技能 brain 配置：
  engine: "tool_sequence"
  steps: [
    {"tool": "tv_search_play", "args": {"keyword": "{{keyword}}"}},
    {"tool": "tv_foreground", "args": {}}
  ]
  intent_keywords: ["飞牛TV", "搜索播放", "在电视上放"]   # 可选，用于 Agent 匹配
  success_text: "已在飞牛TV上播放《{{keyword}}》"           # 可选，成功后的口播

ctx.payload 提供参数值（如 {"keyword": "流浪地球"}），替换步骤里的 {{keyword}}。
"""
from __future__ import annotations

import re

from butler.core.tools import dispatch_tool
from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.tool_sequence")

_PARAM_RE = re.compile(r"\{\{(\w+)\}\}")


def _render(value, params: dict):
    """递归渲染字符串里的 {{param}} 占位符。"""
    if isinstance(value, str):
        return _PARAM_RE.sub(lambda m: str(params.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: _render(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, params) for v in value]
    return value


class ToolSequenceEngine:
    """按 steps 依次调用 Agent 工具。"""

    async def run(self, ctx: SkillContext) -> SkillResult:
        brain = ctx.skill.get("brain") or {}
        steps = brain.get("steps") or []
        if not steps:
            return SkillResult(ok=False, error="tool_sequence: steps 为空", status="error")

        rt = ctx.rt
        if rt is None or getattr(rt, "agent", None) is None:
            return SkillResult(ok=False, error="runtime agent not ready", status="error")

        params = dict(ctx.payload or {})
        results = []
        for i, step in enumerate(steps):
            tool = step.get("tool", "")
            raw_args = step.get("args") or {}
            args = _render(raw_args, params)
            logger.info("tool_sequence step %d: %s(%s)", i + 1, tool, str(args)[:120])
            try:
                result = await dispatch_tool(tool, args, rt.agent)
            except Exception as e:
                logger.warning("tool_sequence step %d (%s) failed: %s", i + 1, tool, e)
                return SkillResult(
                    ok=False, error=f"步骤{i+1}({tool})失败: {e}",
                    status="error", meta={"failed_step": i, "tool": tool, "results": results},
                )
            # 工具返回结构化 JSON 时检查 ok
            ok, err = _check_result(result)
            if not ok:
                logger.warning("tool_sequence step %d (%s) returned error: %s", i + 1, tool, err)
                return SkillResult(
                    ok=False, error=f"步骤{i+1}({tool})返回错误: {err}",
                    status="error", meta={"failed_step": i, "tool": tool, "results": results},
                )
            results.append({"step": i + 1, "tool": tool, "result": str(result)[:300]})

        # 成功：渲染 success_text
        text = ""
        success_tpl = brain.get("success_text") or ""
        if success_tpl:
            text = _render(success_tpl, params)
        else:
            text = f"已执行 {len(steps)} 步操作。"

        return SkillResult(
            ok=True, text=text, status="ok",
            meta={"engine": "tool_sequence", "steps_executed": len(steps), "results": results},
        )

    def describe(self) -> dict:
        return {
            "name": "工具序列（ReAct 沉淀）",
            "modes": [],
            "desc": "按预定义步骤依次调用 Agent 工具，支持 {{param}} 参数化。用于把成功的多步 ReAct 序列沉淀为可复用技能。",
        }


def _check_result(result: str) -> tuple[bool, str]:
    """检查工具返回值是否表示成功。返回 (ok, error)。"""
    if not result:
        return False, "空结果"
    s = str(result).strip()
    if s.startswith("{"):
        try:
            import json
            obj = json.loads(s)
            if isinstance(obj, dict):
                if obj.get("ok") is False:
                    return False, str(obj.get("error") or obj.get("message") or "unknown")
                return True, ""
        except Exception:
            pass
    # 非 JSON 结果：包含"失败"/"错误"关键词视为失败
    if any(kw in s for kw in ("失败", "错误", "超时", "不可达", "未找到")):
        return False, s[:100]
    return True, ""
