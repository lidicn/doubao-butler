"""科幻级晨起场景。

v1.7 P0-1：检测到用户真正起床后，小爱播报个性化信息。

触发条件（同时满足）：
  1. 睡眠模式已退出（最近一次 sleep exit）
  2. 定位引擎检测到用户离开主卧（或在主卧有活动）
  3. 时间 6:00-10:00
  4. 当天未播报过（去重）

播报内容：
  - 睡眠时长（从时序数据模式切换记录计算）
  - 室内环境（温度/湿度，从 HA）
  - 今日天气（从 HA weather 实体）
  - 设备状态摘要（从时序数据异常事件）
  - 主动问询（可选，需要小爱耳朵配合）

实现方式：APScheduler 每 5 分钟检查一次（6:00-10:00 时段）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from butler.logging_setup import get_logger
from butler.timeseries import store as ts

logger = get_logger("butler.morning")

# 去重：当天是否已播报
_morning_reported_date: str = ""


@dataclass
class MorningContext:
    """晨起播报上下文。"""
    user: str = "lidicn"
    sleep_duration_hours: float = 0.0
    sleep_quality: str = "良好"
    indoor_temp: float | None = None
    indoor_humidity: float | None = None
    weather_text: str = ""
    weather_temp: float | None = None
    device_summary: str = ""
    has_anomalies: bool = False


class MorningRoutine:
    """晨起场景引擎。"""

    def __init__(self, rt=None):
        self.rt = rt

    async def check_and_broadcast(self) -> bool:
        """检查是否满足起床条件，满足则播报。返回是否播报。"""
        global _morning_reported_date

        now = time.time()
        local = time.localtime(now)
        hour = local.tm_hour
        today_str = time.strftime("%Y-%m-%d", local)

        # 只在 6:00-10:00 检查
        if hour < 6 or hour >= 10:
            return False

        # 当天已播报过
        if _morning_reported_date == today_str:
            return False

        # 条件1：睡眠模式已退出（当前不是 sleep 模式，且最近有 sleep exit）
        current_mode = ts.get_current_mode()
        if current_mode == "sleep":
            return False  # 还在睡眠模式

        # 检查最近是否有 sleep exit（在 6:00 之后）
        transitions = ts.get_mode_transitions(mode="sleep", limit=5)
        has_recent_exit = False
        sleep_enter_ts = None
        for t in transitions:
            if t["action"] == "exit" and t["ts"] > now - 4 * 3600:  # 4 小时内退出
                has_recent_exit = True
            if t["action"] == "enter" and sleep_enter_ts is None:
                sleep_enter_ts = t["ts"]
        if not has_recent_exit:
            return False  # 没有最近的睡眠退出，可能不是今天起床

        # 条件2：定位引擎检测到用户不在主卧（或有活动）
        user_left_bedroom = True  # 默认通过（定位引擎可能没有数据）
        if self.rt and hasattr(self.rt, "presence_engine"):
            try:
                snapshot = self.rt.presence_engine.snapshot()
                lidicn_loc = snapshot.get("users", {}).get("lidicn", {})
                room = lidicn_loc.get("room", "")
                confidence = lidicn_loc.get("confidence", 0)
                # 如果 lidicn 仍在主卧且置信度高，可能还没起床
                if "主卧" in room and confidence >= 0.7:
                    user_left_bedroom = False
            except Exception as e:
                logger.debug("presence snapshot failed: %s", e)

        if not user_left_bedroom:
            return False  # 还在主卧，可能没起床

        # 所有条件满足，开始播报
        logger.info("morning routine triggered for %s", today_str)
        _morning_reported_date = today_str

        try:
            ctx = await self._build_context(sleep_enter_ts, transitions)
            await self._broadcast(ctx)
            return True
        except Exception as e:
            logger.warning("morning broadcast failed: %s", e)
            return False

    async def _build_context(self, sleep_enter_ts: float | None,
                               transitions: list[dict]) -> MorningContext:
        """构建播报上下文。"""
        ctx = MorningContext()

        # 睡眠时长
        if sleep_enter_ts:
            # 找对应的 exit 时间
            sleep_exit_ts = None
            for t in transitions:
                if t["action"] == "exit" and t["ts"] > sleep_enter_ts:
                    sleep_exit_ts = t["ts"]
                    break
            if sleep_exit_ts:
                ctx.sleep_duration_hours = round((sleep_exit_ts - sleep_enter_ts) / 3600, 1)
                if ctx.sleep_duration_hours >= 7:
                    ctx.sleep_quality = "良好"
                elif ctx.sleep_duration_hours >= 6:
                    ctx.sleep_quality = "一般"
                else:
                    ctx.sleep_quality = "不足"

        # 室内环境（从 HA）
        if self.rt and getattr(self.rt, "ha", None):
            try:
                ha = self.rt.ha
                states = await ha.get_states()
                for entity in states:
                    eid = entity.get("entity_id", "")
                    state = entity.get("state", "")
                    attrs = entity.get("attributes", {})
                    if eid.startswith("sensor.") and "温度" in attrs.get("friendly_name", ""):
                        try:
                            ctx.indoor_temp = float(state)
                        except (ValueError, TypeError):
                            pass
                    if eid.startswith("sensor.") and "湿度" in attrs.get("friendly_name", ""):
                        try:
                            ctx.indoor_humidity = float(state)
                        except (ValueError, TypeError):
                            pass
                    if eid.startswith("weather."):
                        ctx.weather_text = attrs.get("state", state) or ""
                        ctx.weather_temp = attrs.get("temperature")
            except Exception as e:
                logger.debug("ha states failed: %s", e)

        # 设备异常摘要
        anomalies = ts.get_device_anomalies(resolved=False, limit=5)
        if anomalies:
            ctx.has_anomalies = True
            ctx.device_summary = f"发现 {len(anomalies)} 个未解决设备异常"
        else:
            ctx.device_summary = "所有设备正常"

        return ctx

    async def _broadcast(self, ctx: MorningContext) -> None:
        """播报晨起信息。"""
        lines = []
        lines.append("早上好。")

        if ctx.sleep_duration_hours > 0:
            lines.append(f"您昨晚共睡眠 {ctx.sleep_duration_hours} 小时，睡眠质量{ctx.sleep_quality}。")

        env_parts = []
        if ctx.indoor_temp is not None:
            env_parts.append(f"室内温度 {ctx.indoor_temp}℃")
        if ctx.indoor_humidity is not None:
            env_parts.append(f"湿度 {ctx.indoor_humidity}%")
        if env_parts:
            lines.append("当前" + "，".join(env_parts) + "。")

        if ctx.weather_text:
            weather_line = f"今天天气{ctx.weather_text}"
            if ctx.weather_temp is not None:
                weather_line += f"，气温 {ctx.weather_temp}℃"
            lines.append(weather_line + "。")

        lines.append(f"智能家居系统{ctx.device_summary}。")

        lines.append("新的一天开始了，祝您今天顺利。")

        text = "".join(lines)
        logger.info("morning broadcast: %s", text[:200])

        # 通过 TTS 播报（小爱在主卧/客厅）
        if self.rt and hasattr(self.rt, "tts"):
            try:
                await self.rt.tts.speak(
                    text=text,
                    voice="xiaoyi",  # 小爱音色
                    device_id="xiao_touch8",  # 主卧室音箱
                )
            except Exception as e:
                logger.warning("morning tts failed: %s", e)

        # 同时 Bark 推送（文字版）
        if self.rt and getattr(self.rt, "bark", None):
            try:
                await self.rt.bark.push(
                    body=text,
                    title="【晨起播报】早上好",
                    level="active",
                    group="morning",
                )
            except Exception as e:
                logger.debug("morning bark failed: %s", e)

        # v1.7 P1-6 晨起主动问询：播报后询问是否需要启动晨间服务
        await self._ask_morning_action()

    async def _ask_morning_action(self) -> None:
        """晨起主动问询：询问用户是否需要打开窗帘/启动早餐模式等。"""
        if not self.rt or not hasattr(self.rt, "proactive_engine"):
            return

        try:
            engine = self.rt.proactive_engine
            # 检查当前模式是否允许主动问询（日常模式允许）
            if not engine.is_enabled():
                logger.debug("morning inquiry skipped: proactive disabled by mode")
                return

            inquiry = await engine.ask(
                event_key="morning_action_inquiry",
                title="晨间服务",
                question="需要我为你打开窗帘并启动早餐模式吗？",
                options=["打开窗帘", "启动早餐模式", "都不需要"],
                context={"scene": "morning_routine"},
                tts_device="xiao_touch8",
                cooldown_hours=12,
            )
            if inquiry:
                logger.info("morning inquiry created: %s", inquiry.id)
        except Exception as e:
            logger.warning("morning inquiry failed: %s", e)
