"""智动任务引擎：把 Koin Action 执行逻辑接入技能系统。

技能 JSON 中 brain 配置：
{
  "engine": "cron_task",
  "api_id": "caiyunweather",
  "condition": {"type": "hour_local", "hours": [12,18,20], "threshold": 50},
  "message_template": "今天可能有雨，记得带伞"
}

输出由 cron_task_executor 内部处理（TTS/Bark/TV），
引擎返回空文本避免 runner 重复推送。
"""
from __future__ import annotations

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.cron_task")


class CronTaskEngine:
    async def run(self, ctx: SkillContext) -> SkillResult:
        rt = ctx.rt
        if rt is None or getattr(rt, "cron_task_executor", None) is None:
            return SkillResult(ok=False, error="cron_task_executor not initialized")

        executor = rt.cron_task_executor
        result = await executor.execute_task(ctx.skill)

        status = result.get("status", "")

        if status == "failed":
            return SkillResult(ok=False, error=result.get("reason", "执行失败"),
                               meta={"status": "failed", "http_status": result.get("http_status")})

        if status == "skipped":
            return SkillResult(ok=True, text="", status="skipped",
                               meta={"status": "skipped", "reason": result.get("reason")})

        # success: cron_task_executor 已自行处理输出，返回空文本避免 runner 重复推送
        meta = {"status": "success", "http_status": result.get("http_status"),
                "action_result": result.get("action_result", "")}
        return SkillResult(ok=True, text="", meta=meta)

    def describe(self) -> dict:
        return {"name": "智动任务引擎", "modes": ["http_condition"],
                "desc": "HTTP API + 条件判断 + TTS/Bark/TV 推送"}
