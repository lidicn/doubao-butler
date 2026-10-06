"""用户插件引擎模板（示例：提示词 → LLM → 一句话文本）。

用法：
  1) 容器内复制模板到用户插件目录（data 是持久化卷）：
     docker exec doubao-butler cp -r /app/butler/skills/engines/_template_ /app/data/plugins/my_engine
  2) 改 manifest.json 的 id/name，改本文件 run() 的逻辑
  3) docker restart doubao-butler，WebUI「技能」页即可在引擎下拉里看到新引擎
     （WebUI 也能用「基于模板新建」直接建技能）

协议契约：
  - 目录：<plugins_root>/<engine_id>/manifest.json + entry 指定的 Python 模块
  - manifest.json：{"id","name","version","entry","class"}，`_` 前缀目录会被跳过（即本模板）
  - 引擎类须实现 async def run(self, ctx) -> SkillResult
  - 可选实现 describe() -> dict，用于 WebUI 展示名称/模式/说明
  - 加载失败只跳过该引擎，不影响其他引擎

ctx（butler.skills.runner_types.SkillContext）可用字段：
  ctx.skill    技能定义 dict，常用 brain.prompt / brain.max_chars / senses / output
  ctx.rt       全局 Runtime：rt.llm / rt.tts / rt.tv / rt.ha / rt.bark /
               rt.memory(memory-agent) / rt.doubao(doubao2api) / rt.devices / rt.roles
  ctx.role     解析出的角色（butler.roles.store.Role）：voice / tts_backend /
               output_devices / system / presence_rooms
  ctx.source   触发来源：api | mqtt | test
  ctx.dry_run  试跑：不要真的推送/外发，只产出文本
  ctx.payload  触发方附带参数
  ctx.now      当前时间字符串

重要：输出推送（TV / 小爱 / Bark）由 runner 统一按角色的 output_devices 分发，
引擎只需专心产出 SkillResult.text。角色音色、设备路由、去重、熔断都不用引擎操心。
"""
from __future__ import annotations

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.template")


class Engine:
    """示例引擎：把 brain.prompt 交给 LLM，产出一句不超过 max_chars 的话。"""

    async def run(self, ctx: SkillContext) -> SkillResult:
        brain = ctx.skill.get("brain") or {}
        prompt = brain.get("prompt") or "说一句关心家人的话。"
        max_chars = int(brain.get("max_chars") or 60)
        system = (ctx.role.system if getattr(ctx, "role", None) else "") or "你是家庭管家，说话自然简短。"

        rt = ctx.rt
        if rt is None or getattr(rt, "llm", None) is None:
            return SkillResult(ok=False, error="runtime not ready")

        try:
            text, _ = await rt.llm.chat(
                system, [{"role": "user", "content": prompt}],
                max_tokens=256, temperature=0.9,
            )
        except Exception as e:
            logger.warning("template engine llm failed: %s", e)
            return SkillResult(ok=False, error=str(e))

        text = (text or "").strip()
        if max_chars and len(text) > max_chars:
            text = text[:max_chars]
        if not text:
            return SkillResult(ok=False, error="empty text")

        # dry_run 时仍然产出文本（供 WebUI 试跑预览），推送由 runner 拦截
        return SkillResult(ok=True, text=text, meta={"engine": "template", "dry_run": ctx.dry_run})

    def describe(self) -> dict:
        return {"name": "我的引擎（模板）", "modes": [], "desc": "复制后改写 run()"}
