"""LLM 文本生成引擎：brain.prompt → LLM → 简短口播文本。

用于 daily_dev_summary / today_schedule / news_brief 等需要 LLM 生成内容的技能。
brain 配置：
  prompt:      用户提示词（支持 {{member}}/{{room}}/{{time}} 占位符）
  system:      可选 LLM 系统提示词（空则用角色 system 或默认）
  max_chars:   输出上限
  temperature: 采样温度
"""
from __future__ import annotations

from datetime import datetime

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.llm_text")


class LLMTextEngine:
    async def run(self, ctx: SkillContext) -> SkillResult:
        brain = ctx.skill.get("brain") or {}
        rt = ctx.rt
        if rt is None or getattr(rt, "llm", None) is None:
            return SkillResult(ok=False, error="runtime llm not ready")

        prompt_tpl = brain.get("prompt") or "请简短说一句关于家庭的话。"
        max_chars = int(brain.get("max_chars") or 120)
        temperature = float(brain.get("temperature") or 0.9)

        # 模板渲染
        payload = ctx.payload or {}
        now = datetime.now()
        fmt = {
            "member": str(payload.get("member") or "家人"),
            "room": str(payload.get("room") or ""),
            "time": now.strftime("%H:%M"),
            "date": now.strftime("%m月%d日"),
            "weekday": "一二三四五六日"[now.weekday()],
            "channel": str(payload.get("channel") or ""),
        }
        prompt = prompt_tpl
        for k, v in fmt.items():
            prompt = prompt.replace("{{" + k + "}}", v)

        system = (brain.get("system") or "").strip()
        if not system and getattr(ctx, "role", None) and getattr(ctx.role, "system", None):
            system = ctx.role.system

        try:
            # 注意：deepseek 系推理模型的 token 预算会被 reasoning_content 占用，
            # max_tokens 过小会导致正式 content 被截断为空。此处给足预算。
            text, _ = await rt.llm.chat(
                system or "你是家庭管家，说话自然简短。",
                [{"role": "user", "content": prompt}],
                max_tokens=1024, temperature=temperature,
            )
        except Exception as e:
            logger.warning("llm_text failed: %s", e)
            return SkillResult(ok=False, error=str(e), status="error")

        text = (text or "").strip()
        if max_chars and len(text) > max_chars:
            text = text[:max_chars]
        if not text:
            return SkillResult(ok=False, error="empty text")

        meta = {"engine": "llm_text", "member": fmt["member"], "room": fmt["room"]}
        if ctx.payload.get("_trigger_room"):
            meta["room"] = ctx.payload["_trigger_room"]
        if ctx.payload.get("_trigger_member"):
            meta["person"] = ctx.payload["_trigger_member"]
        return SkillResult(ok=True, text=text, meta=meta)

    def describe(self) -> dict:
        return {"name": "LLM 文本生成引擎", "modes": ["template"],
                "desc": "prompt → LLM → 简短口播文本（日程/总结/简报）"}
