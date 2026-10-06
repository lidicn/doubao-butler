"""设备控制域（从 butler/core/tools.py 拆分）。"""
from __future__ import annotations

import asyncio
import time

from butler.logging_setup import get_logger

logger = get_logger("butler.tools")

async def _resolve_entity(agent, hint: str, domain: str = "") -> str:
    """根据 LLM 给的 entity_id 或中文设备名，从 HA 状态里模糊匹配真实 entity_id。"""
    try:
        states = await agent.ha.get_states()
    except Exception:
        return hint
    if not isinstance(states, list):
        return hint
    # 1. 精确匹配 entity_id
    for s in states:
        if s.get("entity_id") == hint:
            return hint
    # 2. 提取关键词（支持中文设备名和英文 entity_id）
    raw = hint.lower()
    for prefix in ("light.", "switch.", "fan.", "cover.", "climate.", "media_player."):
        raw = raw.replace(prefix, "")
    raw = raw.replace("_", " ").replace("-", " ").strip()
    # 中英文映射表
    en2cn = {
        "monitor": "显示器", "lamp": "灯", "light": "灯", "bar": "挂灯",
        "desk": "书房", "study": "书房", "living": "客厅", "bedroom": "卧室",
        "ceiling": "吸顶", "strip": "灯带", "bulb": "灯泡", "curtain": "窗帘",
        "ac": "空调", "air conditioner": "空调", "fan": "风扇",
    }
    keywords = [raw]
    for en, cn in en2cn.items():
        if en in raw:
            keywords.append(cn)
    # 3. 按 domain 过滤 + 多关键词匹配（任一关键词命中即可）
    candidates = []
    for s in states:
        eid = s.get("entity_id", "")
        if domain and not eid.startswith(domain + "."):
            continue
        fname = (s.get("attributes") or {}).get("friendly_name", "")
        search_blob = (eid + " " + fname).lower()
        score = 0
        for kw in keywords:
            if kw and kw in search_blob:
                score += 1
        if score > 0:
            candidates.append((score, eid, fname))
    # 3b. 指定 domain 没找到时，跨域重搜（LLM 可能传错 domain）
    if not candidates and domain:
        for s in states:
            eid = s.get("entity_id", "")
            fname = (s.get("attributes") or {}).get("friendly_name", "")
            search_blob = (eid + " " + fname).lower()
            score = 0
            for kw in keywords:
                if kw and kw in search_blob:
                    score += 1
            if score > 0:
                candidates.append((score, eid, fname))
                logger.info("_resolve_entity cross-domain match: %s -> %s (score=%d, hint_domain=%s)",
                            hint, eid, score, domain)
    if candidates:
        # 按匹配分数降序，名字长度升序
        candidates.sort(key=lambda x: (-x[0], len(x[2])))
        logger.info("_resolve_entity matched %s -> %s (score=%d)", hint, candidates[0][1], candidates[0][0])
        return candidates[0][1]
    return hint
