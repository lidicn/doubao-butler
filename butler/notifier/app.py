"""主动推送通道：把 butler 的主动消息推送到豆包app各角色对话。

设计（v0.5）：
- 豆包app定位为「多角色通知展示窗口」（单向）：butler 主动说的话出现在对应角色对话。
- 用户在豆包app回复的消息 butler 收不到（doubao2api 无 webhook），用户主动交互走小爱音箱。
- 推送流程：构造场景描述（user 消息）+ 角色 system prompt → doubao2api chat(keep_conversation)
  → 豆包大模型生成自然回复 → 出现在豆包app对应角色对话。
- 每个角色一个 conversation_id，存在 role_state.json，跨容器重启持久化。

用法：
    await rt.notifier.push("butler", "现在7:10，大佬还没起床，请叫醒他")
    await rt.notifier.push("butler", "提醒", direct_text="大佬，该去医院复诊了")
"""
from __future__ import annotations

from butler.logging_setup import get_logger

logger = get_logger("butler.notifier")

# 主动推送的包装前缀。doubao2api 会把这条 user 消息轮询回 webhook，
# 不拦就是管家把「自己说的话」当用户指令再跑一遍（自问自答循环）。
SELF_PUSH_PREFIX = "请原样输出以下内容，不要加任何前缀、后缀、解释或表情："


def is_self_push(text: str) -> bool:
    """是否为管家自己发出的推送文本（webhook 无状态拦截用，重启不失效）。"""
    return text.startswith(SELF_PUSH_PREFIX)


class AppNotifier:
    def __init__(self, rt):
        self.rt = rt

    async def push(self, role_id: str, scene: str,
                   direct_text: str | None = None) -> str:
        """主动推送一条消息到指定角色的豆包app对话。

        Args:
            role_id: 角色 ID（butler / xiaoyue / gu_anheng 等）
            scene: 场景描述，作为 user 消息发给豆包大模型
            direct_text: 若提供，system prompt 要求豆包原样输出此文本（用于精确提醒）

        Returns:
            豆包大模型生成的回复文本（也已出现在豆包app对话里）
        """
        rt = self.rt
        if rt is None or rt.doubao is None or rt.role_state is None:
            logger.warning("notifier.push skipped: rt/doubao/role_state not ready")
            return ""
        role = rt.roles.get(role_id) if rt.roles else None
        if role is None:
            logger.warning("notifier.push: role %s not found", role_id)
            return ""
        system = role.system or "你是家庭管家。"
        conv_id = rt.role_state.get(role_id)
        # direct_text 时用 silent 模式：不发 system_prompt，把原样输出指令移到 user 消息
        if direct_text:
            # 加【ℹ️】前缀，让豆包长期记忆规则触发只回复👌
            user_msg = f"{SELF_PUSH_PREFIX}【ℹ️】{direct_text}"
            use_silent = True
        else:
            user_msg = scene
            use_silent = False
        try:
            reply, new_conv = await rt.doubao.chat(
                user_msg, keep_conversation=True,
                conversation_id=conv_id,
                system_prompt=None if use_silent else system,
                silent=use_silent,
            )
        except Exception as e:
            logger.warning("notifier.push doubao chat failed: %s", e)
            return ""
        if new_conv and new_conv != conv_id:
            rt.role_state.set(role_id, new_conv)
            logger.info("notifier: role %s conversation_id=%s", role_id, new_conv)
        logger.info("notifier.push role=%s reply=%s", role_id, (reply or "")[:80])
        return reply or ""

    async def reply(self, role_id: str, text: str) -> str:
        """把 butler 已生成的回复原样写入豆包app对话（回声模式，不让大模型改写）。

        用于 webhook 回复：用户在手机发消息 → butler 生成回复 → 原样写回对话。
        """
        rt = self.rt
        if rt is None or rt.doubao is None or rt.role_state is None:
            logger.warning("notifier.reply skipped: rt/doubao/role_state not ready")
            return ""
        role = rt.roles.get(role_id) if rt.roles else None
        if role is None:
            logger.warning("notifier.reply: role %s not found", role_id)
            return ""
        # 回声模式：system 强制原样输出，user 消息就是要写入的文本
        echo_system = (
            "你是一个回声助手。用户发什么内容，你就一字不差地原样回复什么，"
            "不要加任何解释、前缀、后缀、表情或改写。直接输出用户的内容。"
        )
        conv_id = rt.role_state.get(role_id)
        try:
            reply, new_conv = await rt.doubao.chat(
                text, keep_conversation=True,
                conversation_id=conv_id, system_prompt=echo_system,
            )
        except Exception as e:
            logger.warning("notifier.reply doubao chat failed: %s", e)
            return ""
        if new_conv and new_conv != conv_id:
            rt.role_state.set(role_id, new_conv)
        logger.info("notifier.reply role=%s text=%s", role_id, (reply or "")[:60])
        return reply or ""

    async def push_image(self, role_id: str, image_url: str,
                         prompt: str = "请描述这个画面") -> str:
        """把一张图片推送到指定角色的豆包app对话，附带分析。

        image_url 可以是 HTTP URL（管家会下载并转 base64）或 data: URL。
        使用 /v1/images/analyses + keep_conversation，图片和分析都出现在目标对话里。
        """
        import base64
        import httpx
        rt = self.rt
        if rt is None or rt.doubao is None or rt.role_state is None:
            logger.warning("notifier.push_image skipped: rt/doubao/role_state not ready")
            return ""

        # HTTP URL → base64 data URL（doubao2api 视觉端点需要 data URL 或 CDN URL）
        if image_url.startswith("http"):
            try:
                async with httpx.AsyncClient(timeout=30) as c:
                    r = await c.get(image_url)
                    r.raise_for_status()
                    img_b64 = base64.b64encode(r.content).decode()
                    data_url = f"data:image/jpeg;base64,{img_b64}"
            except Exception as e:
                logger.warning("push_image download failed: %s", e)
                return ""
        else:
            data_url = image_url

        conv_id = rt.role_state.get(role_id)
        try:
            text, new_conv = await rt.doubao.vision(
                prompt, data_url,
                keep_conversation=True,
                conversation_id=conv_id,
            )
        except Exception as e:
            logger.warning("notifier.push_image failed: %s", e)
            return ""
        if new_conv and new_conv != conv_id:
            rt.role_state.set(role_id, new_conv)
        logger.info("notifier.push_image role=%s conv=%s reply=%s",
                     role_id, new_conv, (text or "")[:80])
        return text or ""

    async def push_to_all(self, scene: str, direct_text: str | None = None,
                          role_ids: list[str] | None = None) -> dict[str, str]:
        """推送到多个角色。返回 {role_id: reply}。"""
        rt = self.rt
        if rt is None or rt.roles is None:
            return {}
        ids = role_ids or [r.id for r in rt.roles.all() if r.enabled]
        results = {}
        for rid in ids:
            results[rid] = await self.push(rid, scene, direct_text)
        return results
