"""提醒找人引擎：scheduled 事件到点 → 全屋找人 → 所在房间播报 / Bark 兜底。

payload（由 trigger 动作参数或外部注入）：
  member: 目标成员（如 lidicn / Kevin / Emily）
  text:   提醒内容

行为：
- 找到：meta["room"]=所在房间，text=称呼前缀+提醒内容 → runner 按房间分发小爱播报。
- 未找到：直接 Bark 推送，返回 text=""（静默，避免 runner 全屋播报打扰）。
"""
from __future__ import annotations

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.reminder_find")


class ReminderFindEngine:
    async def run(self, ctx: SkillContext) -> SkillResult:
        rt = ctx.rt
        payload = ctx.payload or {}
        member = str(payload.get("member") or "").strip()
        text = str(payload.get("text") or "").strip()
        if not text:
            text = "提醒你一下"
        if rt is None or getattr(rt, "locator", None) is None:
            return SkillResult(ok=False, error="locator not ready")

        meta: dict = {"engine": "reminder_find", "member": member}
        try:
            res = await rt.locator.find_member(member) if member else {"found": False, "detail": "未指定成员"}
        except Exception as e:
            logger.warning("locator.find_member failed: %s", e)
            res = {"found": False, "detail": str(e)}

        meta["locate"] = res

        if res.get("found") and res.get("room"):
            room = str(res["room"])
            meta["room"] = room
            meta["via"] = res.get("via", "vision")
            # 组装播报：带称呼前缀
            announce = text
            if member:
                try:
                    nick = rt.settings.nickname_of(member)
                    if nick and nick != member:
                        announce = f"{nick}，{text}"
                except Exception:
                    pass
            logger.info("reminder locate hit: %s in %s (@%s)", member, room, res.get("last_seen"))
            return SkillResult(ok=True, text=announce, meta=meta)

        # 未找到 → Bark 兜底（静默，不触发全屋播报）
        try:
            title = "豆包管家提醒"
            body = f"提醒：{text}"
            if member:
                body += f"（在全屋摄像头中未找到{member}）"
            ok = await rt.bark.push(body, title=title)
            meta["fallback"] = "bark"
            meta["bark_ok"] = ok
            logger.info("reminder not found member=%s, bark fallback ok=%s", member, ok)
        except Exception as e:
            logger.warning("reminder bark fallback failed: %s", e)
            meta["fallback"] = "bark_failed"
        return SkillResult(ok=True, text="", status="bark_fallback", meta=meta)

    def describe(self) -> dict:
        return {"name": "提醒找人引擎", "modes": ["find"],
                "desc": "到点全屋找人→所在房间播报，找不到 Bark"}
