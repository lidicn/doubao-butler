"""信号源适配器：从 HA 拉取人在传感器/device_tracker/person 数据。"""
from __future__ import annotations

import json
import time
from typing import Any

from butler.integrations.ha import HAClient
from butler.logging_setup import get_logger

logger = get_logger("butler.presence.sources")


class HASource:
    """HA 信号源适配器。

    从 HA 拉取三类数据：
    1. binary_sensor.*_occupancy / *_motion — 人在传感器（只知道有人，不知道是谁）
    2. device_tracker.* — 设备追踪（home/not_home，部分有位置）
    3. person.* — 人员实体（home/not_home）
    """

    def __init__(self, ha: HAClient, engine):
        self.ha = ha
        self.engine = engine
        self._cache: dict[str, Any] = {}
        self._cache_ts: float = 0

    async def fetch(self) -> dict[str, Any]:
        """拉取 HA 状态，返回结构化信号数据。

        Returns:
            {
                "occupancy": {"room_id": [entity_id, ...]},  # 有人的房间
                "persons": {"user_id": "home"|"not_home"|"unknown"},
                "devices": {"entity_id": "home"|"not_home"|"unknown"},
                "all_occupancy": {"entity_id": "on"|"off"},  # 所有人在传感器状态
            }
        """
        now = time.time()
        # 缓存 5 秒，避免频繁调用
        if self._cache and now - self._cache_ts < 5:
            return self._cache

        try:
            states = await self.ha.get_states()
        except Exception as e:
            logger.warning("HA fetch states failed: %s", e)
            return self._cache or {}

        occupancy_active: dict[str, list[str]] = {}
        all_occupancy: dict[str, str] = {}
        persons: dict[str, str] = {}
        devices: dict[str, str] = {}

        for s in states:
            eid = s.get("entity_id", "")
            state = s.get("state", "unknown")

            # 人在/运动传感器
            if eid.startswith("binary_sensor.") and any(
                k in eid for k in ("_occupancy", "_motion", "_presence")
            ):
                all_occupancy[eid] = state
                if state == "on":
                    room = self.engine.get_room_for_sensor(eid)
                    if room:
                        if room not in occupancy_active:
                            occupancy_active[room] = []
                        occupancy_active[room].append(eid)

            # person 实体
            elif eid.startswith("person."):
                persons[eid] = state

            # device_tracker
            elif eid.startswith("device_tracker."):
                devices[eid] = state

        result = {
            "occupancy": occupancy_active,
            "persons": persons,
            "devices": devices,
            "all_occupancy": all_occupancy,
            "ts": now,
        }
        self._cache = result
        self._cache_ts = now
        return result

    def get_user_home_status(self, user_id: str, data: dict) -> str:
        """判断用户是否在家（基于 person + device_tracker）。

        Returns:
            "home" / "not_home" / "unknown"
        """
        device_entities = self.engine.get_user_devices(user_id)
        persons = data.get("persons", {})
        devices = data.get("devices", {})

        for eid in device_entities:
            if eid in persons:
                if persons[eid] == "home":
                    return "home"
            if eid in devices:
                if devices[eid] == "home":
                    return "home"

        # 如果所有关联设备都是 not_home，则判定不在家
        all_not_home = True
        has_any = False
        for eid in device_entities:
            if eid in persons:
                has_any = True
                if persons[eid] != "not_home":
                    all_not_home = False
            if eid in devices:
                has_any = True
                if devices[eid] != "not_home":
                    all_not_home = False

        if has_any and all_not_home:
            return "not_home"
        return "unknown"


class MaPresenceSource:
    """MA ma/presence MQTT 信号源。

    订阅 memory-agent 的 ma/presence 主题，解析 ArcFace 视觉识别结果，
    作为 arcface_recognized 信号上报到定位引擎（权重 1.0，最强信号）。

    MA 推送格式：
        {"members":[{"name":"lidicn","member_id":"lidicn","room":"客厅",
                     "via":"arcface","confidence":0.95,"last_seen":...,"trigger":"face_detected"}],
         "total":1,"ts":"..."}

    用法：
        source = MaPresenceSource(engine)
        # 在 MQTT 消息回调中调用：
        source.handle_message(payload)
    """

    def __init__(self, engine):
        self.engine = engine
        self._last_payload: dict[str, Any] | None = None
        self._last_ts: float = 0
        self._enabled = engine.config.get("ma_presence", {}).get("enabled", True)
        self._signal_type = engine.config.get("ma_presence", {}).get("signal_type", "arcface_recognized")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def handle_message(self, payload: dict[str, Any]) -> int:
        """处理一条 ma/presence MQTT 消息，上报所有成员的位置信号。

        Args:
            payload: MQTT 消息载荷（已解析为 dict）

        Returns:
            成功上报的信号数量
        """
        if not self._enabled:
            return 0
        if not isinstance(payload, dict):
            return 0

        members = payload.get("members", [])
        if not isinstance(members, list):
            return 0

        self._last_payload = payload
        self._last_ts = time.time()

        reported = 0
        for m in members:
            if not isinstance(m, dict):
                continue
            name = (m.get("name") or m.get("member_id") or "").strip()
            room_name = (m.get("room") or "").strip()
            confidence = float(m.get("confidence") or 0.0)
            via = m.get("via", "arcface")

            if not name or not room_name:
                continue

            # 映射中文房间名到管家 room_id
            room_id = self.engine.map_room_name(room_name)
            if not room_id:
                logger.warning("MA presence: unknown room '%s' for user '%s', skipping", room_name, name)
                continue

            # 只接受 arcface 来源的高置信信号（其他来源如 device_tracker 已由 HA 源处理）
            if via != "arcface":
                logger.debug("MA presence: skip non-arcface via=%s for %s", via, name)
                continue

            # 上报信号到引擎
            self.engine.report_signal(
                signal_type=self._signal_type,
                user_id=name,
                room=room_id,
                confidence=min(1.0, max(0.0, confidence)),
                metadata={"via": via, "room_name": room_name, "trigger": m.get("trigger", "")},
            )
            reported += 1

        if reported > 0:
            logger.info("MA presence: reported %d signals from %d members", reported, len(members))
        return reported

    def get_last_payload(self) -> dict[str, Any] | None:
        return self._last_payload

    def get_status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "signal_type": self._signal_type,
            "last_ts": self._last_ts,
            "last_payload": self._last_payload,
        }
