"""行动执行器：把决策层的白名单行动落到真实通道。

白名单（安全边界，硬编码）：
- speak              通过指定房间小爱音箱播报（ha notify_text）
- notify             推送到豆包app对应角色对话（notifier.push direct_text）
- reminder           设定稍后提醒（复用 tools._set_reminder，到点全屋找人）
- suggest_automation 推送「建议创建自动化」文案（后续接 autoflow MCP）

注意：白名单之外任何行动（尤其控制设备）一律拒绝，由 engine 层过滤。
"""
from __future__ import annotations

import logging
from datetime import datetime

from butler.logging_setup import get_logger

logger = get_logger("butler.decision.action_router")

ALLOWED_ACTIONS = ("speak", "notify", "reminder", "suggest_automation")


class ActionRouter:
    def __init__(self, rt, cfg):
        self.rt = rt
        self.cfg = cfg

    async def execute(self, action: dict) -> str:
        """执行一个决策行动。返回执行结果描述（失败不抛异常）。"""
        act = str(action.get("action") or "")
        if act not in ALLOWED_ACTIONS:
            return f"拒绝：行动 {act} 不在白名单"
        text = str(action.get("text") or "").strip()
        if not text:
            return "拒绝：text 为空"

        try:
            if act == "speak":
                return await self._speak(text, action)
            if act == "notify":
                return await self._notify(text, action)
            if act == "reminder":
                return await self._reminder(text, action)
            if act == "suggest_automation":
                return await self._suggest_automation(text, action)
        except Exception as e:
            logger.warning("decision action %s failed: %s", act, e)
            return f"执行失败：{e}"
        return f"未知行动 {act}"

    # ---- speak ----

    async def _speak(self, text: str, action: dict) -> str:
        """按 room 找小爱音箱播报。room 为空则用主角色 butler 的输出设备。"""
        ha = getattr(self.rt, "ha", None)
        if ha is None or not ha.s.ha_token:
            return "HA 未配置，无法播报"
        devs = self.rt.devices.all() if getattr(self.rt, "devices", None) else []
        room = str(action.get("room") or "")

        targets = []
        for d in devs:
            if d.type != "xiaomi" or not d.enabled or not d.ha_entity:
                continue
            if room and d.room != room:
                continue
            targets.append(d)
        # 按房间优先，其次但但 butler 主输出设备
        if not targets and not room:
            role = getattr(self.rt, "roles", None)
            brole = role.get("butler") if role else None
            for did in (brole.output_devices if brole else ["xiao_living", "tv_living"]):
                dev = self.rt.devices.get(did)
                if dev and dev.type == "xiaomi" and dev.enabled and dev.ha_entity:
                    targets.append(dev)

        if not targets:
            return f"房间 {room or '(默认)'} 无可用小爱音箱"
        dev = targets[0]
        res = await ha.notify_message(text, dev.ha_entity)
        logger.info("decision speak room=%s device=%s -> %s", room or "*", dev.id, res)
        return f"已在{dev.room or room}播报：{text}"

    # ---- notify ----

    async def _notify(self, text: str, action: dict) -> str:
        notifier = getattr(self.rt, "notifier", None)
        if notifier is None:
            return "notifier 未就绪，无法通知"
        role = str(action.get("role") or "butler")
        await notifier.push(role, "decision", direct_text=text)
        logger.info("decision notify role=%s -> %s", role, text)
        return f"已推送到豆包app（{role}）：{text}"

    # ---- reminder ----

    async def _reminder(self, text: str, action: dict) -> str:
        """设定提醒。时间：minutes（默认 5）或 at（绝对时间）。member 到点全屋找人。"""
        from butler.core.tools import _set_reminder
        minutes = int(action.get("minutes") or 5)
        at = str(action.get("at") or "")
        member = str(action.get("member") or "")
        # _set_reminder 第一个参数只要带 scheduler 属性即可（其内部经 get_runtime 取 trigger 引擎）
        res = await _set_reminder(self.rt, text, minutes, at=at, member=member)
        logger.info("decision reminder -> %s", res)
        return res

    # ---- suggest_automation ----

    async def _suggest_automation(self, text: str, action: dict) -> str:
        """推送自动化建议到豆包app（v0.9 先不接 autoflow MCP，仅建议文案，用户确认后另行创建）。"""
        notifier = getattr(self.rt, "notifier", None)
        if notifier is None:
            return "notifier 未就绪"
        role = str(action.get("role") or "butler")
        msg = f"🤖 发现一个可以自动化的习惯：{text}\n回复「创建」即可让管家安排。"
        await notifier.push(role, "decision", direct_text=msg)
        logger.info("decision suggest_automation role=%s -> %s", role, text)
        return f"已推送自动化建议：{text}"
