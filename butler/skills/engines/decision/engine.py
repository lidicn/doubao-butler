"""决策层技能引擎壳：把 heartbeat trigger 的 action 转成 DecisionEngine.tick。

不自己拼输出文本——决策执行（speak/notify/reminder/suggest_automation）由
decision.action_router 内部完成，因此返回 meta.suppress_output=True 让 runner
跳过自动分发（否则 butler 主角色会再播报一遍）。
"""
from __future__ import annotations

import json

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.decision")


class DecisionSkillEngine:
    async def run(self, ctx: SkillContext) -> SkillResult:
        rt = ctx.rt
        if rt is None:
            return SkillResult(ok=False, error="runtime not ready")
        engine = getattr(rt, "decision", None)
        if engine is None:
            return SkillResult(ok=False, error="decision engine not ready")

        # payload 原样透传（可带 mock 供测试）
        payload = dict(ctx.payload or {})
        try:
            res = await engine.tick(payload)
        except Exception as e:
            logger.exception("decision tick failed")
            return SkillResult(ok=False, error=f"decision tick failed: {e}")

        action = res.get("action", "no_action")
        text = res.get("text", "") or ""
        if res.get("ok"):
            summary_text = ""
            if action == "no_action":
                summary_text = "一切正常，无需打扰。"
            elif res.get("result"):
                summary_text = str(res["result"])
            elif text:
                summary_text = text
            return SkillResult(
                ok=True,
                text=summary_text,
                meta={"suppress_output": True, "engine": "decision", "decision": res},
            )
        # 被过滤/失败：不给口播（避免频繁打扰），只记录
        return SkillResult(
            ok=True,
            text="",
            meta={"suppress_output": True, "engine": "decision", "decision": res},
        )
