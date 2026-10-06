"""感知规则引擎：消费事件流，按预定义场景模板匹配，推断用户意图。

场景模板：
1. 进入书房工作：书房门开→关→灯亮→空调开
2. 人员移动轨迹：人体感应器按房间顺序触发
3. 入睡：卧室门关→灯亮→空调开→灯灭（21:00后）
4. 离家：大门开→关→所有灯灭
5. 回家：大门开→玄关灯亮
6. 早晨起床：主卧灯亮（工作日6:30-7:30）
"""
from __future__ import annotations

import time

from butler.logging_setup import get_logger
from butler.core.event_stream import EventStream

logger = get_logger("butler.core.perception_engine")

# 场景模板定义
SCENE_TEMPLATES = [
    {
        "name": "进入书房工作",
        "description": "书房门开关后开灯开空调",
        "conditions": [
            {"entity_contains": "study_door", "state": "off", "within_sec": 120},
            {"entity_contains": "study_light", "state": "on", "after_prev_sec": 30},
            {"entity_contains": "study_climate", "state": "on", "after_prev_sec": 30},
        ],
        "infer": "user_in_study",
    },
    {
        "name": "入睡",
        "description": "卧室门关后开灯开空调再关灯（晚上）",
        "conditions": [
            {"entity_contains": "bedroom_door", "state": "off", "within_sec": 300, "time_window": "21:00-23:59"},
            {"entity_contains": "bedroom_light", "state": "on", "after_prev_sec": 60},
            {"entity_contains": "bedroom_climate", "state": "on", "after_prev_sec": 60},
            {"entity_contains": "bedroom_light", "state": "off", "after_prev_sec": 120},
        ],
        "infer": "user_asleep",
    },
    {
        "name": "回家",
        "description": "大门开→玄关灯亮",
        "conditions": [
            {"entity_contains": "door", "state": "on", "within_sec": 60},
            {"entity_contains": "entry_light", "state": "on", "after_prev_sec": 30},
        ],
        "infer": "user_home",
    },
    {
        "name": "早晨起床",
        "description": "主卧灯亮（工作日早上）",
        "conditions": [
            {"entity_contains": "master_bedroom_light", "state": "on", "within_sec": 60, "time_window": "06:30-07:30", "weekday_only": True},
        ],
        "infer": "user_wakeup",
    },
]


class PerceptionEngine:
    """感知规则引擎。"""

    def __init__(self, event_stream: EventStream):
        self.es = event_stream
        self._last_match: dict[str, float] = {}  # scene -> last match ts
        self._match_cooldown = 300  # 5分钟冷却，避免重复触发

    def check(self) -> list[dict]:
        """检查当前事件窗口是否匹配任何场景。返回匹配到的场景列表。"""
        events = self.es.get_recent(seconds=300)  # 最近5分钟
        if not events:
            return []

        matches = []
        for template in SCENE_TEMPLATES:
            if self._match_template(template, events):
                name = template["name"]
                now = time.time()
                if now - self._last_match.get(name, 0) > self._match_cooldown:
                    self._last_match[name] = now
                    matches.append({
                        "scene": name,
                        "infer": template["infer"],
                        "ts": now,
                    })
                    logger.info("scene matched: %s -> %s", name, template["infer"])
        return matches

    def _match_template(self, template: dict, events: list[dict]) -> bool:
        """检查事件序列是否匹配场景模板。"""
        conditions = template["conditions"]
        if not conditions:
            return False

        # 按条件顺序检查
        event_idx = 0
        last_ts = 0

        for cond in conditions:
            found = False
            while event_idx < len(events):
                e = events[event_idx]
                event_idx += 1

                # 检查实体匹配
                if cond["entity_contains"] not in e["entity_id"].lower():
                    continue
                # 检查状态
                if e["state"] != cond["state"]:
                    continue

                # 检查时间窗口
                if "after_prev_sec" in cond and last_ts > 0:
                    if e["ts"] - last_ts > cond["after_prev_sec"]:
                        continue
                if "within_sec" in cond and last_ts > 0:
                    if e["ts"] - last_ts > cond["within_sec"]:
                        continue

                last_ts = e["ts"]
                found = True
                break

            if not found:
                return False

        return True
