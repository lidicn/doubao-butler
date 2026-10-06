"""静态文本引擎：不需要 LLM，直接生成文本。

模式：
- greeting（默认）：从 ctx.payload 取 member，调用 rt.persona.greeting(member)
- template：直接返回 brain.prompt（支持 {{member}} 占位符）

用于 greet 等简单技能，避免不必要的 LLM 调用。
"""
from __future__ import annotations

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.static_text")


class StaticTextEngine:
    async def run(self, ctx: SkillContext) -> SkillResult:
        brain = ctx.skill.get("brain") or {}
        mode = brain.get("mode", "greeting")
        member = str((ctx.payload or {}).get("member") or "家人")

        rt = ctx.rt
        if mode == "greeting":
            if rt is None or getattr(rt, "persona", None) is None:
                return SkillResult(ok=False, error="runtime persona not ready")
            try:
                text = rt.persona.greeting(member)
            except Exception as e:
                logger.warning("static_text greeting failed: %s", e)
                text = f"{member}回来啦"
        else:
            # template 模式
            tpl = brain.get("prompt") or "你好，{{member}}。"
            text = tpl.replace("{{member}}", member)

        text = (text or "").strip()
        if not text:
            return SkillResult(ok=False, error="empty text")

        # v0.3: 透传 trigger 事件房间/成员 → 输出分发按所在房间过滤设备
        meta = {"engine": "static_text", "mode": mode, "member": member}
        if ctx.payload.get("_trigger_room"):
            meta["room"] = ctx.payload["_trigger_room"]
        if ctx.payload.get("_trigger_member"):
            meta["person"] = ctx.payload["_trigger_member"]
        return SkillResult(ok=True, text=text, meta=meta)

    def describe(self) -> dict:
        return {"name": "静态文本引擎", "modes": ["greeting", "template"],
                "desc": "persona.greeting 或模板文本，不调 LLM"}
