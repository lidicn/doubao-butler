"""PWA 对话消息存储：用户 ↔ PM 直接沟通，不经 LLM。"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from butler.config import get_settings
from butler.logging_setup import get_logger

logger = get_logger("butler.pwa_chat_store")

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
        logger.info("pwa chat store opened at %s", db_path)
    return _conn


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS pwa_messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            role      TEXT NOT NULL,           -- user | pm
            text      TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pwa_msg_created ON pwa_messages(created_at);
        """
    )


def add_message(role: str, text: str) -> dict:
    c = get_conn()
    now = time.time()
    cur = c.execute(
        "INSERT INTO pwa_messages (role, text, created_at) VALUES (?,?,?)",
        (role, text, now),
    )
    c.commit()
    mid = cur.lastrowid
    logger.info("pwa message added: %s [%s] %s", mid, role, text[:50])
    return get_message(mid)


def get_message(mid: int) -> dict | None:
    c = get_conn()
    row = c.execute("SELECT * FROM pwa_messages WHERE id=?", (mid,)).fetchone()
    return dict(row) if row else None


def list_messages(since_id: int = 0, limit: int = 100) -> list:
    c = get_conn()
    rows = c.execute(
        "SELECT * FROM pwa_messages WHERE id > ? ORDER BY created_at ASC LIMIT ?",
        (since_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_id() -> int:
    c = get_conn()
    row = c.execute("SELECT MAX(id) as mid FROM pwa_messages").fetchone()
    return row["mid"] if row and row["mid"] else 0
