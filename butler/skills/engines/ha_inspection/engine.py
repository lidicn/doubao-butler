"""HA 设备巡检引擎：查询 Home Assistant 设备状态，检测异常并生成播报文本。

检测项（brain 配置开关）：
  offline:        设备掉线（state=unavailable/unknown）
  low_battery:    低电量（battery_level < threshold，默认 20%）
  empty_room_light: 无人房间开灯（occupancy=off 但 light=on）
  all:            全部检测（默认）

brain 配置：
  checks:         ["offline", "low_battery", "empty_room_light"] 或 "all"
  battery_threshold: 低电量阈值（默认 20）
  ignore_domains: 忽略的域列表（默认 ["automation", "script", "scene", "zone", "person", "sun", "weather"]）
  ignore_entities: 忽略的实体 ID 列表（支持前缀匹配）

输出：巡检报告文本，由 runner 按角色 output_devices 分发（小爱 TTS 播报）。
"""
from __future__ import annotations

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.ha_inspection")

# 默认忽略的域（这些实体的 unavailable 不算掉线）
_DEFAULT_IGNORE_DOMAINS = {
    "automation", "script", "scene", "zone", "person", "sun", "weather",
    "input_boolean", "input_number", "input_text", "input_select", "input_datetime",
    "counter", "timer", "group", "conversation", "persistent_notification",
    "sensor", "binary_sensor", "device_tracker", "button", "event",
    "update", "ai_task", "tts", "stt", "text", "notify", "remote",
    "select", "number", "assist_satellite", "ws_mcp_server",
}

# 掉线检测只检查这些实际设备域
_OFFLINE_CHECK_DOMAINS = {
    "light", "switch", "media_player", "climate", "cover", "fan",
    "vacuum", "lock", "camera", "water_heater", "humidifier", "lawn_mower",
    "valve", "siren", "remote",
}

# 无人开灯检测排除的关键词（传感器指示灯等）
_LIGHT_EXCLUDE_KEYWORDS = ["指示灯", "led", "indicator"]


class HAInspectionEngine:
    async def run(self, ctx: SkillContext) -> SkillResult:
        brain = ctx.skill.get("brain") or {}
        rt = ctx.rt
        if rt is None or getattr(rt, "ha", None) is None:
            return SkillResult(ok=False, error="runtime ha not ready")

        # 解析检测项
        checks = brain.get("checks") or "all"
        if checks == "all":
            checks = ["offline", "low_battery", "empty_room_light"]
        if isinstance(checks, str):
            checks = [checks]

        battery_threshold = int(brain.get("battery_threshold") or 20)
        ignore_domains = set(brain.get("ignore_domains") or _DEFAULT_IGNORE_DOMAINS)
        ignore_entities = brain.get("ignore_entities") or []

        # 获取所有实体状态
        try:
            states = await rt.ha.get_states()
        except Exception as e:
            logger.warning("ha_inspection get_states failed: %s", e)
            return SkillResult(ok=False, error=f"HA 连接失败: {e}")

        if not states:
            return SkillResult(ok=True, text="HA 无设备数据，巡检完成。",
                               meta={"engine": "ha_inspection", "total": 0})

        # 按域分组
        by_domain: dict[str, list[dict]] = {}
        for st in states:
            eid = st.get("entity_id", "")
            domain = eid.split(".")[0] if "." in eid else "unknown"
            if domain in ignore_domains:
                continue
            # 忽略指定实体
            if any(eid.startswith(ie) for ie in ignore_entities):
                continue
            by_domain.setdefault(domain, []).append(st)

        issues: list[str] = []
        details: dict = {"total_entities": len(states), "checked_domains": list(by_domain.keys())}

        # 1. 设备掉线检测（只检查实际设备域）
        if "offline" in checks:
            offline = []
            for domain, entities in by_domain.items():
                if domain in _OFFLINE_CHECK_DOMAINS:
                    for st in entities:
                        state = (st.get("state") or "").lower()
                        if state in ("unavailable", "unknown"):
                            name = (st.get("attributes") or {}).get("friendly_name") or st["entity_id"]
                            offline.append(name)
            if offline:
                issues.append(f"发现 {len(offline)} 个设备掉线：{('、'.join(offline[:5]))}{'等' if len(offline) > 5 else ''}")
                details["offline_count"] = len(offline)
                details["offline_devices"] = offline[:10]

        # 2. 低电量检测（直接从所有 states 查 sensor 域）
        if "low_battery" in checks:
            low_bat = []
            for st in states:
                eid = st.get("entity_id", "")
                if not eid.startswith("sensor."):
                    continue
                attrs = st.get("attributes") or {}
                # 电池传感器：device_class=battery 或 entity_id 含 battery
                is_battery = (attrs.get("device_class") == "battery" or
                              "battery" in eid.lower())
                if is_battery:
                    try:
                        level = float(st.get("state") or 0)
                        if 0 < level < battery_threshold:
                            name = attrs.get("friendly_name") or eid
                            low_bat.append(f"{name}({level:.0f}%)")
                    except (ValueError, TypeError):
                        pass
            if low_bat:
                issues.append(f"发现 {len(low_bat)} 个设备低电量：{('、'.join(low_bat[:5]))}{'等' if len(low_bat) > 5 else ''}")
                details["low_battery_count"] = len(low_bat)
                details["low_battery_devices"] = low_bat[:10]

        # 3. 无人房间开灯检测
        if "empty_room_light" in checks:
            empty_lights = []
            # 获取所有 occupancy 传感器（直接从所有 states 查）
            occupancy: dict[str, str] = {}  # room -> state
            for st in states:
                eid = st.get("entity_id", "")
                if not eid.startswith("binary_sensor."):
                    continue
                attrs = st.get("attributes") or {}
                if attrs.get("device_class") == "occupancy":
                    name = attrs.get("friendly_name") or eid
                    # 从名称提取房间（如 "客厅 人体传感器" -> "客厅"）
                    room = name.split()[0] if name else eid
                    occupancy[room] = (st.get("state") or "").lower()

            # 检查灯（排除传感器指示灯等非照明设备）
            for st in by_domain.get("light", []):
                state = (st.get("state") or "").lower()
                if state != "on":
                    continue
                attrs = st.get("attributes") or {}
                name = attrs.get("friendly_name") or st["entity_id"]
                name_lower = name.lower()
                # 排除指示灯等非照明设备
                if any(kw in name_lower for kw in _LIGHT_EXCLUDE_KEYWORDS):
                    continue
                # 从灯名称提取房间
                room = name.split()[0] if name else ""
                # 如果该房间有 occupancy 传感器且无人
                if room in occupancy and occupancy[room] in ("off", "unavailable"):
                    empty_lights.append(name)

            if empty_lights:
                issues.append(f"发现 {len(empty_lights)} 个无人房间开灯：{('、'.join(empty_lights[:5]))}{'等' if len(empty_lights) > 5 else ''}")
                details["empty_room_light_count"] = len(empty_lights)
                details["empty_room_lights"] = empty_lights[:10]

        # 生成报告文本
        if issues:
            text = "设备巡检发现异常：" + "；".join(issues) + "。请及时处理。"
        else:
            text = "设备巡检完成，所有设备正常，无异常。"

        details["issues_count"] = len(issues)
        details["engine"] = "ha_inspection"

        return SkillResult(ok=True, text=text, meta=details)

    def describe(self) -> dict:
        return {
            "name": "HA 设备巡检",
            "modes": ["offline", "low_battery", "empty_room_light", "all"],
            "desc": "查询 HA 设备状态，检测掉线/低电量/无人开灯，生成播报文本",
        }
