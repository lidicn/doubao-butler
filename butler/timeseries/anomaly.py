"""实时异常检测引擎。

v1.7 P0-3：对比 MA 作息规律 + 实时数据，实时检测异常并推送。
v1.8.1 修复：CRITICAL_DEVICES 从宽泛子串匹配改为精确 entity_id 白名单，解决大量误报。

检测规则：
  - 到点无活动（工作日 9:00 后用户仍在睡眠模式且无活动）
  - 家电超时运行（洗衣机/空调运行超过 3 小时）
  - 关键设备掉线（路由器/网关/HA 本身掉线）
  - 模式异常（离家模式下检测到人体活动）

去重：同一异常 1 小时内只推送一次。
推送：Bark（默认），critical 级别加小爱 TTS。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from butler.logging_setup import get_logger, warn_throttled
from butler.timeseries import store as ts

logger = get_logger("butler.timeseries.anomaly")

# 去重缓存：{anomaly_key: last_push_ts}
_dedup_cache: dict[str, float] = {}
DEDUP_WINDOW = 3600  # 1 小时

# 紧急开关：异常检测推送总开关（v1.8 已改为汇总推送，重新开启）
ANOMALY_PUSH_ENABLED = True

# 关键设备白名单（掉线触发 critical）
# v1.8.1 修复：从宽泛的子串匹配改为精确 entity_id 白名单
# 之前的 "ha"/"nas" 等短子串导致大量误报（任何包含 ha 的 entity 都被当成关键设备）
CRITICAL_DEVICE_ENTITY_IDS = {
    # 在这里添加真正需要监控的关键设备 entity_id
    # 示例：
    # "sensor.router_status",
    # "binary_sensor.gateway_online",
    # "switch.main",
    # "person.homeassistant",
    # "sensor.nas_cpu",
    # 目前为空：因为之前的白名单太宽泛导致大量误报，先清空，后续按需添加
}

# 只检测这些域的"家电超时运行"（普通开关/插座/灯长期开启是正常的）
APPLIANCE_DOMAINS = {"climate", "fan", "vacuum", "humidifier", "water_heater", "lawn_mower", "siren"}

# 家电超时阈值（分钟）—— 只对 APPLIANCE_DOMAINS 中的设备生效
APPLIANCE_TIMEOUT = {
    "washing_machine": 180,   # 洗衣机 3 小时（通过名称匹配）
    "dishwasher": 240,        # 洗碗机 4 小时
    "dryer": 180,             # 烘干机 3 小时
    "ac": 480,                 # 空调 8 小时
    "fan": 720,                # 风扇 12 小时
    "vacuum": 120,             # 扫地机器人 2 小时
    "water_heater": 120,       # 热水器 2 小时
    "default": 240,            # 默认 4 小时
}

# 排除关键词（这些设备即使在 APPLIANCE_DOMAINS 也不检测超时）
APPLIANCE_EXCLUDE_KEYWORDS = ["指示灯", "led", "indicator", "宽动态", "微光", "摄像机控制"]


@dataclass
class AnomalyEvent:
    """异常事件。"""
    key: str                    # 去重用的唯一键
    entity_id: str              # 关联实体
    anomaly_type: str           # offline/low_battery/abnormal
    severity: str               # info/warning/critical
    message: str                # 人类可读描述
    extra: dict = field(default_factory=dict)


class AnomalyDetector:
    """实时异常检测器。"""

    def __init__(self, rt=None):
        self.rt = rt
        self._last_check = 0.0

    async def check_all(self) -> list[AnomalyEvent]:
        """执行所有检测规则，返回新发现的异常事件。"""
        events = []
        now = time.time()
        self._last_check = now

        try:
            events.extend(await self._check_no_activity())
        except Exception as e:
            logger.warning("check_no_activity failed: %s", e)

        try:
            events.extend(await self._check_appliance_timeout())
        except Exception as e:
            logger.warning("check_appliance_timeout failed: %s", e)

        try:
            events.extend(await self._check_critical_offline())
        except Exception as e:
            logger.warning("check_critical_offline failed: %s", e)

        try:
            events.extend(await self._check_mode_anomaly())
        except Exception as e:
            logger.warning("check_mode_anomaly failed: %s", e)

        # 去重 + 写入存储
        new_events = []
        for ev in events:
            if self._is_duplicate(ev.key):
                continue
            self._mark_pushed(ev.key)
            new_events.append(ev)
            # 写入存储
            try:
                ts.record_device_anomaly(
                    entity_id=ev.entity_id,
                    anomaly_type=ev.anomaly_type,
                    severity=ev.severity,
                    message=ev.message,
                )
            except Exception as e:
                logger.warning("record anomaly failed: %s", e)

        # 汇总推送：所有新异常合并成一条 Bark（v1.8 优化，避免逐条推送骚扰）
        if new_events:
            await self._push_anomaly_summary(new_events)
            logger.info("anomaly check found %d new anomalies, summary pushed", len(new_events))
        return new_events

    # ---- 检测规则 ----

    async def _check_no_activity(self) -> list[AnomalyEvent]:
        """到点无活动检测：工作日 9:00 后用户仍在睡眠模式且无活动。"""
        events = []
        now = time.time()
        hour = time.localtime(now).tm_hour
        weekday = time.localtime(now).tm_wday  # 0=周一, 6=周日

        # 只在工作日 9:00-11:00 检查
        if weekday >= 5 or hour < 9 or hour >= 11:
            return events

        current_mode = ts.get_current_mode()
        if current_mode != "sleep":
            return events

        # 检查定位引擎：lidicn 是否在主卧且无活动
        if self.rt and hasattr(self.rt, "presence_engine"):
            try:
                snapshot = self.rt.presence_engine.snapshot()
                lidicn_loc = snapshot.get("users", {}).get("lidicn", {})
                room = lidicn_loc.get("room", "")
                confidence = lidicn_loc.get("confidence", 0)
                if "主卧" in room and confidence >= 0.6:
                    events.append(AnomalyEvent(
                        key=f"no_activity_lidicn_{time.strftime('%Y%m%d')}",
                        entity_id="person.lidicn",
                        anomaly_type="abnormal",
                        severity="info",
                        message=f"工作日 {hour}:00 后 lidicn 仍在主卧（睡眠模式），可能起床异常，建议关怀提醒。",
                    ))
            except Exception as e:
                logger.debug("presence snapshot failed: %s", e)

        return events

    async def _check_appliance_timeout(self) -> list[AnomalyEvent]:
        """家电超时运行检测：只检测 APPLIANCE_DOMAINS 中的设备。"""
        events = []
        if not self.rt or not getattr(self.rt, "ha", None):
            return events

        try:
            ha = self.rt.ha
            states = await ha.get_states()
            for entity in states:
                eid = entity.get("entity_id", "")
                state = entity.get("state", "")
                if state != "on":
                    continue
                domain = eid.split(".")[0]
                # 只检测家电域（普通开关/插座/灯长期开启是正常的）
                if domain not in APPLIANCE_DOMAINS:
                    continue
                name = entity.get("attributes", {}).get("friendly_name", eid)
                # 排除指示灯等非主要功能
                if any(kw in name.lower() for kw in APPLIANCE_EXCLUDE_KEYWORDS):
                    continue

                last_changed = entity.get("last_changed", "")
                if not last_changed:
                    continue
                try:
                    from datetime import datetime
                    start_dt = datetime.fromisoformat(last_changed.replace("Z", "+00:00"))
                    run_minutes = (time.time() - start_dt.timestamp()) / 60.0
                except Exception:
                    warn_throttled(logger, "anomaly.last_changed", "anomaly 有实体 last_changed 解析不了（该实体跳过）")
                    continue

                # 判断超时阈值
                threshold = APPLIANCE_TIMEOUT.get("default", 240)
                if "洗衣机" in name or "washing" in eid.lower():
                    threshold = APPLIANCE_TIMEOUT["washing_machine"]
                elif "空调" in name or domain == "climate":
                    threshold = APPLIANCE_TIMEOUT["ac"]
                elif "洗碗机" in name:
                    threshold = APPLIANCE_TIMEOUT["dishwasher"]
                elif "烘干机" in name:
                    threshold = APPLIANCE_TIMEOUT["dryer"]
                elif domain == "fan":
                    threshold = APPLIANCE_TIMEOUT["fan"]
                elif domain == "vacuum":
                    threshold = APPLIANCE_TIMEOUT["vacuum"]
                elif domain == "water_heater":
                    threshold = APPLIANCE_TIMEOUT["water_heater"]

                if run_minutes > threshold:
                    events.append(AnomalyEvent(
                        key=f"appliance_timeout_{eid}",
                        entity_id=eid,
                        anomaly_type="abnormal",
                        severity="warning",
                        message=f"{name} 已运行 {int(run_minutes)} 分钟（阈值 {threshold} 分钟），可能异常或忘记关闭。",
                        extra={"run_minutes": run_minutes, "threshold": threshold},
                    ))
        except Exception as e:
            logger.warning("ha get_states failed: %s", e)

        return events

    async def _check_critical_offline(self) -> list[AnomalyEvent]:
        """关键设备掉线检测。

        v1.8.1 修复：从宽泛的子串匹配改为精确 entity_id 白名单匹配。
        之前的 any(kw in eid for kw in CRITICAL_DEVICES) 会导致大量误报。
        """
        events = []
        if not CRITICAL_DEVICE_ENTITY_IDS:
            # 白名单为空时跳过检测（避免误报）
            return events
        if not self.rt or not getattr(self.rt, "ha", None):
            return events

        try:
            ha = self.rt.ha
            states = await ha.get_states()
            for entity in states:
                eid = entity.get("entity_id", "")
                state = entity.get("state", "")
                if state != "unavailable":
                    continue
                # 精确匹配白名单
                if eid not in CRITICAL_DEVICE_ENTITY_IDS:
                    continue
                name = entity.get("attributes", {}).get("friendly_name", eid)
                events.append(AnomalyEvent(
                    key=f"critical_offline_{eid}",
                    entity_id=eid,
                    anomaly_type="offline",
                    severity="critical",
                    message=f"关键设备 {name}（{eid}）已掉线！",
                ))
        except Exception as e:
            logger.warning("critical offline check failed: %s", e)

        return events

    async def _check_mode_anomaly(self) -> list[AnomalyEvent]:
        """模式异常检测：离家模式下检测到人体活动。"""
        events = []
        current_mode = ts.get_current_mode()
        if current_mode != "away":
            return events

        # 检查定位引擎：是否有人在家
        if self.rt and hasattr(self.rt, "presence_engine"):
            try:
                snapshot = self.rt.presence_engine.snapshot()
                for user, loc in snapshot.get("users", {}).items():
                    if loc.get("confidence", 0) >= 0.6 and loc.get("room"):
                        events.append(AnomalyEvent(
                            key=f"mode_anomaly_{user}_{time.strftime('%Y%m%d%H')}",
                            entity_id=f"person.{user}",
                            anomaly_type="abnormal",
                            severity="critical",
                            message=f"离家模式下检测到 {user} 在 {loc.get('room')}（置信度 {loc.get('confidence'):.0%}），可能是入侵或模式误设！",
                        ))
            except Exception as e:
                logger.debug("presence snapshot failed: %s", e)

        return events

    # ---- 去重 ----

    def _is_duplicate(self, key: str) -> bool:
        last = _dedup_cache.get(key, 0)
        return (time.time() - last) < DEDUP_WINDOW

    def _mark_pushed(self, key: str) -> None:
        _dedup_cache[key] = time.time()

    # ---- 推送 ----

    async def _push_anomaly_summary(self, events: list[AnomalyEvent]) -> None:
        """汇总推送：所有新异常合并成一条 Bark，避免逐条推送骚扰。

        v1.8 优化：
          - 标题：【异常检测】发现 N 个问题
          - 正文：按严重度分组，列出每个异常的简要描述
          - 优先级：有 critical 则用 critical，否则 warning
          - 走 PushGuard 风控（Bark.push 已集成风控）
        """
        if not ANOMALY_PUSH_ENABLED:
            logger.info("anomaly push disabled, only logged: %d anomalies", len(events))
            return

        if not events:
            return

        # 按严重度分组
        critical = [e for e in events if e.severity == "critical"]
        warning = [e for e in events if e.severity == "warning"]
        info = [e for e in events if e.severity == "info"]

        # 组装正文
        lines = []
        if critical:
            lines.append(f"🔴 严重({len(critical)})：")
            for e in critical[:5]:
                lines.append(f"  - {e.message}")
            if len(critical) > 5:
                lines.append(f"  ... 还有 {len(critical) - 5} 条")
        if warning:
            lines.append(f"🟡 警告({len(warning)})：")
            for e in warning[:5]:
                lines.append(f"  - {e.message}")
            if len(warning) > 5:
                lines.append(f"  ... 还有 {len(warning) - 5} 条")
        if info:
            lines.append(f"🔵 提示({len(info)})：")
            for e in info[:3]:
                lines.append(f"  - {e.message}")

        body = "\n".join(lines)
        title = f"【异常检测】发现 {len(events)} 个问题"

        # 推送 Bark（走风控）
        try:
            from butler.runtime import get_runtime
            rt = get_runtime()
            bark = getattr(rt, "bark", None)
            if bark:
                priority = "critical" if critical else "warning" if warning else "info"
                await bark.push(
                    body=body,
                    title=title,
                    group="anomaly",
                    priority=priority,
                )
        except Exception as e:
            logger.warning("anomaly summary push failed: %s", e)

        # critical 级别加 TTS（只播第一条摘要）
        if critical and self.rt and hasattr(self.rt, "tts"):
            try:
                first_msg = critical[0].message
                await self.rt.tts.speak(
                    text=f"异常检测：{first_msg}",
                    device_id="xiao_living",  # speak() ⛔ priority 参数，且不指定设备只合成不播
                )
            except Exception as e:
                logger.warning("anomaly critical tts failed: %s", e)
