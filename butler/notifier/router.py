"""通知路由层：统一 Bark / TTS / 豆包app 三通道分发。

设计：
- 调用方只需要说"我要发一条通知"，不用关心走哪条路
- 路由规则：
  - critical/warning → 始终走 Bark（手机推送），不管在不在场
  - info + 人在场 → 走 TTS（音箱播报）
  - info + 人不在场 → 走 Bark
  - TTS 合成失败 → 自动降级到 Bark，不丢消息
  - 所有通知同时推送到豆包app对应角色对话（AppNotifier.push）
- 路由决策基于 priority + presence + 设备可用性
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from butler.logging_setup import get_logger

logger = get_logger("butler.notify.router")


class NotifyPriority(str, Enum):
    CRITICAL = "critical"   # 告警/安全：必达
    WARNING = "warning"     # 重要提醒
    INFO = "info"           # 常规播报


@dataclass
class NotifyRequest:
    """一条通知请求。"""
    content: str
    role_id: str = "butler"
    priority: NotifyPriority = NotifyPriority.INFO
    device_id: str | None = None       # 目标音箱（info 级用）
    member: str = ""                   # 说话人
    voice: str | None = None
    nowvoice_voice: str | None = None
    play_on_speaker: bool = True       # 是否尝试 TTS 播报
    push_to_app: bool = True           # 是否同时推豆包app
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotifyResult:
    """通知发送结果。"""
    channel: str = ""          # "tts" | "bark" | "app" | "none"
    success: bool = False
    fallback_used: bool = False
    detail: str = ""


class NotificationRouter:
    """通知路由：按优先级和在场状态自动选通道。"""

    def __init__(self, rt=None):
        self.rt = rt
        self._bark_fallback_count = 0
        self._tts_fail_count = 0

    def set_rt(self, rt) -> None:
        self.rt = rt

    async def route(self, req: NotifyRequest) -> NotifyResult:
        """路由一条通知到合适的通道。"""
        rt = self.rt
        result = NotifyResult()

        # 1. 高优先级：始终 Bark + App
        if req.priority in (NotifyPriority.CRITICAL, NotifyPriority.WARNING):
            bark_ok = await self._push_bark(req, critical=True)
            app_ok = await self._push_app(req) if req.push_to_app else False
            result.channel = "bark" if bark_ok else ("app" if app_ok else "none")
            result.success = bark_ok or app_ok
            result.detail = f"critical: bark={bark_ok} app={app_ok}"
            return result

        # 2. info 级：先试 TTS，失败降级 Bark
        if req.play_on_speaker and req.device_id and rt and rt.tts:
            try:
                res = await rt.tts.speak(
                    req.content,
                    voice=req.voice,
                    device_id=req.device_id,
                    member=req.member,
                    nowvoice_voice=req.nowvoice_voice,
                )
                if res is not None:
                    result.channel = "tts"
                    result.success = True
                    result.detail = "tts played"
                    # 同时推 app（静默模式）
                    if req.push_to_app:
                        await self._push_app(req, silent=True)
                    return result
            except Exception as e:
                self._tts_fail_count += 1
                logger.warning("notify route: TTS failed, fallback to Bark: %s", e)

        # 3. 降级：Bark
        bark_ok = await self._push_bark(req, critical=False)
        app_ok = await self._push_app(req) if req.push_to_app else False
        result.channel = "bark" if bark_ok else ("app" if app_ok else "none")
        result.success = bark_ok or app_ok
        result.fallback_used = True
        result.detail = f"fallback: bark={bark_ok} app={app_ok}"
        return result

    async def _push_bark(self, req: NotifyRequest, critical: bool = False) -> bool:
        """推 Bark 手机推送。"""
        rt = self.rt
        if rt is None or not hasattr(rt, "bark") or rt.bark is None:
            return False
        try:
            title = f"【告警】{req.role_id}" if critical else f"豆包管家 · {req.role_id}"
            sound = "alarm" if critical else None
            priority = "critical" if critical else "info"
            ok = await rt.bark.push(
                req.content,
                title=title,
                sound=sound,
                priority=priority,
            )
            return bool(ok)
        except Exception as e:
            logger.warning("bark push failed: %s", e)
            return False

    async def _push_app(self, req: NotifyRequest, silent: bool = False) -> bool:
        """推豆包app角色对话。"""
        rt = self.rt
        if rt is None or not hasattr(rt, "notifier") or rt.notifier is None:
            return False
        try:
            if silent:
                # 静默模式：直接输出文本，不触发角色人设
                await rt.notifier.push(
                    req.role_id, scene="",
                    direct_text=req.content,
                )
            else:
                await rt.notifier.push(req.role_id, scene=req.content)
            return True
        except Exception as e:
            logger.warning("app push failed: %s", e)
            return False

    def stats(self) -> dict:
        return {
            "bark_fallback_count": self._bark_fallback_count,
            "tts_fail_count": self._tts_fail_count,
        }
