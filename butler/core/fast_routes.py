"""Koin 快速路由规则存储。

从 data/fast_routes.json 加载规则，支持运行时添加。
每条规则格式：
{
  "id": "weather_realtime",
  "keywords": ["天气", "气温", "多少度"],
  "exclude_keywords": ["几点", "明天", "后天"],
  "tool": "get_weather",
  "tool_args": {"type": "realtime"},
  "enabled": true,
  "source": "builtin"  # builtin / auto / manual
}
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from butler.core.atomic_json import write_json_atomic
from butler.logging_setup import get_logger

logger = get_logger("butler.core.fast_routes")

ROUTES_FILE = Path("/app/data/fast_routes.json")


class FastRouteStore:
    def __init__(self):
        self.routes: list[dict] = []
        self._load()

    def _load(self):
        if ROUTES_FILE.exists():
            try:
                with open(ROUTES_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    raise ValueError("fast_routes.json 顶层不是数组")
                self.routes = data
            except Exception as e:
                # 表行 94 T-04：回退空列表＝快速路由永久静默失效 ⇒ 兜底成内置规则。
                # ⛔ 在这里 _save()：盖回去＝抹掉现场，人工无从判断丢了哪些用户自加规则。
                logger.error("fast_routes.json unreadable, fallback to builtin rules: %s", e)
                self.routes = self._builtin()
        else:
            self.routes = self._builtin()
            self._save()

    def _save(self):
        ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 表行 127 T-06 的损坏半：就地截断写崩在中途＝整个规则文件只剩半截（性能半另议，见台账 §18 ⛔清单）
        write_json_atomic(ROUTES_FILE, self.routes)

    def _builtin(self) -> list[dict]:
        return [
            {
                "id": "weather_realtime",
                "keywords": ["天气", "气温", "多少度", "温度多少"],
                "exclude_keywords": ["几点", "明天", "后天", "下周", "什么时候"],
                "tool": "get_weather",
                "tool_args": {"type": "realtime"},
                "enabled": True,
                "source": "builtin"
            },
            {
                "id": "weather_rain",
                "keywords": ["会下雨", "下雨吗", "会不会下", "降水概率", "下雨概率"],
                "exclude_keywords": [],
                "tool": "get_weather",
                "tool_args": {"type": "hourly"},
                "enabled": True,
                "source": "builtin"
            },
            {
                "id": "weather_daily",
                "keywords": ["明天天气", "后天天气", "未来天气", "三天天气", "预报"],
                "exclude_keywords": [],
                "tool": "get_weather",
                "tool_args": {"type": "daily"},
                "enabled": True,
                "source": "builtin"
            },
            {
                "id": "capability_discovery",
                "keywords": ["你会做什么", "你能干嘛", "你会什么", "你能做什么", "帮助", "help", "你能帮我", "你能干什么", "有什么功能", "你有什么本事"],
                "exclude_keywords": [],
                "tool": "show_capabilities",
                "tool_args": {},
                "enabled": True,
                "source": "builtin"
            },
            {
                "id": "current_time",
                "keywords": ["几点", "现在时间", "现在几点", "今天几号", "星期几", "今天周几"],
                "exclude_keywords": [],
                "tool": "get_current_time",
                "tool_args": {},
                "enabled": True,
                "source": "builtin"
            },
            {
                "id": "presence",
                "keywords": ["谁在家", "谁在客厅", "凯文在吗", "爱美丽在吗", "家人在哪", "谁回来了"],
                "exclude_keywords": [],
                "tool": "get_presence",
                "tool_args": {},
                "enabled": True,
                "source": "builtin"
            }
        ]

    def match(self, text: str) -> dict | None:
        """匹配文本，返回命中的规则，未命中返回 None。命中后自动统计。"""
        t = text.strip().lower()
        for rule in self.routes:
            if not rule.get("enabled", True):
                continue
            # 排除关键词
            if any(k in t for k in rule.get("exclude_keywords", [])):
                continue
            # 包含任一关键词
            if any(k in t for k in rule.get("keywords", [])):
                # 统计命中
                rule["hits"] = rule.get("hits", 0) + 1
                rule["last_hit"] = time.time()
                self._save()
                return rule
        return None

    def record_fallback(self, route_id: str):
        """记录规则误命中（走了快速路由但又降级到 LLM）。"""
        for r in self.routes:
            if r["id"] == route_id:
                r["fallbacks"] = r.get("fallbacks", 0) + 1
                self._save()
                return

    def add_route(self, route: dict):
        """添加新规则。"""
        route["id"] = f"auto_{int(time.time())}"
        route["source"] = "auto"
        route.setdefault("enabled", True)
        self.routes.append(route)
        self._save()

    def list_routes(self) -> list[dict]:
        return self.routes

    def toggle_route(self, route_id: str, enabled: bool):
        for r in self.routes:
            if r["id"] == route_id:
                r["enabled"] = enabled
                self._save()
                return True
        return False

    def delete_route(self, route_id: str):
        self.routes = [r for r in self.routes if r["id"] != route_id]
        self._save()
