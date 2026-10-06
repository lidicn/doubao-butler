"""LLM 决策引擎：触发器 → LLM Agent（工具白名单）→ 自然对话。

brain 配置：
  tools_whitelist:  允许 LLM 调用的工具名列表（白名单外工具根本不传给 LLM）
  system:           系统提示词（支持 {{member}}/{{room}}/{{time}} 占位符）
  prompt:           用户提示词（当前场景描述）
  max_chars:        最终口播文本上限（默认 80）
  temperature:      采样温度（默认 0.7，比 llm_text 低一些，保持稳重）

安全：
  - 只把 tools_whitelist 中的工具 schema 传给 LLM，其他工具物理不可见
  - 工具执行走 dispatch_tool，参数由 LLM 生成但有现成工具逻辑保护
"""
from __future__ import annotations

import time
from datetime import datetime

from homesdk.time import house_now
from butler.core.tools import TOOL_SCHEMAS, dispatch_tool
from butler.logging_setup import get_logger
from butler.skills.engines.llm_decide.ask import pending_ask_manager
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.llm_decide")


class LLMDecideEngine:
    async def run(self, ctx: SkillContext) -> SkillResult:
        brain = ctx.skill.get("brain") or {}
        rt = ctx.rt
        if rt is None:
            return SkillResult(ok=False, error="runtime not ready")

        llm = getattr(rt, "llm", None)
        if llm is None:
            return SkillResult(ok=False, error="runtime llm not ready")

        agent = getattr(rt, "agent", None)
        if agent is None:
            return SkillResult(ok=False, error="runtime agent not ready")

        # ── 工具白名单：物理过滤，只传允许的工具 ──
        whitelist = set(brain.get("tools_whitelist") or [])
        if not whitelist:
            return SkillResult(ok=False, error="tools_whitelist is empty, refuse to run")

        filtered_schemas = [
            s for s in TOOL_SCHEMAS
            if s.get("function", {}).get("name") in whitelist
        ]
        if not filtered_schemas:
            return SkillResult(ok=False, error=f"no tools matched whitelist: {whitelist}")

        logger.info("llm_decide tools: %s", [s["function"]["name"] for s in filtered_schemas])

        # ── 确定性代码预查状态（不经过 LLM，避免 ReAct 循环）──
        context_facts = []
        for tool_name in whitelist:
            if tool_name in ("tv_foreground", "get_weather", "get_current_time"):
                try:
                    result = await dispatch_tool(tool_name, {}, agent)
                    # 把工具结果转成 LLM 能读懂的自然语言
                    if tool_name == "tv_foreground":
                        try:
                            import json as _json
                            _data = _json.loads(result) if isinstance(result, str) else result
                            _pkg = (_data.get("result") or {}).get("package") or _data.get("package") or ""
                            if _pkg:
                                context_facts.append(f"电视当前是开着的，前台应用包名：{_pkg}")
                            else:
                                context_facts.append("电视当前是关闭的")
                        except Exception:
                            context_facts.append(f"电视状态：{result}")
                    elif tool_name == "get_weather":
                        context_facts.append(f"天气：{result}")
                    elif tool_name == "get_current_time":
                        context_facts.append(f"时间：{result}")
                    logger.info("llm_decide pre-check %s -> %.60s", tool_name, result)
                except Exception as e:
                    logger.warning("llm_decide pre-check %s failed: %s", tool_name, e)

        # ── 上下文模板渲染 ──
        payload = ctx.payload or {}
        now = house_now()
        weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        fmt = {
            "member": str(payload.get("member") or payload.get("_trigger_member") or "家人"),
            "room": str(payload.get("room") or payload.get("_trigger_room") or ""),
            "time": now.strftime("%H:%M"),
            "date": now.strftime("%m月%d日"),
            "weekday": weekday_cn,
        }

        system_tpl = brain.get("system") or "你是家庭管家，看到主人早上出现，自然问候。"
        prompt_tpl = brain.get("prompt") or "现在是早上，主人刚出现在客厅。"

        def _render(s: str) -> str:
            for k, v in fmt.items():
                s = s.replace("{{" + k + "}}", v)
            return s

        system = _render(system_tpl)
        prompt = _render(prompt_tpl)

        # 注入当前时间
        system += f"\n\n【当前时间】{now.strftime('%Y年%m月%d日')} {weekday_cn} {now.strftime('%H:%M')}（Asia/Shanghai）。"

        # ── 工具执行器 ──
        async def executor(name: str, args: dict) -> str:
            # 二次校验：白名单外的工具直接拒绝
            if name not in whitelist:
                logger.warning("llm_decide blocked non-whitelist tool: %s", name)
                return f"工具 {name} 不在白名单中，不可用。"
            try:
                result = await dispatch_tool(name, args, agent)
                logger.info("llm_decide tool_call: %s(%s) -> %.80s", name, args, result)
                return str(result)
            except Exception as e:
                logger.warning("llm_decide tool_call %s failed: %s", name, e)
                return f"工具调用失败: {e}"

        # ── 调 LLM ──
        max_chars = int(brain.get("max_chars") or 80)
        temperature = float(brain.get("temperature") or 0.7)

        # 注入预查事实到 prompt
        if context_facts:
            prompt += "\n\n【当前事实】\n" + "\n".join(context_facts)

        messages = [{"role": "user", "content": prompt}]

        try:
            # 第一轮纯文本生成问句，不带工具（避免 ReAct 循环）
            text, _ = await llm.chat(
                system, messages,
                temperature=temperature,
                max_tokens=200,
            )
        except Exception as e:
            logger.warning("llm_decide failed: %s", e)
            return SkillResult(ok=False, error=str(e), status="error")

        text = (text or "").strip()
        if not text:
            return SkillResult(ok=False, error="empty response", status="error")

        # 截断过长输出
        if len(text) > max_chars:
            text = text[:max_chars]

        # ── Ask 挂起：问完后等用户语音回复 ──
        ask_wait = float(brain.get("ask_wait_seconds") or 0)
        room = str(payload.get("room") or payload.get("_trigger_room") or "客厅")
        # 只有生成的文本是问句才挂起 ask（电视已开着就不问了）
        is_question = "?" in text or "？" in text
        if ask_wait > 0 and is_question:
            # LLM 输出是问题，注册 pending ask
            await pending_ask_manager.arm(
                room=room, question=text, brain=brain, rt=rt, timeout=ask_wait,
            )
            logger.info("llm_decide armed ask (%.0fs): %.40s", ask_wait, text)

        meta = {
            "engine": "llm_decide",
            "tools_used": list(whitelist),
            "member": fmt["member"],
            "room": fmt["room"],
            "ask_armed": ask_wait > 0,
        }
        return SkillResult(ok=True, text=text, meta=meta)

    def describe(self) -> dict:
        return {
            "name": "LLM 决策引擎",
            "modes": ["agent"],
            "desc": "触发器→LLM Agent（工具白名单）→自然对话。LLM 自主判断说什么、是否调用受限工具。",
        }
