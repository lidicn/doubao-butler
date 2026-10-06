"""人格与成员装配：系统 prompt、成员称呼/语气、主动问候语。"""
from __future__ import annotations

from butler.config import Settings


class PersonaEngine:
    def __init__(self, settings: Settings):
        self.s = settings

    def system_prompt(self) -> str:
        return self.s.persona.system

    def member_context(self, member: str) -> str:
        mc = self.s.member_by_name(member)
        if not mc:
            return f"对方是「{member or '家人'}」，你还不熟悉 TA，先自然地打招呼。"
        parts = [f"对方是{mc.name}"]
        if mc.nickname:
            parts.append(f"你平时叫 TA「{mc.nickname}」")
        if mc.room:
            parts.append(f"常待在{mc.room}")
        if mc.tone:
            parts.append(f"说话语气：{mc.tone}")
        if mc.topics_allow:
            parts.append(f"可以多聊：{', '.join(mc.topics_allow)}")
        if mc.topics_block:
            parts.append(f"避免聊：{', '.join(mc.topics_block)}")
        return "；".join(parts) + "。"

    def greeting(self, member: str) -> str:
        tpl = self.s.persona.greeting_template
        nick = self.s.nickname_of(member)
        try:
            return tpl.format(nickname=nick, name=member)
        except Exception:
            return f"{nick}回来啦"
