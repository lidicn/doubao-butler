"""防重复：bigram Jaccard 相似度，仅比对同一成员 7 天窗口内的回复指纹。"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time

from butler.config import Settings
from butler.logging_setup import get_logger
from butler.store import repo

logger = get_logger("butler.dedup")

_PUNCT = re.compile(r"[\s\W]+")


def bigrams(text: str) -> set[str]:
    s = _PUNCT.sub("", text or "")
    if len(s) <= 1:
        return {s}
    return {s[i : i + 2] for i in range(len(s) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


class DedupChecker:
    def __init__(self, settings: Settings):
        self.s = settings

    async def is_duplicate(self, member: str, text: str) -> tuple[bool, float]:
        bg = bigrams(text)
        since = time.time() - self.s.dedup_window_days * 86400
        fps = await asyncio.to_thread(repo.recent_fingerprints, member, since)
        best = 0.0
        for fp in fps:
            best = max(best, jaccard(bg, fp))
        return best >= self.s.dedup_jaccard_threshold, best

    async def record(self, member: str, text: str) -> None:
        bg = bigrams(text)
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        await asyncio.to_thread(repo.add_fingerprint, member, h, bg)
