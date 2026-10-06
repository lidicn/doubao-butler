"""多源融合定位引擎。

从 HA 采集人在传感器/device_tracker/person 数据，加权置信度融合，
输出每个用户的实时位置+置信度，支持身份排除推理。

架构：HA 采数据 → 管家融合推理 → 管家决策执行。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from butler.logging_setup import get_logger

logger = get_logger("butler.presence")


class PresenceEngine:
    """多源融合定位引擎主类。"""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.config_path = self.data_dir / "presence_config.json"
        self.config: dict[str, Any] = {}
        self._users: dict[str, UserPresence] = {}
        self._rooms: dict[str, RoomState] = {}
        self._last_poll: float = 0
        self._external_signals: list[dict] = []  # ArcFace/ESP32 等外部上报信号

    def load_config(self) -> None:
        if self.config_path.exists():
            try:
                self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
                logger.info("presence config loaded: %d rooms, %d sensors, %d users",
                            len(self.config.get("rooms", [])),
                            len(self.config.get("sensor_room_map", {})),
                            len(self.config.get("user_device_map", {})))
            except Exception as e:
                logger.warning("presence config parse failed: %s", e)
                self.config = {}
        else:
            logger.warning("presence config not found at %s", self.config_path)
            self.config = {}

    def get_room_for_sensor(self, entity_id: str) -> str | None:
        """根据 entity_id 查找所属房间。"""
        mapping = self.config.get("sensor_room_map", {})
        # 精确匹配
        if entity_id in mapping:
            return mapping[entity_id]
        # 前缀匹配
        for prefix, room in mapping.items():
            if prefix.startswith("_"):
                continue
            if entity_id.startswith(prefix):
                return room
        return None

    def get_user_devices(self, user_id: str) -> list[str]:
        """获取用户关联的 device_tracker/person entity_id。"""
        val = self.config.get("user_device_map", {}).get(user_id, [])
        if isinstance(val, list):
            return val
        return []

    def get_all_users(self) -> list[str]:
        return [k for k in self.config.get("user_device_map", {}).keys() if not k.startswith("_")]

    def get_room_name(self, room_id: str) -> str:
        for r in self.config.get("rooms", []):
            if r["id"] == room_id:
                return r["name"]
        return room_id

    def map_room_name(self, room_name: str) -> str | None:
        """将 MA 推送的中文房间名映射为管家内部 room_id。

        先查 room_name_map（精确匹配），再查 rooms 的 aliases。
        """
        if not room_name:
            return None
        # 1. room_name_map 精确匹配
        mapping = self.config.get("room_name_map", {})
        if room_name in mapping:
            return mapping[room_name]
        # 2. rooms aliases 匹配
        for r in self.config.get("rooms", []):
            if r.get("name") == room_name:
                return r["id"]
            for alias in r.get("aliases", []):
                if alias == room_name:
                    return r["id"]
        # 3. 如果已经是 room_id，直接返回
        for r in self.config.get("rooms", []):
            if r["id"] == room_name:
                return r["id"]
        return None

    def report_signal(self, signal_type: str, user_id: str, room: str,
                      confidence: float = 1.0, metadata: dict | None = None) -> None:
        """外部信号上报（ArcFace/ESP32 等）。

        Args:
            signal_type: 信号类型（arcface_recognized / esp32_ios_notification / watch_location）
            user_id: 用户 ID
            room: 房间 ID
            confidence: 信号置信度 0-1
            metadata: 附加信息
        """
        weight = self.config.get("signal_weights", {}).get(signal_type, 0.5)
        self._external_signals.append({
            "type": signal_type,
            "user": user_id,
            "room": room,
            "weight": weight,
            "confidence": confidence,
            "ts": time.time(),
            "metadata": metadata or {},
        })
        # 只保留最近 100 条
        self._external_signals = self._external_signals[-100:]
        logger.info("presence signal reported: type=%s user=%s room=%s weight=%.2f",
                    signal_type, user_id, room, weight)

    def get_user_presence(self, user_id: str) -> UserPresence | None:
        return self._users.get(user_id)

    def get_all_presence(self) -> dict[str, UserPresence]:
        return dict(self._users)

    def get_room_state(self, room_id: str) -> RoomState | None:
        return self._rooms.get(room_id)

    def get_all_rooms(self) -> dict[str, RoomState]:
        return dict(self._rooms)

    def snapshot(self) -> dict:
        """返回当前定位快照（用于 API 响应）。"""
        return {
            "users": {uid: u.to_dict() for uid, u in self._users.items()},
            "rooms": {rid: r.to_dict() for rid, r in self._rooms.items()},
            "last_poll": self._last_poll,
            "ts": time.time(),
        }


class UserPresence:
    """用户位置快照。"""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.room: str | None = None
        self.confidence: float = 0.0
        self.sources: list[str] = []
        self.last_seen: float = 0
        self.history: list[dict] = []

    def update(self, room: str, confidence: float, sources: list[str]) -> None:
        if self.room != room:
            self.history.append({
                "room": self.room,
                "confidence": self.confidence,
                "ts": self.last_seen,
            })
            self.history = self.history[-10:]
        self.room = room
        self.confidence = confidence
        self.sources = sources
        self.last_seen = time.time()

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "room": self.room,
            "room_name": self.room,
            "confidence": round(self.confidence, 3),
            "sources": self.sources,
            "last_seen": self.last_seen,
            "history": self.history[-5:],
        }


class RoomState:
    """房间状态。"""

    def __init__(self, room_id: str):
        self.room_id = room_id
        self.occupancy: bool = False
        self.users: list[str] = []
        self.unknown_person: bool = False
        self.last_motion: float = 0
        self.active_sensors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "room_id": self.room_id,
            "occupancy": self.occupancy,
            "users": self.users,
            "unknown_person": self.unknown_person,
            "last_motion": self.last_motion,
            "active_sensors": self.active_sensors,
        }
