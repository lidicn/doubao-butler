"""时序数据存储：模式切换/设备异常/家电运行时长。

v1.7 P0-2：管家侧只存储需要实时告警的数据，行为习惯学习复用 MA member-schedule。
"""
from __future__ import annotations

import sqlite3
import time
import json
from pathlib import Path
from typing import Any

from butler.config import get_settings
from butler.logging_setup import get_logger

logger = get_logger("butler.timeseries.store")

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
        logger.info("timeseries store opened at %s", db_path)
    return _conn


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        -- 模式切换记录
        CREATE TABLE IF NOT EXISTS ts_mode_transitions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            mode        TEXT NOT NULL,           -- sleep/movie/guest/away/daily
            action      TEXT NOT NULL,           -- enter/exit
            source      TEXT DEFAULT 'system',   -- 触发来源
            ts          REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ts_mode_ts ON ts_mode_transitions(ts);
        CREATE INDEX IF NOT EXISTS idx_ts_mode_mode ON ts_mode_transitions(mode);

        -- 设备异常事件
        CREATE TABLE IF NOT EXISTS ts_device_anomalies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id   TEXT NOT NULL,
            type        TEXT NOT NULL,            -- offline/low_battery/abnormal
            severity    TEXT NOT NULL DEFAULT 'warning',  -- info/warning/critical
            message     TEXT NOT NULL DEFAULT '',
            resolved    INTEGER NOT NULL DEFAULT 0,
            ts          REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ts_anomaly_ts ON ts_device_anomalies(ts);
        CREATE INDEX IF NOT EXISTS idx_ts_anomaly_resolved ON ts_device_anomalies(resolved);

        -- 家电运行时长
        CREATE TABLE IF NOT EXISTS ts_appliance_runtime (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id       TEXT NOT NULL,
            duration_minutes REAL NOT NULL,        -- 本次运行时长（分钟）
            started_at      REAL NOT NULL,
            ended_at        REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ts_runtime_entity ON ts_appliance_runtime(entity_id);
        CREATE INDEX IF NOT EXISTS idx_ts_runtime_started ON ts_appliance_runtime(started_at);
        """
    )


# ---- 模式切换 ----

def record_mode_transition(mode: str, action: str, source: str = "system") -> int:
    """记录模式切换。返回记录 ID。"""
    if action not in ("enter", "exit"):
        raise ValueError("action must be enter or exit")
    c = get_conn()
    now = time.time()
    cur = c.execute(
        "INSERT INTO ts_mode_transitions (mode, action, source, ts) VALUES (?, ?, ?, ?)",
        (mode, action, source, now),
    )
    c.commit()
    logger.info("mode transition: %s %s (source=%s)", mode, action, source)
    return cur.lastrowid


def get_mode_transitions(mode: str = None, limit: int = 100, offset: int = 0) -> list[dict]:
    """查询模式切换历史。"""
    c = get_conn()
    query = "SELECT * FROM ts_mode_transitions WHERE 1=1"
    params = []
    if mode:
        query += " AND mode=?"
        params.append(mode)
    query += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = c.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_current_mode() -> str | None:
    """获取当前模式（最近一次 enter 且未 exit 的模式）。"""
    c = get_conn()
    # 找最近的 enter 记录，然后看它之后有没有对应的 exit
    row = c.execute(
        "SELECT mode, ts FROM ts_mode_transitions WHERE action='enter' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    enter_mode = row["mode"]
    enter_ts = row["ts"]
    # 检查该 enter 之后有没有同模式的 exit
    exit_row = c.execute(
        "SELECT 1 FROM ts_mode_transitions WHERE mode=? AND action='exit' AND ts>? LIMIT 1",
        (enter_mode, enter_ts),
    ).fetchone()
    if exit_row:
        return None  # 已经退出了
    return enter_mode


# ---- 设备异常 ----

def record_device_anomaly(entity_id: str, anomaly_type: str, severity: str = "warning",
                           message: str = "") -> int:
    """记录设备异常事件。返回记录 ID。"""
    if anomaly_type not in ("offline", "low_battery", "abnormal"):
        raise ValueError("type must be offline/low_battery/abnormal")
    if severity not in ("info", "warning", "critical"):
        raise ValueError("severity must be info/warning/critical")
    c = get_conn()
    now = time.time()
    cur = c.execute(
        """INSERT INTO ts_device_anomalies (entity_id, type, severity, message, ts)
           VALUES (?, ?, ?, ?, ?)""",
        (entity_id, anomaly_type, severity, message, now),
    )
    c.commit()
    logger.info("device anomaly: %s %s (%s) - %s", entity_id, anomaly_type, severity, message[:80])
    return cur.lastrowid


def get_device_anomalies(resolved: bool = None, severity: str = None,
                          limit: int = 100, offset: int = 0) -> list[dict]:
    """查询设备异常事件。"""
    c = get_conn()
    query = "SELECT * FROM ts_device_anomalies WHERE 1=1"
    params = []
    if resolved is not None:
        query += " AND resolved=?"
        params.append(1 if resolved else 0)
    if severity:
        query += " AND severity=?"
        params.append(severity)
    query += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = c.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def resolve_anomaly(anomaly_id: int) -> bool:
    """标记异常为已解决。"""
    c = get_conn()
    c.execute("UPDATE ts_device_anomalies SET resolved=1 WHERE id=?", (anomaly_id,))
    c.commit()
    return True


# ---- 家电运行时长 ----

def record_appliance_runtime(entity_id: str, started_at: float, ended_at: float) -> int:
    """记录家电运行时长。返回记录 ID。"""
    duration = max(0.0, (ended_at - started_at) / 60.0)
    c = get_conn()
    cur = c.execute(
        """INSERT INTO ts_appliance_runtime (entity_id, duration_minutes, started_at, ended_at)
           VALUES (?, ?, ?, ?)""",
        (entity_id, duration, started_at, ended_at),
    )
    c.commit()
    logger.info("appliance runtime: %s %.1f min", entity_id, duration)
    return cur.lastrowid


def get_appliance_runtime(entity_id: str, days: int = 7) -> list[dict]:
    """查询家电最近 N 天的运行记录。"""
    c = get_conn()
    cutoff = time.time() - days * 86400
    rows = c.execute(
        "SELECT * FROM ts_appliance_runtime WHERE entity_id=? AND started_at>=? ORDER BY started_at DESC",
        (entity_id, cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


def get_appliance_total_runtime(entity_id: str, days: int = 7) -> float:
    """查询家电最近 N 天的总运行时长（分钟）。"""
    c = get_conn()
    cutoff = time.time() - days * 86400
    row = c.execute(
        "SELECT COALESCE(SUM(duration_minutes), 0) as total FROM ts_appliance_runtime WHERE entity_id=? AND started_at>=?",
        (entity_id, cutoff),
    ).fetchone()
    return row["total"] if row else 0.0
