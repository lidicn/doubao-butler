"""位置历史存储（SQLite，复用 butler.db）。"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from butler.logging_setup import get_logger

logger = get_logger("butler.presence.store")


class PresenceStore:
    """位置历史存储。

    表结构：
      presence_history(id, user_id, room, confidence, sources, ts)
      presence_snapshots(id, snapshot_json, ts)
    """

    def __init__(self, data_dir: str):
        self.db_path = Path(data_dir) / "butler.db"
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init()
        return self._conn

    def _init(self) -> None:
        c = self._get_conn()
        c.execute("""
            CREATE TABLE IF NOT EXISTS presence_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                room TEXT,
                confidence REAL DEFAULT 0,
                sources TEXT DEFAULT '[]',
                ts REAL NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS presence_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_json TEXT NOT NULL,
                ts REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_presence_history_user ON presence_history(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_presence_history_ts ON presence_history(ts)")
        c.commit()

    def record_location(self, user_id: str, room: str | None,
                        confidence: float, sources: list[str]) -> None:
        """记录一次位置更新。"""
        import json
        c = self._get_conn()
        c.execute(
            "INSERT INTO presence_history (user_id, room, confidence, sources, ts) VALUES (?, ?, ?, ?, ?)",
            (user_id, room, confidence, json.dumps(sources, ensure_ascii=False), time.time()),
        )
        c.commit()

    def save_snapshot(self, snapshot: dict) -> None:
        """保存一次完整快照。"""
        import json
        c = self._get_conn()
        c.execute(
            "INSERT INTO presence_snapshots (snapshot_json, ts) VALUES (?, ?)",
            (json.dumps(snapshot, ensure_ascii=False), time.time()),
        )
        c.commit()

    def get_history(self, user_id: str, limit: int = 50) -> list[dict]:
        """获取用户位置历史。"""
        import json
        c = self._get_conn()
        cur = c.execute(
            "SELECT user_id, room, confidence, sources, ts FROM presence_history WHERE user_id = ? ORDER BY ts DESC LIMIT ?",
            (user_id, limit),
        )
        rows = cur.fetchall()
        result = []
        for row in rows:
            result.append({
                "user_id": row[0],
                "room": row[1],
                "confidence": row[2],
                "sources": json.loads(row[3]) if row[3] else [],
                "ts": row[4],
            })
        return result

    def cleanup_old(self, retention_hours: int = 168) -> int:
        """清理过期历史，返回删除的行数。"""
        cutoff = time.time() - retention_hours * 3600
        c = self._get_conn()
        cur = c.execute("DELETE FROM presence_history WHERE ts < ?", (cutoff,))
        c.execute("DELETE FROM presence_snapshots WHERE ts < ?", (cutoff,))
        c.commit()
        return cur.rowcount
