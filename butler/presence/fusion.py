"""加权置信度融合算法。

对每个用户和每个房间，计算加权得分：
  score(u, r) = Σ (weight(signal) * match(signal, u, r))
  confidence(u, r) = sigmoid(score(u, r) - threshold)

最终位置 = argmax_r confidence(u, r)
如果 max confidence < threshold → 位置未知
"""
from __future__ import annotations

import math
import time
from typing import Any

from butler.logging_setup import get_logger

logger = get_logger("butler.presence.fusion")


class FusionEngine:
    """加权置信度融合引擎。"""

    # 降级阈值：MA 信号丢失超过 60 秒触发降级
    DEGRADE_THRESHOLD_SECONDS = 60

    def __init__(self, engine):
        self.engine = engine
        self._degraded = False
        self._last_degrade_log = 0.0

    def _check_degraded(self) -> bool:
        """检查是否处于降级模式（MA 信号丢失超过 60 秒）。

        降级时自动切换权重配置，保证定位不中断。
        """
        ma_source = getattr(self.engine, "_ma_source", None)
        if ma_source is None or not ma_source.enabled:
            # 没有 MA 源，不算降级（本来就只有 HA）
            return False

        last_ts = getattr(ma_source, "_last_ts", 0)
        if last_ts == 0:
            # 从未收到 MA 消息，视为降级
            elapsed = float("inf")
        else:
            elapsed = time.time() - last_ts

        should_degrade = elapsed > self.DEGRADE_THRESHOLD_SECONDS

        # 状态切换时打日志（限流：每 30 秒最多一次）
        if should_degrade != self._degraded:
            now = time.time()
            if now - self._last_degrade_log > 30:
                if should_degrade:
                    logger.warning("MA presence signal lost (%.0fs), degraded to HA-only mode", elapsed)
                else:
                    logger.info("MA presence signal restored, normal mode")
                self._last_degrade_log = now
            self._degraded = should_degrade

        return self._degraded

    def _get_weights(self) -> dict[str, float]:
        """获取当前权重配置（降级模式下自动调整）。"""
        weights = dict(self.engine.config.get("signal_weights", {}))
        if self._check_degraded():
            # 降级模式：提高 HA 信号权重，降低阈值
            weights["occupancy_sensor"] = 0.5
            weights["person_home"] = 0.6
        return weights

    def _get_threshold(self) -> float:
        """获取当前置信度阈值（降级模式下降低）。"""
        base = self.engine.config.get("confidence_threshold", 0.6)
        if self._degraded:
            return 0.45
        return base

    def fuse(self, ha_data: dict[str, Any]) -> dict[str, dict[str, float]]:
        """融合所有信号，返回每个用户在每个房间的置信度。

        Returns:
            {user_id: {room_id: confidence, ...}}
        """
        users = self.engine.get_all_users()
        result: dict[str, dict[str, float]] = {u: {} for u in users}

        occupancy = ha_data.get("occupancy", {})
        external_signals = self.engine._external_signals
        weights = self._get_weights()
        threshold = self._get_threshold()

        for user_id in users:
            scores: dict[str, float] = {}
            contributing: dict[str, list[str]] = {}

            # 1. 外部信号（ArcFace/ESP32/手表定位）
            for sig in external_signals:
                # 只考虑最近 5 分钟的信号
                if time.time() - sig.get("ts", 0) > 300:
                    continue
                if sig.get("user") != user_id:
                    continue
                room = sig.get("room")
                if not room:
                    continue
                weight = sig.get("weight", 0.5) * sig.get("confidence", 1.0)
                scores[room] = scores.get(room, 0) + weight
                if room not in contributing:
                    contributing[room] = []
                contributing[room].append(sig.get("type", "external"))

            # 2. HA person/device home 状态（弱信号，只说明在家，不在具体房间）
            home_status = self._get_home_status(user_id, ha_data)
            person_home_weight = weights.get("person_home", 0.4) * 0.5  # person_home 基础分=权重*0.5
            if home_status == "home":
                # 在家但不知道在哪个房间，给所有有人的房间加基础分
                for room in occupancy:
                    scores[room] = scores.get(room, 0) + person_home_weight
                    if room not in contributing:
                        contributing[room] = []
                    if "person_home" not in contributing[room]:
                        contributing[room].append("person_home")
            elif home_status == "not_home":
                # 明确不在家
                scores["away"] = scores.get("away", 0) + 0.9
                if "away" not in contributing:
                    contributing["away"] = []
                contributing["away"].append("person_not_home")

            # 3. 人在传感器（只知道有人，不知道是谁）
            #    如果用户在家，且只有一个房间有人，则推断用户在那个房间
            occupancy_weight = weights.get("occupancy_sensor", 0.3)
            if home_status == "home" and len(occupancy) == 1:
                room = list(occupancy.keys())[0]
                scores[room] = scores.get(room, 0) + occupancy_weight
                if room not in contributing:
                    contributing[room] = []
                if "occupancy_single" not in contributing[room]:
                    contributing[room].append("occupancy_single")

            # 计算置信度：加权得分直接作为置信度（0-1 范围），超过 1 截断
            for room, score in scores.items():
                confidence = min(1.0, max(0.0, score))
                result[user_id][room] = confidence

            # 如果没有任何信号，置信度全 0
            if not scores:
                result[user_id] = {}

        return result

    def determine_locations(self, fused: dict[str, dict[str, float]]) -> dict[str, tuple[str | None, float, list[str]]]:
        """根据融合结果确定每个用户的最终位置。

        Returns:
            {user_id: (room, confidence, sources)}
        """
        threshold = self._get_threshold()
        result = {}

        for user_id, rooms in fused.items():
            if not rooms:
                result[user_id] = (None, 0.0, [])
                continue

            best_room = max(rooms, key=rooms.get)
            best_conf = rooms[best_room]

            if best_conf < threshold:
                result[user_id] = (None, best_conf, [])
            else:
                sources = self._get_contributing_sources(user_id, best_room)
                result[user_id] = (best_room, best_conf, sources)

        return result

    def _sigmoid(self, x: float) -> float:
        try:
            return 1.0 / (1.0 + math.exp(-x))
        except OverflowError:
            return 0.0 if x < 0 else 1.0

    def _get_home_status(self, user_id: str, ha_data: dict) -> str:
        """从 HA 数据判断用户是否在家。"""
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

    def _get_contributing_sources(self, user_id: str, room: str) -> list[str]:
        """获取对用户在某房间的判断有贡献的信号源。"""
        sources = []
        external_signals = self.engine._external_signals
        for sig in external_signals:
            if time.time() - sig.get("ts", 0) > 300:
                continue
            if sig.get("user") == user_id and sig.get("room") == room:
                stype = sig.get("type", "external")
                if stype not in sources:
                    sources.append(stype)
        return sources
