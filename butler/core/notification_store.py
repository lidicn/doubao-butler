"""通知中心存储：任务变更、决策请求、系统消息统一记录。"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from butler.config import get_settings
from butler.logging_setup import get_logger

logger = get_logger("butler.notification_store")

VALID_TYPES = ("task", "decision", "system", "dev_speak")
VALID_LEVELS = ("info", "warning", "critical")

_conn: sqlite3.Connection | None = None


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
        logger.info("notification store opened at %s", db_path)
    return _conn


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            type      TEXT NOT NULL,           -- task | decision | system | dev_speak
            level     TEXT NOT NULL DEFAULT 'info',
            title     TEXT NOT NULL,
            body      TEXT NOT NULL DEFAULT '',
            ref_id    TEXT,                     -- 关联的任务ID/决策ID
            is_read   INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notif_read ON notifications(is_read);
        CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at);
        """
    )


def add_notification(ntype: str, title: str, body: str = "",
                     level: str = "info", ref_id: str = "") -> dict:
    if ntype not in VALID_TYPES:
        raise ValueError(f"invalid type: {ntype}")
    if level not in VALID_LEVELS:
        raise ValueError(f"invalid level: {level}")
    c = get_conn()
    now = time.time()
    cur = c.execute(
        "INSERT INTO notifications (type, level, title, body, ref_id, created_at) VALUES (?,?,?,?,?,?)",
        (ntype, level, title, body, ref_id, now),
    )
    c.commit()
    nid = cur.lastrowid
    logger.info("notification added: %s [%s] %s", nid, ntype, title)
    return get_notification(nid)


def get_notification(nid: int) -> dict | None:
    c = get_conn()
    row = c.execute("SELECT * FROM notifications WHERE id=?", (nid,)).fetchone()
    return dict(row) if row else None


def list_notifications(limit: int = 50, offset: int = 0, unread_only: bool = False) -> list:
    c = get_conn()
    query = "SELECT * FROM notifications"
    params = []
    if unread_only:
        query += " WHERE is_read=0"
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = c.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def mark_read(nid: int) -> bool:
    c = get_conn()
    c.execute("UPDATE notifications SET is_read=1 WHERE id=?", (nid,))
    c.commit()
    return True


def mark_all_read() -> int:
    c = get_conn()
    cur = c.execute("UPDATE notifications SET is_read=1 WHERE is_read=0")
    c.commit()
    return cur.rowcount


def get_unread_count() -> int:
    c = get_conn()
    row = c.execute("SELECT COUNT(*) as cnt FROM notifications WHERE is_read=0").fetchone()
    return row["cnt"] if row else 0
