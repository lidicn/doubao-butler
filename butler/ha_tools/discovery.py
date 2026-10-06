"""HA 动态实体发现：实体索引 + 按需发现 + 状态查询 + 动作执行。

v1.6 P0-4：借鉴 MCP Assist，不把全量 HA 实体发给 LLM，而是：
  1. 实体索引（区域/域/设备类，~500 tokens）
  2. 按需发现工具（ha_discover_entities）
  3. 状态查询工具（ha_get_states）
  4. 动作执行工具（ha_action）

ReAct 循环中 LLM 先查索引再按需发现，预期 HA 控制相关 token 减少 80-90%。
"""
from __future__ import annotations

import json
import time
from typing import Any

from butler.logging_setup import get_logger

logger = get_logger("butler.ha_tools.discovery")

# 索引缓存（5 分钟过期）
_index_cache: dict[str, Any] = {"data": None, "ts": 0}
INDEX_TTL = 300  # 5 分钟


class HADiscovery:
    """HA 动态实体发现引擎。"""

    def __init__(self, rt=None):
        self.rt = rt

    @property
    def ha(self):
        if self.rt and hasattr(self.rt, "ha"):
            return self.rt.ha
        return None

    # ---- 1. 实体索引 ----

    async def get_entity_index(self) -> dict:
        """生成 HA 实体索引（区域/域/设备类统计，不包含具体实体名）。"""
        cache = _index_cache
        if cache["data"] and (time.time() - cache["ts"]) < INDEX_TTL:
            return cache["data"]

        if not self.ha:
            return {"error": "ha not configured"}

        try:
            states = await self.ha.get_states()
        except Exception as e:
            logger.warning("ha get_states failed: %s", e)
            return {"error": str(e)}

        # 按域统计
        domains: dict[str, int] = {}
        # 按区域统计（从 area_id 或 friendly_name 推断）
        areas: dict[str, int] = {}
        # 按设备类统计（device_class）
        device_classes: dict[str, int] = {}
        # 域内的实体数量
        total = len(states)

        for entity in states:
            eid = entity.get("entity_id", "")
            domain = eid.split(".")[0] if "." in eid else "unknown"
            domains[domain] = domains.get(domain, 0) + 1

            attrs = entity.get("attributes", {})
            # 区域：从 area_id 或 friendly_name 中的房间名推断
            area = attrs.get("area_name") or attrs.get("area_id") or ""
            if area:
                areas[area] = areas.get(area, 0) + 1

            # 设备类
            dc = attrs.get("device_class", "")
            if dc:
                device_classes[dc] = device_classes.get(dc, 0) + 1

        index = {
            "total_entities": total,
            "domains": dict(sorted(domains.items(), key=lambda x: -x[1])),
            "areas": dict(sorted(areas.items(), key=lambda x: -x[1])),
            "device_classes": dict(sorted(device_classes.items(), key=lambda x: -x[1])),
            "generated_at": time.time(),
            "token_estimate": self._estimate_tokens(domains, areas, device_classes),
        }

        cache["data"] = index
        cache["ts"] = time.time()
        return index

    # ---- 2. 按需发现 ----

    async def discover_entities(self, domain: str = None, area: str = None,
                                  device_class: str = None, name_contains: str = None,
                                  state: str = None, limit: int = 50) -> list[dict]:
        """按需发现实体：按条件过滤，只返回匹配的实体（含 entity_id + friendly_name + state）。"""
        if not self.ha:
            return []

        try:
            states = await self.ha.get_states()
        except Exception as e:
            logger.warning("ha get_states failed: %s", e)
            return []

        results = []
        for entity in states:
            eid = entity.get("entity_id", "")
            attrs = entity.get("attributes", {})
            name = attrs.get("friendly_name", eid)

            # 按域过滤
            if domain:
                ent_domain = eid.split(".")[0] if "." in eid else ""
                if ent_domain != domain:
                    continue

            # 按区域过滤
            if area:
                ent_area = attrs.get("area_name") or attrs.get("area_id") or ""
                if area.lower() not in ent_area.lower():
                    continue

            # 按设备类过滤
            if device_class:
                ent_dc = attrs.get("device_class", "")
                if ent_dc != device_class:
                    continue

            # 按名称过滤
            if name_contains:
                if name_contains.lower() not in name.lower() and name_contains.lower() not in eid.lower():
                    continue

            # 按状态过滤
            if state:
                if entity.get("state") != state:
                    continue

            results.append({
                "entity_id": eid,
                "name": name,
                "state": entity.get("state"),
                "domain": eid.split(".")[0] if "." in eid else "",
            })

            if len(results) >= limit:
                break

        return results

    # ---- 3. 状态查询 ----

    async def get_entity_states(self, entity_ids: list[str]) -> list[dict]:
        """查询指定实体的完整状态（含 attributes）。"""
        if not self.ha or not entity_ids:
            return []

        try:
            states = await self.ha.get_states()
        except Exception as e:
            logger.warning("ha get_states failed: %s", e)
            return []

        id_set = set(entity_ids)
        return [e for e in states if e.get("entity_id") in id_set]

    # ---- 4. 动作执行 ----

    async def execute_action(self, entity_id: str, service: str,
                              domain: str = None, data: dict = None) -> dict:
        """执行 HA 服务调用（动作执行）。P0-5 白名单检查。"""
        if not self.ha:
            return {"ok": False, "error": "ha not configured"}

        # P0-5 白名单检查
        whitelist = getattr(self.rt, "ha_whitelist", None) if self.rt else None
        current_mode = "daily"
        if self.rt and hasattr(self.rt, "mode_engine"):
            current_mode = self.rt.mode_engine.current

        if whitelist:
            allowed, reason = whitelist.is_allowed(entity_id, mode=current_mode)
            if not allowed:
                logger.info("ha action blocked by whitelist: %s (%s)", entity_id, reason)
                return {"ok": False, "error": reason, "blocked": True}

        # 推断域
        if not domain:
            domain = entity_id.split(".")[0] if "." in entity_id else ""

        try:
            result = await self.ha.call_service_strict(domain, service, {
                "entity_id": entity_id,
                **(data or {}),
            })
            return {"ok": True, "domain": domain, "service": service, "entity_id": entity_id}
        except Exception as e:
            logger.warning("ha action failed: %s.%s on %s: %s", domain, service, entity_id, e)
            return {"ok": False, "error": str(e)}

    # ---- 工具方法 ----

    def _estimate_tokens(self, domains: dict, areas: dict, device_classes: dict) -> int:
        """估算索引的 token 数量（粗略）。"""
        text = json.dumps({"domains": domains, "areas": areas, "device_classes": device_classes},
                           ensure_ascii=False)
        # 中文约 1.5 token/字，英文约 0.25 token/char
        return int(len(text) * 0.3)
