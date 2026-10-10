"""通知中心存储：任务变更、决策请求、系统消息统一记录。"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from butler.config import get_settings
from butler.core import singleton
from butler.logging_setup import get_logger

logger = get_logger("butler.notification_store")

VALID_TYPES = ("task", "decision", "system", "dev_speak")
VALID_LEVELS = ("info", "warning", "critical")

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    """懒初始化：建连＋建表全收进 `butler.core.singleton` 的双检锁，先跑完 `_init` 再发布 `_conn`。

    同族坏码（D15）＝判定在锁外＋先发布再建表：并发首调各建一条、先建的那条被覆盖后⛔ 关。
    判定收敛到 helper（批19 在 `store/db.py` 钉的同一件事），站点⛔ 各写一遍双检锁。
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
    logger.info("notification store opened at %s", db_path)
    return c


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
