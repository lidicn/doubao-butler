"""PWA 对话消息存储：用户 ↔ PM 直接沟通，不经 LLM。"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from butler.config import get_settings
from butler.core import singleton
from butler.logging_setup import get_logger

logger = get_logger("butler.pwa_chat_store")

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    """懒初始化：建连＋建表全收进 `butler.core.singleton` 的双检锁，先跑完 `_init` 再发布 `_conn`。

    同族坏码（D15）＝判定在锁外＋先发布再建表：并发首调各建一条、先建的那条被覆盖后⛔ 关。
    """
    if _conn is not None:
        return _conn                    # 热路径无锁
    return singleton.init(globals(), "_conn", _build_conn)


def close_conn() -> None:
    """释放进程级缓存连接（批186·P1-11）：关停／换 DATA_DIR／测试临时目录里的句柄要收得回来。

    真身在 `butler.core.singleton.release`——与 `get_conn` 共用同一条（模块, 全局名）锁，
    站点⛔ 各写一遍关锁＋置 None。释放后 `get_conn()` 会重建（懒单例语义不变）。
    """
    singleton.release(globals(), "_conn")


def _build_conn() -> sqlite3.Connection:
    s = get_settings()
    Path(s.data_dir).mkdir(parents=True, exist_ok=True)
    db_path = Path(s.data_dir) / "butler.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.row_factory = sqlite3.Row
    try:
        _init(c)                         # ★ 建表跑完之前这条连接不对外可见
    except Exception:
        c.close()                        # ★ 坏连接⛔ 留在全局上、也⛔ 漏句柄
        raise
    logger.info("pwa chat store opened at %s", db_path)
    return c


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
