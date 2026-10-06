"""运行时状态：在场人员、对话状态机节点、冷却时钟、静音/免打扰。

所有访问发生在单事件循环内（MQTT 消费者协程 + API 协程），无需加锁。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class DialogState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    WAITING = "waiting"


@dataclass
class PresentMember:
    name: str
    since: float = field(default_factory=time.time)
    confidence: float = 0.0


@dataclass
class DialogSnapshot:
    state: str = DialogState.IDLE.value
    present: list[dict] = field(default_factory=list)
    last_speak_ts: float = 0.0
    last_speak_member: str = ""
    muted_until: float = 0.0
    today_turns: int = 0
    recent: list[dict] = field(default_factory=list)


class RuntimeState:
    def __init__(self):
        self.state = DialogState.IDLE
        self.present: dict[str, PresentMember] = {}
        self.last_speak_ts = 0.0
        self.last_speak_member = ""
        self.muted_until = 0.0
        self.today_turns = 0
        self._today_date = time.strftime("%Y-%m-%d")
        self.recent_turns: list[dict] = []  # 最近若干轮，供 WebUI

    def set_state(self, st: DialogState) -> None:
        self.state = st

    def mark_present(self, name: str, confidence: float = 0.0) -> None:
        if name in self.present:
            self.present[name].confidence = max(self.present[name].confidence, confidence)
        else:
            self.present[name] = PresentMember(name=name, confidence=confidence)

    def mark_absent(self, name: str) -> None:
        self.present.pop(name, None)

    def is_present(self, name: str) -> bool:
        return name in self.present

    def present_list(self) -> list[PresentMember]:
        return list(self.present.values())

    def note_speak(self, member: str) -> None:
        self.last_speak_ts = time.time()
        self.last_speak_member = member
        self._rollover_day()
        self.today_turns += 1

    def add_turn(self, member: str, role: str, text: str) -> None:
        self._rollover_day()
        self.recent_turns.insert(0, {"ts": time.time(), "member": member, "role": role, "text": text})
        self.recent_turns = self.recent_turns[:30]

    def mute(self, seconds: int) -> None:
        self.muted_until = time.time() + seconds

    def is_muted(self) -> bool:
        return time.time() < self.muted_until

    def _rollover_day(self) -> None:
        d = time.strftime("%Y-%m-%d")
        if d != self._today_date:
            self._today_date = d
            self.today_turns = 0

    def snapshot(self) -> DialogSnapshot:
        return DialogSnapshot(
            state=self.state.value,
            present=[{"name": p.name, "since": p.since, "confidence": p.confidence} for p in self.present.values()],
            last_speak_ts=self.last_speak_ts,
            last_speak_member=self.last_speak_member,
            muted_until=self.muted_until,
            today_turns=self.today_turns,
            recent=self.recent_turns,
        )
