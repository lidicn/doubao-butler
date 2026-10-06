"""身份排除推理。

当人在传感器触发但无法识别身份时，使用排除法：
  已知 A 在书房，B 在学校 → 客厅人在传感器触发 → 推断是 C 或访客
"""
from __future__ import annotations

import time
from typing import Any

from butler.logging_setup import get_logger

logger = get_logger("butler.presence.inference")


class InferenceEngine:
    """身份排除推理引擎。"""

    def __init__(self, engine):
        self.engine = engine

    def infer_room_occupants(self, room: str, ha_data: dict,
                              locations: dict[str, tuple]) -> dict[str, Any]:
        """推断某个房间里有哪些人。

        Args:
            room: 房间 ID
            ha_data: HA 数据
            locations: 融合定位结果 {user_id: (room, confidence, sources)}

        Returns:
            {
                "users": ["user_id", ...],       # 确定在该房间的用户
                "possible_users": ["user_id", ...], # 可能在该房间的用户（位置未知但在家）
                "unknown_person": bool,            # 是否有无法识别的人
                "confidence": float,               # 推断置信度
            }
        """
        occupancy = ha_data.get("occupancy", {})
        if room not in occupancy:
            return {"users": [], "possible_users": [], "unknown_person": False, "confidence": 0.0}

        users_in_room = []
        possible_users = []
        users_elsewhere = []
        users_away = []

        for user_id, (loc, conf, _) in locations.items():
            if loc == room and conf >= self.engine.config.get("confidence_threshold", 0.6):
                users_in_room.append(user_id)
            elif loc is not None and loc != room:
                users_elsewhere.append(user_id)
            elif loc == "away":
                users_away.append(user_id)
            else:
                # 位置未知但在家的用户
                home_status = self._check_home(user_id, ha_data)
                if home_status == "home":
                    possible_users.append(user_id)

        # 排除推理：如果房间有人，但已知的用户都不在这个房间
        unknown_person = False
        confidence = 0.0

        if users_in_room:
            # 有明确识别的用户
            confidence = min(1.0, len(users_in_room) * 0.4)
        elif possible_users:
            # 有位置未知但在家的用户，可能是他们
            confidence = 0.5
        elif users_elsewhere or users_away:
            # 所有已知用户都在别处或离家 → 可能是访客
            unknown_person = True
            confidence = 0.6

        return {
            "users": users_in_room,
            "possible_users": possible_users,
            "unknown_person": unknown_person,
            "confidence": round(confidence, 3),
        }

    def _check_home(self, user_id: str, ha_data: dict) -> str:
        device_entities = self.engine.get_user_devices(user_id)
        persons = ha_data.get("persons", {})
        devices = ha_data.get("devices", {})

        for eid in device_entities:
            if eid in persons and persons[eid] == "home":
                return "home"
            if eid in devices and devices[eid] == "home":
                return "home"

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
