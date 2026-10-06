"""HA 动作执行引擎：静默调用 Home Assistant 服务。

用于「开电视」「切台」等不需要说话的动作类技能。
brain 配置：
  domain:   HA 域（如 media_player）
  service:  HA 服务（如 turn_on / select_source）
  entity:   目标实体模板（支持 {{member}}/{{room}}/{{channel}}），可为空（用 data）
  data:     额外数据模板（dict）

执行成功返回 ok=True, text="" → runner 不推送输出（静默）。
"""
from __future__ import annotations

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.ha_action")


class HAActionEngine:
    async def run(self, ctx: SkillContext) -> SkillResult:
        brain = ctx.skill.get("brain") or {}
        rt = ctx.rt
        if rt is None or getattr(rt, "ha", None) is None:
            return SkillResult(ok=False, error="runtime ha not ready")

        domain = (brain.get("domain") or "").strip()
        service = (brain.get("service") or "").strip()
        if not domain or not service:
            return SkillResult(ok=False, error="brain.domain/service required")

        # 模板渲染
        payload = ctx.payload or {}
        def _r(v):
            if isinstance(v, str):
                for k in ("member", "room", "channel", "entity"):
                    if k in payload:
                        v = v.replace("{{" + k + "}}", str(payload.get(k, "")))
                        v = v.replace("{{event." + k + "}}", str(payload.get(k, "")))
                return v
            return v

        entity = _r(brain.get("entity") or "")
        # v0.3: 优先用触发方 params 传入的 entity（trigger 动作链可精确指定设备）
        if payload.get("entity"):
            entity = str(payload["entity"])
        data = {k: _r(v) for k, v in (brain.get("data") or {}).items()}
        # payload 里的 channel/entity 等透传进 data
        for pk in ("channel", "source"):
            if pk in payload and pk not in data:
                data[pk] = payload[pk]
        if entity:
            data.setdefault("entity_id", entity)

        try:
            res = await rt.ha.call_service_strict(domain, service, data)
        except Exception as e:
            logger.warning("ha_action %s.%s failed: %s", domain, service, e)
            return SkillResult(ok=False, error=str(e), status="error")

        meta = {"engine": "ha_action", "domain": domain, "service": service, "result": str(res)[:200]}
        if ctx.payload.get("_trigger_room"):
            meta["room"] = ctx.payload["_trigger_room"]
        if ctx.payload.get("_trigger_member"):
            meta["person"] = ctx.payload["_trigger_member"]
        # 动作执行：返回空文本静默，不触发发声
        return SkillResult(ok=True, text="", status="ok", meta=meta)

    def describe(self) -> dict:
        return {"name": "HA 动作执行引擎", "modes": ["service"],
                "desc": "调用 HA 服务（开电视/切台/开关设备），静默不发声"}
