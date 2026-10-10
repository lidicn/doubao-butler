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

输出：巡检报告文本，由 runner 按角色 output_devices 分发（小爱 TTS 播报）；
定时腿（`app.py:_scheduled_device_inspection`）走 Bark。批168 起报告⛔ 再说四种谎：
  · 电量 0%（＝彻底没电）单列一档（旧谓词 `0 < level` 把它整档吞掉）；
  · 电量传感器读不出数当场计数（`sensor` 域在 ignore 名单里，不在这里出声就全屋无人知晓）；
  · 掉线按**设备**归并（一台音箱的多个虚拟开关⛔ 各占一行、占满前 5 个名额）；
  · 房间归属拿不到时写「判断不了」，⛔ 把一个从没跑成的检测算进「正常」。
  文案面向出资人：⛔ 出现「实体／域／occupancy／404／数据面」这类术语。
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
            return SkillResult(
                ok=True,
                text="设备巡检跑不成：HA 一个设备状态都没回，是数据源出了问题，"
                     "不代表家里一切正常。",
                meta={"engine": "ha_inspection", "total": 0, "data_plane_empty": True},
            )

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

        # 1. 设备掉线检测（只检查实际设备域；按设备归并成一行）
        if "offline" in checks:
            offline_groups: dict[str, int] = {}
            offline_entities = 0
            for domain, entities in by_domain.items():
                if domain not in _OFFLINE_CHECK_DOMAINS:
                    continue
                for st in entities:
                    state = (st.get("state") or "").lower()
                    if state not in ("unavailable", "unknown"):
                        continue
                    name = (st.get("attributes") or {}).get("friendly_name") or st["entity_id"]
                    offline_entities += 1
                    # 米家形制＝「设备名␣␣部件名」：一台音箱的多个虚拟开关算同一台设备
                    key = name.split("  ")[0].strip() if "  " in name else name
                    offline_groups[key] = offline_groups.get(key, 0) + 1
            if offline_entities:
                shown = ["%s×%d" % (k, n) if n > 1 else k
                         for k, n in sorted(offline_groups.items(),
                                            key=lambda x: (-x[1], x[0]))]
                issues.append("发现 %d 台设备掉线（合计 %d 处没信号）：%s%s" % (
                    len(offline_groups), offline_entities,
                    "、".join(shown[:5]), "等" if len(shown) > 5 else ""))
                details["offline_count"] = offline_entities
                details["offline_device_groups"] = len(offline_groups)
                details["offline_devices"] = shown[:10]

        # 2. 电量检测（直接从所有 states 查 sensor 域；0%、低电量、取不到读数各一档）
        if "low_battery" in checks:
            dead_bat: list[str] = []
            low_bat: list[str] = []
            unreadable_bat = 0
            for st in states:
                eid = st.get("entity_id", "")
                if not eid.startswith("sensor."):
                    continue
                attrs = st.get("attributes") or {}
                # 电池传感器：device_class=battery 或 entity_id 含 battery
                is_battery = (attrs.get("device_class") == "battery" or
                              "battery" in eid.lower())
                if not is_battery:
                    continue
                raw = (st.get("state") or "").strip()
                if raw == "" or raw.lower() in ("unavailable", "unknown"):
                    # 「读不出来」≠「没有」：这批设备在掉线清单里也是隐身的（sensor 被 ignore）
                    unreadable_bat += 1
                    continue
                try:
                    level = float(raw)
                except (ValueError, TypeError):
                    continue
                name = attrs.get("friendly_name") or eid
                if level <= 0:
                    dead_bat.append("%s(0%%)" % name)
                elif level < battery_threshold:
                    low_bat.append("%s(%.0f%%)" % (name, level))
            if dead_bat:
                issues.append("发现 %d 个设备电量已耗尽（0%%）：%s%s" % (
                    len(dead_bat), "、".join(dead_bat[:5]), "等" if len(dead_bat) > 5 else ""))
                details["dead_battery_count"] = len(dead_bat)
                details["dead_battery_devices"] = dead_bat[:10]
            if low_bat:
                issues.append("发现 %d 个设备低电量：%s%s" % (
                    len(low_bat), "、".join(low_bat[:5]), "等" if len(low_bat) > 5 else ""))
                details["low_battery_count"] = len(low_bat)
                details["low_battery_devices"] = low_bat[:10]
            if unreadable_bat:
                issues.append("另有 %d 个设备的电量读不出来（传感器没上数，不是没电）"
                              % unreadable_bat)
                details["battery_no_reading_count"] = unreadable_bat

        # 3. 无人房间开灯检测（配不上房间的灯＝「无法判定」，⛔ 计入正常）
        if "empty_room_light" in checks:
            empty_lights = []
            unattributed = 0
            # 房间候选：占用传感器名多为「<设备/房间名>[␣部件名]」，两段都留作键
            occupancy: dict[str, str] = {}

            def _remember(room: str, state: str) -> None:
                if not room:
                    return
                if occupancy.get(room) != "on":   # 任一来源说「有人」就算有人
                    occupancy[room] = state

            for st in states:
                eid = st.get("entity_id", "")
                if not eid.startswith("binary_sensor."):
                    continue
                attrs = st.get("attributes") or {}
                if attrs.get("device_class") == "occupancy":
                    name = attrs.get("friendly_name") or eid
                    state = (st.get("state") or "").lower()
                    _remember(name.split()[0] if name.split() else "", state)
                    _remember(name, state)

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
                hit = None
                for room, room_state in occupancy.items():
                    if room in name and (hit is None or len(room) > len(hit[0])):
                        hit = (room, room_state)
                if hit is None:
                    unattributed += 1
                elif hit[1] in ("off", "unavailable"):
                    empty_lights.append(name)

            if empty_lights:
                issues.append("发现 %d 个无人房间开灯：%s%s" % (
                    len(empty_lights), "、".join(empty_lights[:5]),
                    "等" if len(empty_lights) > 5 else ""))
                details["empty_room_light_count"] = len(empty_lights)
                details["empty_room_lights"] = empty_lights[:10]
            if unattributed:
                issues.append("另有 %d 盏开着的灯判断不了房间有没有人"
                              "（传感器名字里没有房间，这条检测在本家跑不成）" % unattributed)
                details["light_room_indeterminate"] = unattributed

        # 生成报告文本
        if issues:
            text = "设备巡检发现异常：" + "；".join(issues) + "。请及时处理。"
        else:
            text = "设备巡检未发现异常。"

        details["issues_count"] = len(issues)
        details["engine"] = "ha_inspection"

        return SkillResult(ok=True, text=text, meta=details)

    def describe(self) -> dict:
        return {
            "name": "HA 设备巡检",
            "modes": ["offline", "low_battery", "empty_room_light", "all"],
            "desc": "查询 HA 设备状态，检测掉线/低电量/无人开灯，生成播报文本",
        }
