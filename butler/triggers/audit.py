"""触发执行审计（v1.8 P0-6）。

记录所有触发源的执行历史到 SQLite，支持按触发源/时间范围/结果过滤查询。

与 PushGuard 审计日志统一存储在 /app/data/push_guard.db（分表），
但 trigger_audit 表独立，不与 push_audit 混合。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from butler.logging_setup import get_logger

logger = get_logger("butler.triggers.audit")


CALIBER_HEARTBEAT = "schedule_heartbeat"
# 这本账记的是被 wrap_scheduler_job 包起来的定时腿**每轮一行**，与那一轮有没有命中 trigger 无关。
# 「触发/失败多少次」用 butler.db.trigger_runs（repo.trigger_run_stats_since），两本账⛔ 互换——
# 口径见路线图 §14.5（DCD 裁定 A）。
CALIBER_NOTE_HEARTBEAT = (
    "此块＝trigger_audit 调度心跳（定时腿每轮一行，与是否命中 trigger 无关）；"
    "「触发/失败多少次」用 execution 块＝butler.db.trigger_runs；两本账⛔ 互换，见路线图 §14.5。"
    "records 三列计数恒 0（调用点未传，且为进程级累计）⇒ ⛔ 当计数读，见格3 裁定 C。"
)

# 审计表 DDL
_DDL = """
CREATE TABLE IF NOT EXISTS trigger_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    name TEXT,
    event_type TEXT,
    matched_rule TEXT,
    actions_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    result TEXT NOT NULL DEFAULT 'success',  -- success / error
    error TEXT,
    payload TEXT  -- JSON
);
CREATE INDEX IF NOT EXISTS idx_trigger_audit_source ON trigger_audit(source_id);
CREATE INDEX IF NOT EXISTS idx_trigger_audit_ts ON trigger_audit(ts);
CREATE INDEX IF NOT EXISTS idx_trigger_audit_result ON trigger_audit(result);
"""


class TriggerAuditor:
    """触发执行审计器。"""

    def __init__(self, db_path: str = "/app/data/push_guard.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表。"""
        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.executescript(_DDL)
                conn.commit()
            logger.info("trigger_audit table ready at %s", self.db_path)
        except Exception as e:
            logger.warning("trigger_audit init failed: %s", e)

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    def record(self, source_id: str, source_type: str, name: str = "",
               result: str = "success", duration_ms: int = 0,
               error: str = "", event_type: str = "", matched_rule: str = "",
               actions_count: int = 0, success_count: int = 0, fail_count: int = 0,
               payload: dict | None = None) -> None:
        """记一行调度心跳（定时腿每轮一行，⛔ 等于「一次触发执行」）。

        由 TriggerRegistry.mark_success/mark_error 自动调用，
        也可由 TriggerEngine 手动调用记录规则匹配详情。

        三列 actions_count/success_count/fail_count 恒 0：registry 的调用点没传参，
        且它们是进程级累计值（重启归零）——写进流水会把「这一次」变成「截至这一行的累计」，
        ⛔ 拿这三列回答任何计数问题（DCD 裁定 20261001 格3＝C 留着标死）。
        """
        try:
            payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
            with self._get_conn() as conn:
                conn.execute(
                    """INSERT INTO trigger_audit
                       (ts, source_id, source_type, name, event_type, matched_rule,
                        actions_count, success_count, fail_count, duration_ms, result, error, payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (time.time(), source_id, source_type, name, event_type, matched_rule,
                     actions_count, success_count, fail_count, duration_ms, result,
                     error[:500] if error else None, payload_json),
                )
                conn.commit()
        except Exception as e:
            logger.debug("trigger_audit record failed: %s", e)

    def latest_ts(self) -> float:
        """trigger_audit 最后一行的 ts（调度心跳口径的新鲜度读数）。

        表空返回 0.0；读失败**直接抛**（本类里现成的 query()/get_stats() 是"失败返 0/空"
        的形状，那正是一条假绿通道，新读法不再沿用）。
        """
        with self._get_conn() as conn:
            r = conn.execute("SELECT MAX(ts) FROM trigger_audit").fetchone()
        return float(r[0]) if r and r[0] is not None else 0.0

    def query(self, source_id: str | None = None, result: str | None = None,
              since_ts: float | None = None, until_ts: float | None = None,
              limit: int = 100, offset: int = 0) -> list[dict]:
        """查询触发执行审计。"""
        conditions = []
        params: list[Any] = []
        if source_id:
            conditions.append("source_id = ?")
            params.append(source_id)
        if result:
            conditions.append("result = ?")
            params.append(result)
        if since_ts:
            conditions.append("ts >= ?")
            params.append(since_ts)
        if until_ts:
            conditions.append("ts <= ?")
            params.append(until_ts)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"""SELECT id, ts, source_id, source_type, name, event_type, matched_rule,
                         actions_count, success_count, fail_count, duration_ms, result, error, payload
                  FROM trigger_audit{where}
                  ORDER BY ts DESC LIMIT ? OFFSET ?"""
        params.extend([limit, offset])

        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("trigger_audit query failed: %s", e)
            return []

    def get_stats(self, source_id: str | None = None,
                  since_ts: float | None = None) -> dict:
        """调度心跳统计（总行数/成功/失败/平均耗时）。⛔ 拿它回答「触发/失败多少次」。

        数字与改前逐字一致，只多挂 caliber／caliber_note／since_ts 三格：读失败回 0 的那一格
        也要自报口径（纪律 4——一个 0 在被说明它代表什么之前不算读数）。
        """
        conditions = []
        params: list[Any] = []
        if source_id:
            conditions.append("source_id = ?")
            params.append(source_id)
        if since_ts:
            conditions.append("ts >= ?")
            params.append(since_ts)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"""SELECT COUNT(*) as total,
                         SUM(CASE WHEN result='success' THEN 1 ELSE 0 END) as success,
                         SUM(CASE WHEN result='error' THEN 1 ELSE 0 END) as error,
                         AVG(duration_ms) as avg_duration_ms,
                         MAX(duration_ms) as max_duration_ms
                  FROM trigger_audit{where}"""
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(sql, params).fetchone()
                if row:
                    return {
                        "total": row["total"] or 0,
                        "success": row["success"] or 0,
                        "error": row["error"] or 0,
                        "avg_duration_ms": int(row["avg_duration_ms"] or 0),
                        "max_duration_ms": int(row["max_duration_ms"] or 0),
                        "caliber": CALIBER_HEARTBEAT,
                        "caliber_note": CALIBER_NOTE_HEARTBEAT,
                        "since_ts": since_ts,
                    }
        except Exception as e:
            logger.warning("trigger_audit stats failed: %s", e)
        return {
            "total": 0, "success": 0, "error": 0, "avg_duration_ms": 0, "max_duration_ms": 0,
            "caliber": CALIBER_HEARTBEAT,
            "caliber_note": CALIBER_NOTE_HEARTBEAT,
            "since_ts": since_ts,
        }

    def cleanup_old(self, days: int = 30) -> int:
        """清理 N 天前的审计记录。返回删除条数。"""
        cutoff = time.time() - days * 86400
        try:
            with self._get_conn() as conn:
                cursor = conn.execute("DELETE FROM trigger_audit WHERE ts < ?", (cutoff,))
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.warning("trigger_audit cleanup failed: %s", e)
            return 0
