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

# D12 深度预算：现网加载面 args 可达深度实测为 2，崩溃带起点实测为 500，
# 64＝现测真值的 32 倍、崩溃带起点的 1/8，放行一切合法技能档。
_MAX_RENDER_DEPTH = 64


def _render(value, params: dict, _depth: int = 0, _path: tuple = ()):
    """递归渲染字符串里的 {{param}} 占位符。

    带深度预算与环检测（D12）：超预算或自引用/互引用模板抛 ValueError，
    ⛔ 让 RecursionError 逃逸。环判定沿「当前路径上的祖先容器」比身份，
    ⇒ 同一对象作为兄弟字段出现（共享子树/DAG）不算环，照常渲染。
    """
    if isinstance(value, str):
        return _PARAM_RE.sub(lambda m: str(params.get(m.group(1), m.group(0))), value)
    if isinstance(value, (dict, list)):
        if _depth >= _MAX_RENDER_DEPTH:
            raise ValueError(f"模板嵌套超过深度预算 {_MAX_RENDER_DEPTH}")
        for ancestor in _path:
            if ancestor is value:
                raise ValueError("模板变量存在自引用/互引用环")
        child_path = _path + (value,)
        if isinstance(value, dict):
            return {k: _render(v, params, _depth + 1, child_path) for k, v in value.items()}
        return [_render(v, params, _depth + 1, child_path) for v in value]
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

        # D12：先渲染再发令——success_text 是承诺的口播，炸了⛔ 算成功；
        # 任一步 args 没渲染出来⇒一条工具都不许发。_render 的 ValueError 必须被
        # 捕获转成失败结果，⛔ 穿出 run() 被兜网当『技能匹配失败』吞掉。
        success_tpl = brain.get("success_text") or ""
        try:
            success_text = _render(success_tpl, params) if success_tpl else ""
        except (ValueError, RecursionError) as e:
            logger.warning("tool_sequence success_text 渲染失败: %s", e)
            return SkillResult(
                ok=False, error=f"success_text 渲染失败: {e}",
                status="error", meta={"stage": "success_text", "results": results},
            )

        rendered_steps = []
        for i, step in enumerate(steps):
            tool = step.get("tool", "")
            raw_args = step.get("args") or {}
            try:
                args = _render(raw_args, params)
            except (ValueError, RecursionError) as e:
                logger.warning("tool_sequence step %d (%s) args 渲染失败: %s", i + 1, tool, e)
                return SkillResult(
                    ok=False, error=f"步骤{i+1}({tool})参数渲染失败: {e}",
                    status="error", meta={"failed_step": i, "tool": tool, "stage": "args", "results": results},
                )
            rendered_steps.append((tool, args))

        for i, (tool, args) in enumerate(rendered_steps):
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

        # 成功：口播已在发令前渲染完毕（success_text 缺省时给计数文案）
        text = success_text or f"已执行 {len(steps)} 步操作。"

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
