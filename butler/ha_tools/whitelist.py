"""HA 实体白名单：只允许 agent 控制指定设备。

v1.6 P0-5：
  - 未在白名单的实体只能查询不能控制
  - 跟模式引擎权限控制结合（离家模式只允许安防类设备）
  - 提高安全性，防止 agent 误操作

白名单存储：SQLite 表 ha_entity_whitelist（复用 butler.db）。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from butler.config import get_settings
from butler.logging_setup import get_logger

logger = get_logger("butler.ha_tools.whitelist")

_conn: sqlite3.Connection | None = None

# 安防类设备关键词（离家模式下允许控制）
SECURITY_KEYWORDS = ["camera", "alarm", "siren", "lock", "door", "window", "motion", "安防", "摄像头", "门锁", "门窗", "报警"]


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        s = get_settings()
        Path(s.data_dir).mkdir(parents=True, exist_ok=True)
        db_path = Path(s.data_dir) / "butler.db"
        c = sqlite3.connect(str(db_path), check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        _conn = c
        _init(c)
    return _conn


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS ha_entity_whitelist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id   TEXT NOT NULL UNIQUE,
            name        TEXT DEFAULT '',
            category    TEXT DEFAULT 'general',  -- general/security/climate/media/other
            enabled     INTEGER DEFAULT 1,
            added_at    REAL NOT NULL,
            added_by    TEXT DEFAULT 'admin'
        );
        CREATE INDEX IF NOT EXISTS idx_whitelist_entity ON ha_entity_whitelist(entity_id);
        CREATE INDEX IF NOT EXISTS idx_whitelist_category ON ha_entity_whitelist(category);
        """
    )


class HAWhitelist:
    """HA 实体白名单管理器。"""

    def __init__(self, rt=None):
        self.rt = rt

    def is_empty(self) -> bool:
        """白名单是否为空（未配置时允许所有设备）。"""
        c = get_conn()
        row = c.execute("SELECT COUNT(*) as cnt FROM ha_entity_whitelist WHERE enabled=1").fetchone()
        return (row["cnt"] if row else 0) == 0

    def is_allowed(self, entity_id: str, mode: str = "daily") -> tuple[bool, str]:
        """检查实体是否允许控制。返回 (allowed, reason)。"""
        # 白名单为空 = 未配置，允许所有
        if self.is_empty():
            return True, "whitelist empty, allow all"
        # 离家模式：只允许安防类设备
        if mode == "away":
            if not self._is_security_entity(entity_id):
                return False, "离家模式下只允许控制安防类设备"

        # 检查白名单
        c = get_conn()
        row = c.execute(
            "SELECT * FROM ha_entity_whitelist WHERE entity_id=? AND enabled=1",
            (entity_id,),
        ).fetchone()
        if not row:
            return False, f"实体 {entity_id} 不在白名单中，只允许查询不允许控制"
        return True, "allowed"

    def _is_security_entity(self, entity_id: str) -> bool:
        """判断是否是安防类设备。"""
        eid_lower = entity_id.lower()
        # 先查白名单中的 category
        c = get_conn()
        row = c.execute(
            "SELECT category FROM ha_entity_whitelist WHERE entity_id=? AND enabled=1",
            (entity_id,),
        ).fetchone()
        if row and row["category"] == "security":
            return True
        # 关键词匹配
        return any(kw in eid_lower for kw in SECURITY_KEYWORDS)

    def add(self, entity_id: str, name: str = "", category: str = "general",
            added_by: str = "admin") -> int:
        """添加实体到白名单。"""
        c = get_conn()
        now = time.time()
        try:
            cur = c.execute(
                """INSERT OR REPLACE INTO ha_entity_whitelist (entity_id, name, category, enabled, added_at, added_by)
                   VALUES (?, ?, ?, 1, ?, ?)""",
                (entity_id, name, category, now, added_by),
            )
            c.commit()
            logger.info("whitelist add: %s (%s)", entity_id, category)
            return cur.lastrowid or 0
        except Exception as e:
            logger.warning("whitelist add failed: %s", e)
            return 0

    def remove(self, entity_id: str) -> bool:
        """从白名单移除实体。"""
        c = get_conn()
        c.execute("DELETE FROM ha_entity_whitelist WHERE entity_id=?", (entity_id,))
        c.commit()
        return True

    def set_enabled(self, entity_id: str, enabled: bool) -> bool:
        """启用/禁用白名单实体。"""
        c = get_conn()
        c.execute(
            "UPDATE ha_entity_whitelist SET enabled=? WHERE entity_id=?",
            (1 if enabled else 0, entity_id),
        )
        c.commit()
        return True

    def list(self, category: str = None, enabled: bool = None,
             limit: int = 200, offset: int = 0) -> list[dict]:
        """查询白名单。"""
        c = get_conn()
        query = "SELECT * FROM ha_entity_whitelist WHERE 1=1"
        params = []
        if category:
            query += " AND category=?"
            params.append(category)
        if enabled is not None:
            query += " AND enabled=?"
            params.append(1 if enabled else 0)
        query += " ORDER BY added_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = c.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> dict:
        """统计白名单数量。"""
        c = get_conn()
        total = c.execute("SELECT COUNT(*) as c FROM ha_entity_whitelist").fetchone()["c"]
        enabled = c.execute("SELECT COUNT(*) as c FROM ha_entity_whitelist WHERE enabled=1").fetchone()["c"]
        by_category = {}
        for row in c.execute("SELECT category, COUNT(*) as c FROM ha_entity_whitelist GROUP BY category"):
            by_category[row["category"]] = row["c"]
        return {"total": total, "enabled": enabled, "by_category": by_category}
