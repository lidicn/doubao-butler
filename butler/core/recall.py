"""v2.6#1 记忆召回链路（决策 3 / G2 降级路径）。

三条腿按序尝试，最差也不崩：
  1) MA 语义检索 retrieve（按当前对话内容）
  2) MA 最近记忆 recall
  3) 管家本地可读副本 memory_facts（只取 approved，pending 未复审不进对话）

每次召回记一条 leg=… 的日志，配合 v2.6 trace 可反查"这句话的记忆是从哪条腿来的"。
"""
from __future__ import annotations

import asyncio

from butler.logging_setup import get_logger

logger = get_logger("butler.core.recall")

LEG_MA_RETRIEVE = "ma_retrieve"
LEG_MA_RECALL = "ma_recall"
LEG_LOCAL = "local_copy"
LEG_NONE = "none"


def local_facts(limit: int = 5) -> list[str]:
    """管家本地可读副本：已审核的家庭事实，新的在前。"""
    try:
        from butler.store import repo
        rows = repo.list_facts(status="approved", limit=max(limit * 6, 30))
    except Exception as e:
        logger.warning("local memory copy read failed: %s", type(e).__name__)
        return []
    out: list[str] = []
    for r in rows:
        content = str(r.get("content") or "").strip()
        if content:
            out.append(content)
        if len(out) >= limit:
            break
    return out


async def recall_facts(memory, query: str = "", member: str = "", limit: int = 5,
                       retrieve_timeout: float = 3.0,
                       recall_timeout: float = 2.0) -> tuple[list[str], str]:
    """返回 (facts, leg)；facts 已按 limit 截断，leg 标明服务来源。"""
    facts: list[str] = []
    leg = LEG_NONE
    if memory is not None:
        try:
            facts = await asyncio.wait_for(
                memory.retrieve(query=query, member=member, limit=limit),
                timeout=retrieve_timeout) or []
            if facts:
                leg = LEG_MA_RETRIEVE
        except Exception as e:
            logger.debug("recall leg ma_retrieve failed: %s", type(e).__name__)
        if not facts:
            try:
                facts = await asyncio.wait_for(
                    memory.recall(member, limit=limit), timeout=recall_timeout) or []
                if facts:
                    leg = LEG_MA_RECALL
            except Exception as e:
                logger.debug("recall leg ma_recall failed: %s", type(e).__name__)
    if not facts and leg == LEG_NONE:
        facts = local_facts(limit)
        if facts:
            leg = LEG_LOCAL
    logger.info("recall served: leg=%s n=%d member=%s", leg, len(facts), member or "-")
    return facts[:limit], leg
