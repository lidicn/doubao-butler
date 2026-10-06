"""模式引擎：5 种互斥情景模式的状态机 + 行为规则。

v1.6 P0-1：模式从 HA 自动化升级为管家的全局决策上下文。
复用 v1.7 的 ts_mode_transitions 表做持久化。

5 种模式：
  - daily  日常（默认）
  - movie  观影
  - sleep  睡眠
  - guest  会客
  - away   离家（优先级最高）

模式行为规则：
  - tts_allowed:     是否允许 TTS 播报
  - tts_emergency_only: 是否仅紧急警报可 TTS
  - bark_silent:     Bark 是否静默（不弹通知）
  - skills_allowed:  允许的技能类别（all/emergency_only/anomaly_only/none）
  - care_skills_disabled: 是否禁用关怀类技能
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from butler.logging_setup import get_logger
from butler.timeseries import store as ts

logger = get_logger("butler.modes.engine")

# 5 种模式
MODES = ("daily", "movie", "sleep", "guest", "away")

# 模式中文名
MODE_NAMES = {
    "daily": "日常",
    "movie": "观影",
    "sleep": "睡眠",
    "guest": "会客",
    "away": "离家",
}

# 模式行为规则
MODE_RULES = {
    "daily": {
        "tts_allowed": True,
        "tts_emergency_only": False,
        "bark_silent": False,
        "skills_allowed": "all",
        "care_skills_disabled": False,
        "security_level": "monitor_only",
    },
    "movie": {
        "tts_allowed": False,
        "tts_emergency_only": False,
        "bark_silent": True,
        "skills_allowed": "anomaly_only",
        "care_skills_disabled": True,
        "security_level": "monitor_only",
    },
    "sleep": {
        "tts_allowed": False,
        "tts_emergency_only": True,
        "bark_silent": True,
        "skills_allowed": "emergency_only",
        "care_skills_disabled": True,
        "security_level": "enhanced",
    },
    "guest": {
        "tts_allowed": True,
        "tts_emergency_only": False,
        "bark_silent": False,
        "skills_allowed": "all",
        "care_skills_disabled": True,
        "security_level": "monitor_only",
    },
    "away": {
        "tts_allowed": False,
        "tts_emergency_only": True,
        "bark_silent": False,
        "skills_allowed": "none",
        "care_skills_disabled": True,
        "security_level": "full_arm",
    },
}

# 技能类别映射
SKILL_CATEGORIES = {
    "emergency": {"security_alert", "fire_alert", "gas_leak", "intrusion"},
    "anomaly": {"device_inspection", "anomaly_detection", "device_health"},
    "care": {"morning_routine", "sedentary_reminder", "cooking_reminder", "anti_addiction"},
}


# v1.7 P1-3 模式关联动作：切换到该模式时自动执行的 HA 动作
# 注意：这些动作需要实体在白名单中才能执行（白名单机制会拦截非白名单控制）
MODE_ACTIONS: dict[str, list[dict]] = {
    "movie": [
        # 观影模式：调暗客厅主灯（如果有）
        # {"domain": "light", "service": "turn_off", "entity_id": "light.living_room_main"},
        # 实际实体 ID 需要根据 HA 配置调整，这里留空避免误操作
    ],
    "sleep": [
        # 睡眠模式：关闭公共区域灯光
        # {"domain": "light", "service": "turn_off", "entity_id": "light.living_room"},
        # 实际实体 ID 需要根据 HA 配置调整，这里留空避免误操作
    ],
    "away": [
        # 离家模式：关闭所有灯光和空调
        # {"domain": "light", "service": "turn_off"},
        # {"domain": "climate", "service": "turn_off"},
        # 实际实体 ID 需要根据 HA 配置调整，这里留空避免误操作
    ],
    "guest": [],
    "daily": [],
}


@dataclass
class ModeState:
    """模式状态。"""
    mode: str = "daily"
    since: float = 0.0
    source: str = "system"


class ModeEngine:
    """模式引擎：状态机 + 行为规则判断。"""

    def __init__(self, rt=None):
        self.rt = rt
        self._state = ModeState()
        self._load_current()

    def _load_current(self) -> None:
        """从时序数据加载当前模式。"""
        try:
            current = ts.get_current_mode()
            if current and current in MODES:
                self._state.mode = current
                # 找最近的 enter 记录时间
                transitions = ts.get_mode_transitions(mode=current, limit=1)
                if transitions:
                    self._state.since = transitions[0]["ts"]
                    self._state.source = transitions[0].get("source", "system")
                logger.info("mode engine loaded: %s (since %.0f)", current, self._state.since)
            else:
                logger.info("mode engine default: daily")
        except Exception as e:
            logger.warning("mode engine load failed: %s, default daily", e)

    @property
    def current(self) -> str:
        return self._state.mode

    @property
    def current_name(self) -> str:
        return MODE_NAMES.get(self._state.mode, self._state.mode)

    @property
    def rules(self) -> dict:
        return MODE_RULES.get(self._state.mode, MODE_RULES["daily"])

    def switch(self, mode: str, source: str = "system") -> bool:
        """切换模式。返回是否成功切换。"""
        if mode not in MODES:
            raise ValueError(f"invalid mode: {mode}, must be one of {MODES}")

        old_mode = self._state.mode

        # 离家模式优先级最高：如果当前是 away，只有显式切换到其他模式才退出
        # （不自动从 away 切走）

        if old_mode == mode:
            logger.debug("mode already %s, no switch", mode)
            return False

        # 退出旧模式
        if old_mode != "daily":
            ts.record_mode_transition(old_mode, "exit", source=f"{source}:switch_to_{mode}")

        # 进入新模式
        ts.record_mode_transition(mode, "enter", source=source)

        self._state = ModeState(mode=mode, since=time.time(), source=source)
        logger.info("mode switched: %s -> %s (source=%s)", old_mode, mode, source)

        # 模式切换事件（供 trigger 规则层消费）
        if self.rt and hasattr(self.rt, "trigger_engine"):
            try:
                asyncio_run = getattr(self.rt.trigger_engine, "handle_event", None)
                if asyncio_run:
                    # trigger_engine.handle_event 是 async，这里不 await（在同步上下文）
                    import asyncio
                    asyncio.ensure_future(asyncio_run("mode_changed", {
                        "old_mode": old_mode, "new_mode": mode, "source": source,
                    }))
            except Exception as e:
                logger.debug("mode_changed event dispatch failed: %s", e)

        # v1.7 P1-3 智能决策增强：模式切换时自动执行关联动作
        self._apply_mode_actions(mode, old_mode)

        return True

    def _apply_mode_actions(self, new_mode: str, old_mode: str) -> None:
        """模式切换时自动执行关联 HA 动作（异步，不阻塞切换）。"""
        if not self.rt or not hasattr(self.rt, "ha"):
            return

        import asyncio
        actions = MODE_ACTIONS.get(new_mode, [])
        if not actions:
            return

        async def _execute():
            try:
                ha = self.rt.ha
                for action in actions:
                    try:
                        await ha.call_service(
                            domain=action["domain"],
                            service=action["service"],
                            entity_id=action.get("entity_id"),
                            data=action.get("data"),
                        )
                        logger.info("mode action executed: %s.%s -> %s",
                                    action["domain"], action["service"],
                                    action.get("entity_id", "all"))
                    except Exception as e:
                        logger.debug("mode action %s.%s failed: %s",
                                     action["domain"], action["service"], e)
            except Exception as e:
                logger.warning("mode actions execution failed: %s", e)

        try:
            asyncio.ensure_future(_execute())
        except Exception as e:
            logger.debug("schedule mode actions failed: %s", e)

    # ---- 行为规则判断 ----

    def can_tts(self, emergency: bool = False) -> bool:
        """当前模式是否允许 TTS。"""
        rules = self.rules
        if not rules["tts_allowed"]:
            return emergency and rules["tts_emergency_only"]
        return True

    def can_bark(self, silent: bool = False) -> bool:
        """当前模式是否允许 Bark 推送（非静默）。"""
        if self.rules["bark_silent"]:
            return silent  # 静默模式下只允许静默推送
        return True

    def can_run_skill(self, skill_id: str, category: str = None) -> bool:
        """当前模式是否允许执行指定技能。"""
        allowed = self.rules["skills_allowed"]
        skill_cat = category or self._guess_skill_category(skill_id)

        if allowed == "none":
            return False
        if allowed == "emergency_only":
            ok = skill_cat == "emergency"
        elif allowed == "anomaly_only":
            ok = skill_cat in ("emergency", "anomaly")
        else:
            ok = True

        # 第五枚规则键 care_skills_disabled 此前唯一的读者是零调用的 is_care_skill_disabled
        # ⇒「会客/睡眠不推关怀提醒」这句承诺一直没接上（DCD② 判例：假承诺比缺失更坏）。
        if ok and skill_cat == "care" and self.is_care_skill_disabled():
            return False
        return ok

    def is_care_skill_disabled(self) -> bool:
        """关怀类技能是否被禁用。"""
        return self.rules["care_skills_disabled"]

    def _guess_skill_category(self, skill_id: str) -> str:
        """根据技能 ID 猜测类别。"""
        sid = skill_id.lower()
        for cat, ids in SKILL_CATEGORIES.items():
            for kid in ids:
                if kid in sid:
                    return cat
        return "normal"

    # ---- 状态查询 ----

    def get_state(self) -> dict:
        """获取完整模式状态。"""
        return {
            "mode": self._state.mode,
            "mode_name": self.current_name,
            "since": self._state.since,
            "since_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._state.since)) if self._state.since else None,
            "duration_minutes": round((time.time() - self._state.since) / 60, 1) if self._state.since else 0,
            "source": self._state.source,
            "rules": self.rules,
        }

    def get_all_rules(self) -> dict:
        """获取所有模式的行为规则。"""
        return {mode: {"name": MODE_NAMES[mode], **rules} for mode, rules in MODE_RULES.items()}


# ---- 三个入口共用的判定口（DCD 裁定② 20261002-DB六件影子代码-裁定.md:32-42）----
#
# 为什么挂模块级、⛔ 三处各写一遍：fail-open 政策是一份定义（同一判定两腿必须共用 helper），
# 抄三遍下一次改动必漂成三种行为。
# 为什么每次现取、⛔ 构造期注入：app.py:281 建 tts、:316 建 runner，:427 才装 mode_engine
# ⇒ 构造期拿到的必然是 None；现取让「装配顺序」⛔ 决定行为。

def get_mode_engine():
    """现取模式引擎；未装配／取不到⇒None，交由 `_gate` 按 fail-open 放行。"""
    try:
        from butler.runtime import get_runtime
        return getattr(get_runtime(), "mode_engine", None)
    except Exception as e:
        logger.warning("mode engine lookup failed (fail-open): %s: %s", type(e).__name__, e)
        return None


def _gate(judge) -> tuple[bool, str]:
    """唯一的 fail-open 落点，返回 (是否放行, 模式名)。

    DCD② 风险确认原文＝「最容易翻车的不是崩而是『该响的没响』」⇒ 这道门 ⛔ fail-closed：
    判定自身出错一律放行并留 warning（与 integrations/bark.py:89 的 push_guard 同策）。
    模式名只用于日志与失败原因，⛔ 参与判定。
    """
    engine = get_mode_engine()
    if engine is None:
        return True, ""
    try:
        return bool(judge(engine)), engine.current
    except Exception as e:
        logger.warning("mode gate failed (fail-open): %s: %s", type(e).__name__, e)
        return True, getattr(engine, "current", "")


def gate_tts(emergency: bool = False) -> tuple[bool, str]:
    """发声入口＝tts/manager.py 的 speak（全家这张嘴）。"""
    return _gate(lambda eng: eng.can_tts(emergency=emergency))


def gate_bark(silent: bool = False) -> tuple[bool, str]:
    """推送入口＝notify/router.py 的 _to_bark。"""
    return _gate(lambda eng: eng.can_bark(silent=silent))


def gate_skill(skill_id: str, category: str | None = None) -> tuple[bool, str]:
    """技能执行入口＝skills/runner.py 的 run。"""
    return _gate(lambda eng: eng.can_run_skill(skill_id, category))
