"""唤醒引擎：冷却、免打扰时段、静音判断。决定何时可以主动开口。"""
from __future__ import annotations

import asyncio
import time

from butler.config import Settings
from butler.core.state import RuntimeState
from butler.logging_setup import get_logger, warn_throttled
from butler.store import repo

logger = get_logger("butler.wakeup")


def _now_local() -> tuple[int, int]:
    t = time.localtime()
    return t.tm_hour, t.tm_min


def _in_window(hour: int, minute: int, window: str) -> bool:
    """window 形如 '23:00-07:30' 或 '14:00-15:00'，可多段逗号分隔。"""
    now = hour * 60 + minute
    for seg in window.split(","):
        seg = seg.strip()
        if "-" not in seg:
            continue
        a, b = seg.split("-", 1)
        try:
            ah, am = map(int, a.split(":"))
            bh, bm = map(int, b.split(":"))
        except Exception:
            warn_throttled(logger, "wakeup.window_seg", "wakeup 静默窗口段写法解析不了（该段忽略，其余段照判）", seg)
            continue
        start = ah * 60 + am
        end = bh * 60 + bm
        if start <= end:
            if start <= now < end:
                return True
        else:  # 跨午夜
            if now >= start or now < end:
                return True
    return False


class WakeupEngine:
    def __init__(self, settings: Settings, state: RuntimeState):
        self.s = settings
        self.state = state

    def _in_dnd(self) -> bool:
        if not self.s.dnd_windows:
            return False
        h, m = _now_local()
        return _in_window(h, m, ",".join(self.s.dnd_windows))

    async def decide(self, trigger: str, member: str = "", room: str = "客厅") -> tuple[bool, str]:
        """返回 (是否允许开口, 原因)。同时写唤醒日志。"""
        t0 = time.time()
        reason = "pass"
        allowed = True
        if self.state.is_muted():
            allowed, reason = False, "muted"
        elif self._in_dnd():
            allowed, reason = False, "dnd"
        elif time.time() - self.state.last_speak_ts < self.s.cooldown_seconds:
            allowed, reason = False, "cooldown"
        # 成员维度冷却（cooldown_same_member）原写在此处：
        # 它的时间判定与上一条 elif 完全相同，条件永远不可达（wakeup_log 该 reason 实测 0 行），已删。
        await asyncio.to_thread(repo.log_wakeup, trigger, "pass" if allowed else reason, room=room, member=member, reason=reason, cost_ms=int((time.time() - t0) * 1000))
        return allowed, reason
