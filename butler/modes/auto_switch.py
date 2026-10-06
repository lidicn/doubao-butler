"""定位驱动模式自动切换。

v1.6 P0-2：基于定位引擎自动判断模式，带防抖延时。

规则：
  - 所有人离家（置信度≥0.6 都不在家）持续 10 分钟 → 离家模式
  - 有人回家（之前是离家模式，现在有人在家）→ 日常模式
  - lidicn 在客厅 + TV 开启 持续 5 分钟 → 观影模式
  - 主卧静止 6 小时（lidicn 在主卧且无活动）→ 睡眠模式

防抖：连续 N 次检查满足条件才切换，避免误切换。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from butler.logging_setup import get_logger

logger = get_logger("butler.modes.auto_switch")

# 防抖计数：{condition_key: consecutive_count}
_debounce: dict[str, int] = {}

# 触发阈值（连续检查次数）
THRESHOLD_AWAY = 2      # 离家：连续 2 次（每 5 分钟一次 = 10 分钟）
THRESHOLD_HOME = 1      # 回家：立即切换（1 次）
THRESHOLD_MOVIE = 1     # 观影：连续 1 次（5 分钟）
THRESHOLD_SLEEP = 12    # 睡眠：连续 12 次（每 5 分钟 = 60 分钟，简化版）


@dataclass
class AutoSwitchResult:
    """自动切换结果。"""
    switched: bool = False
    from_mode: str = ""
    to_mode: str = ""
    reason: str = ""
    debounce_count: int = 0


class ModeAutoSwitcher:
    """定位驱动模式自动切换器。"""

    def __init__(self, rt=None):
        self.rt = rt

    async def check_and_switch(self) -> AutoSwitchResult:
        """检查定位状态，满足条件则切换模式。"""
        result = AutoSwitchResult()

        if not self.rt or not hasattr(self.rt, "mode_engine"):
            return result

        engine = self.rt.mode_engine
        current = engine.current

        # 获取定位快照
        presence = self._get_presence_snapshot()
        if not presence:
            return result

        users = presence.get("users", {})
        anyone_home = any(
            u.get("confidence", 0) >= 0.6 and u.get("room")
            for u in users.values()
        )
        all_away = not anyone_home

        # lidicn 位置
        lidicn = users.get("lidicn", {})
        lidicn_room = lidicn.get("room", "")
        lidicn_conf = lidicn.get("confidence", 0)

        # ---- 规则 1：所有人离家 → 离家模式 ----
        if current != "away" and all_away:
            count = self._bump("away", reset_on_false=True)
            result.debounce_count = count
            if count >= THRESHOLD_AWAY:
                engine.switch("away", source="auto:all_away")
                result.switched = True
                result.from_mode = current
                result.to_mode = "away"
                result.reason = "所有人离家"
                logger.info("auto switch to away (all away, debounce=%d)", count)
                return result

        # ---- 规则 2：有人回家（从离家模式）→ 日常模式 ----
        if current == "away" and anyone_home:
            count = self._bump("home", reset_on_false=True)
            result.debounce_count = count
            if count >= THRESHOLD_HOME:
                engine.switch("daily", source="auto:someone_home")
                result.switched = True
                result.from_mode = current
                result.to_mode = "daily"
                result.reason = "有人回家"
                logger.info("auto switch to daily (someone home)")
                return result

        # ---- 规则 3：lidicn 在客厅 + TV 开启 → 观影模式 ----
        if current not in ("away", "sleep", "movie"):
            tv_on = await self._is_tv_on()
            if "客厅" in lidicn_room and lidicn_conf >= 0.6 and tv_on:
                count = self._bump("movie", reset_on_false=True)
                result.debounce_count = count
                if count >= THRESHOLD_MOVIE:
                    engine.switch("movie", source="auto:living_room_tv")
                    result.switched = True
                    result.from_mode = current
                    result.to_mode = "movie"
                    result.reason = "lidicn 在客厅且 TV 开启"
                    logger.info("auto switch to movie (living room + TV on)")
                    return result

        # ---- 规则 4：lidicn 在主卧静止 → 睡眠模式 ----
        if current not in ("away", "sleep"):
            if "主卧" in lidicn_room and lidicn_conf >= 0.7:
                count = self._bump("sleep", reset_on_false=True)
                result.debounce_count = count
                if count >= THRESHOLD_SLEEP:
                    engine.switch("sleep", source="auto:bedroom_still")
                    result.switched = True
                    result.from_mode = current
                    result.to_mode = "sleep"
                    result.reason = "lidicn 在主卧长时间静止"
                    logger.info("auto switch to sleep (bedroom still, debounce=%d)", count)
                    return result

        # 不满足任何切换条件，重置对应防抖
        self._reset_if_needed(current, all_away, anyone_home, lidicn_room, lidicn_conf)
        return result

    def _get_presence_snapshot(self) -> dict:
        """获取定位引擎快照。"""
        if not self.rt or not hasattr(self.rt, "presence_engine"):
            return {}
        try:
            return self.rt.presence_engine.snapshot()
        except Exception as e:
            logger.debug("presence snapshot failed: %s", e)
            return {}

    async def _is_tv_on(self) -> bool:
        """检查客厅 TV 是否开启（async）。"""
        if not self.rt or not hasattr(self.rt, "ha"):
            return False
        try:
            states = await self.rt.ha.get_states()
            for entity in states:
                eid = entity.get("entity_id", "")
                state = entity.get("state", "")
                if eid.startswith("media_player.") and state in ("playing", "on"):
                    name = entity.get("attributes", {}).get("friendly_name", "")
                    if "电视" in name or "TV" in name.upper() or "mytv" in eid.lower():
                        return True
        except Exception as e:
            logger.debug("tv status check failed: %s", e)
        return False

    def _bump(self, key: str, reset_on_false: bool = True) -> int:
        """增加防抖计数。"""
        _debounce[key] = _debounce.get(key, 0) + 1
        return _debounce[key]

    def _reset(self, key: str) -> None:
        """重置防抖计数。"""
        if key in _debounce:
            del _debounce[key]

    def _reset_if_needed(self, current: str, all_away: bool, anyone_home: bool,
                          lidicn_room: str, lidicn_conf: float) -> None:
        """根据当前状态重置不满足条件的防抖计数。"""
        if not all_away:
            self._reset("away")
        if current != "away" or not anyone_home:
            self._reset("home")
        if not ("客厅" in lidicn_room and lidicn_conf >= 0.6):
            self._reset("movie")
        if not ("主卧" in lidicn_room and lidicn_conf >= 0.7):
            self._reset("sleep")
