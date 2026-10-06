"""设备别名学习：记录用户原话 → entity_id 映射，下次快速命中。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from threading import Lock

from butler.logging_setup import get_logger

logger = get_logger("butler.core.aliases")

ALIAS_FILE = Path("/app/data/device_aliases.json")
_lock = Lock()


class AliasStore:
    def __init__(self):
        self.aliases: dict[str, dict] = {}  # alias_text -> {entity_id, domain, count, last_used}
        self._load()

    def _load(self):
        if ALIAS_FILE.exists():
            try:
                self.aliases = json.loads(ALIAS_FILE.read_text(encoding="utf-8"))
                logger.info("Loaded %d device aliases", len(self.aliases))
            except Exception as e:
                logger.warning("Failed to load aliases: %s", e)
                self.aliases = {}

    def _save(self):
        ALIAS_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 写 tmp 再 os.replace：就地截断写＝崩溃留半个 JSON，下次 _load 静默把别名库清零（同款正解见 api/deps.py:56-59）。
        tmp = ALIAS_FILE.with_name(ALIAS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(self.aliases, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, ALIAS_FILE)

    def learn(self, user_text: str, entity_id: str, domain: str, service: str):
        """学习一条别名：用户说的话 → 实际操作的 entity_id"""
        # 从用户原话中提取关键词（2-6个汉字的短语）
        keywords = self._extract_keywords(user_text)
        if not keywords:
            return

        with _lock:
            for kw in keywords:
                if kw not in self.aliases:
                    self.aliases[kw] = {
                        "entity_id": entity_id,
                        "domain": domain,
                        "service": service,
                        "count": 1,
                        "examples": [user_text[:50]],
                    }
                else:
                    # 如果同一个关键词映射到不同设备，增加计数但不覆盖
                    existing = self.aliases[kw]
                    if existing["entity_id"] == entity_id:
                        existing["count"] += 1
                    else:
                        # 歧义：记录冲突，不自动更新
                        existing.setdefault("conflicts", []).append({
                            "entity_id": entity_id,
                            "count": 1,
                        })
            self._save()
        logger.info("Learned alias: '%s' -> %s", keywords, entity_id)

    def match(self, text: str) -> dict | None:
        """快速匹配：用户说的话 → 找到对应的设备"""
        for alias, info in self.aliases.items():
            if alias in text and "conflicts" not in info:
                return {
                    "entity_id": info["entity_id"],
                    "domain": info["domain"],
                    "alias": alias,
                }
        return None

    def list_aliases(self) -> list[dict]:
        """列出所有别名"""
        return [
            {"alias": k, **v}
            for k, v in sorted(self.aliases.items(), key=lambda x: -x[1].get("count", 1))
        ]

    def delete_alias(self, alias: str):
        with _lock:
            if alias in self.aliases:
                del self.aliases[alias]
                self._save()

    def _extract_keywords(self, text: str) -> list[str]:
        """从用户原话中提取可能的设备别名关键词"""
        # 去掉常见动词和语气词
        stop_words = {"打开", "关闭", "关掉", "开一下", "关一下", "把", "那个", "这个", "一下", "帮我", "麻烦", "请问", "你好"}
        words = []
        # 提取 2-6 个汉字的连续片段
        for m in re.finditer(r"[\u4e00-\u9fa5]{2,6}", text):
            w = m.group()
            if w not in stop_words and len(w) >= 2:
                words.append(w)
        # 去重，保留最长的
        seen = set()
        result = []
        for w in sorted(words, key=len, reverse=True):
            if w not in seen:
                seen.add(w)
                result.append(w)
        return result[:5]  # 最多取5个关键词


# 全局单例
_alias_store: AliasStore | None = None


def get_alias_store() -> AliasStore:
    global _alias_store
    if _alias_store is None:
        _alias_store = AliasStore()
    return _alias_store
