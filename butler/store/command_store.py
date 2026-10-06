"""统一指令中心（Command Center）：PM/用户创建指令 → TP/DP 拉取执行 → 状态上报。

解决竞态问题：所有指令走同一个通道，不再有模拟键盘注入冲突。
状态流转：pending → accepted → completed / failed / cancelled
"""
from __future__ import annotations

import json
import time
import uuid

from butler.store.db import get_conn
from butler.logging_setup import get_logger

logger = get_logger("butler.store.command")

# 状态常量
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

# 目标
TARGET_TP = "TP"
TARGET_DP = "DP"
TARGET_PM = "PM"
TARGETS = (TARGET_TP, TARGET_DP, TARGET_PM)

# 优先级
PRIORITY_URGENT = "urgent"
PRIORITY_NORMAL = "normal"
PRIORITIES = (PRIORITY_URGENT, PRIORITY_NORMAL)


def init_table() -> None:
    """创建 command_queue 表（幂等）。"""
    c = get_conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS command_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cmd_id      TEXT NOT NULL UNIQUE,       -- 业务ID，如 CMD-20260908-001
            ts          REAL NOT NULL,               -- 创建时间
            target      TEXT NOT NULL,               -- TP / DP / PM
            text        TEXT NOT NULL,               -- 指令内容
            priority    TEXT NOT NULL DEFAULT 'normal',  -- urgent / normal
            status      TEXT NOT NULL DEFAULT 'pending', -- pending/accepted/completed/failed/cancelled
            created_by  TEXT NOT NULL DEFAULT 'PM',  -- 创建者：PM / user / system
            accepted_at REAL,                         -- 接手时间
            completed_at REAL,                        -- 完成时间
            result      TEXT,                         -- 执行结果摘要
            error       TEXT,                         -- 错误信息
            meta_json   TEXT,                         -- 扩展元数据（JSON）
            report_count INTEGER NOT NULL DEFAULT 0   -- 状态上报次数
        );
        CREATE INDEX IF NOT EXISTS idx_cmd_target_status ON command_queue(target, status);
        CREATE INDEX IF NOT EXISTS idx_cmd_status ON command_queue(status);
        CREATE INDEX IF NOT EXISTS idx_cmd_ts ON command_queue(ts);

        CREATE TABLE IF NOT EXISTS command_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cmd_id      TEXT NOT NULL,
            ts          REAL NOT NULL,
            event       TEXT NOT NULL,               -- created / accepted / completed / failed / cancelled / progress
            detail      TEXT,                         -- 事件详情
            reporter    TEXT                          -- 上报者：TP / DP / PM / system
        );
        CREATE INDEX IF NOT EXISTS idx_cmd_events_cmd ON command_events(cmd_id);
        CREATE INDEX IF NOT EXISTS idx_cmd_events_ts ON command_events(ts);
        """
    )
    c.commit()


def _row_to_dict(row) -> dict:
    d = {}
    for k in row.keys():
        d[k] = row[k]
    # id 统一用业务 ID（cmd_id），与 /api/commands/{cmd_id} 路径一致
    if d.get("cmd_id"):
        d["id"] = d["cmd_id"]
    if d.get("meta_json"):
        try:
            d["meta"] = json.loads(d["meta_json"])
        except Exception:
            d["meta"] = {}
    return d


def _add_event(cmd_id: str, event: str, detail: str = "", reporter: str = "system") -> None:
    c = get_conn()
    c.execute(
        "INSERT INTO command_events (cmd_id, ts, event, detail, reporter) VALUES (?,?,?,?,?)",
        (cmd_id, time.time(), event, detail, reporter),
    )
    c.commit()


def create_command(target: str, text: str, *, priority: str = PRIORITY_NORMAL,
                   created_by: str = "PM", meta: dict | None = None) -> dict:
    """创建指令。返回指令 dict。"""
    if target not in TARGETS:
        raise ValueError(f"invalid target: {target}, must be one of {TARGETS}")
    if priority not in PRIORITIES:
        priority = PRIORITY_NORMAL
    cmd_id = f"CMD-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
    c = get_conn()
    c.execute(
        """INSERT INTO command_queue
           (cmd_id, ts, target, text, priority, status, created_by, meta_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (cmd_id, time.time(), target, text, priority, STATUS_PENDING, created_by,
         json.dumps(meta or {}, ensure_ascii=False)),
    )
    c.commit()
    _add_event(cmd_id, "created", f"指令创建：{text[:100]}", created_by)
    logger.info("command created: %s -> %s (%s)", cmd_id, target, text[:50])
    return get_command(cmd_id)


def get_command(cmd_id: str) -> dict | None:
    """按业务 ID 查询指令。"""
    c = get_conn()
    row = c.execute("SELECT * FROM command_queue WHERE cmd_id=?", (cmd_id,)).fetchone()
    return _row_to_dict(row) if row else None


def pull_commands(target: str, *, limit: int = 5) -> list[dict]:
    """TP/DP 拉取新指令（pending 状态，按优先级+时间排序）。

    注意：只返回，不修改状态。TP/DP 接手后需调用 accept_command。
    """
    if target not in TARGETS:
        return []
    c = get_conn()
    rows = c.execute(
        """SELECT * FROM command_queue
           WHERE target=? AND status=?
           ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END, ts ASC
           LIMIT ?""",
        (target, STATUS_PENDING, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def accept_command(cmd_id: str, *, reporter: str = "") -> dict | None:
    """TP/DP 接手指令（pending → accepted）。"""
    c = get_conn()
    row = c.execute("SELECT * FROM command_queue WHERE cmd_id=?", (cmd_id,)).fetchone()
    if not row:
        return None
    if row["status"] != STATUS_PENDING:
        logger.warning("accept command %s: status=%s, not pending", cmd_id, row["status"])
        return _row_to_dict(row)
    c.execute(
        "UPDATE command_queue SET status=?, accepted_at=?, report_count=report_count+1 WHERE cmd_id=?",
        (STATUS_ACCEPTED, time.time(), cmd_id),
    )
    c.commit()
    _add_event(cmd_id, "accepted", f"{reporter or row['target']} 接手指令", reporter or row["target"])
    logger.info("command accepted: %s by %s", cmd_id, reporter or row["target"])
    return get_command(cmd_id)


def complete_command(cmd_id: str, *, result: str = "", reporter: str = "") -> dict | None:
    """TP/DP 完成指令（accepted → completed）。"""
    c = get_conn()
    row = c.execute("SELECT * FROM command_queue WHERE cmd_id=?", (cmd_id,)).fetchone()
    if not row:
        return None
    c.execute(
        """UPDATE command_queue SET status=?, completed_at=?, result=?, report_count=report_count+1
           WHERE cmd_id=?""",
        (STATUS_COMPLETED, time.time(), result[:2000], cmd_id),
    )
    c.commit()
    _add_event(cmd_id, "completed", result[:200] or "指令完成", reporter or row["target"])
    logger.info("command completed: %s", cmd_id)
    return get_command(cmd_id)


def fail_command(cmd_id: str, *, error: str = "", reporter: str = "") -> dict | None:
    """TP/DP 指令执行失败（accepted → failed）。"""
    c = get_conn()
    row = c.execute("SELECT * FROM command_queue WHERE cmd_id=?", (cmd_id,)).fetchone()
    if not row:
        return None
    c.execute(
        """UPDATE command_queue SET status=?, completed_at=?, error=?, report_count=report_count+1
           WHERE cmd_id=?""",
        (STATUS_FAILED, time.time(), error[:2000], cmd_id),
    )
    c.commit()
    _add_event(cmd_id, "failed", error[:200] or "指令失败", reporter or row["target"])
    logger.warning("command failed: %s: %s", cmd_id, error[:100])
    return get_command(cmd_id)


def cancel_command(cmd_id: str, *, reason: str = "") -> dict | None:
    """PM/用户取消指令（pending/accepted → cancelled）。"""
    c = get_conn()
    row = c.execute("SELECT * FROM command_queue WHERE cmd_id=?", (cmd_id,)).fetchone()
    if not row:
        return None
    if row["status"] in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
        return _row_to_dict(row)
    c.execute(
        "UPDATE command_queue SET status=?, completed_at=?, error=?, report_count=report_count+1 WHERE cmd_id=?",
        (STATUS_CANCELLED, time.time(), reason[:500], cmd_id),
    )
    c.commit()
    _add_event(cmd_id, "cancelled", reason or "指令被取消", "PM")
    logger.info("command cancelled: %s: %s", cmd_id, reason[:50])
    return get_command(cmd_id)


def list_commands(*, target: str = "", status: str = "", limit: int = 50, offset: int = 0) -> dict:
    """列出指令，可按目标/状态过滤。"""
    c = get_conn()
    sql = "SELECT * FROM command_queue WHERE 1=1"
    params = []
    if target:
        sql += " AND target=?"
        params.append(target)
    if status:
        sql += " AND status=?"
        params.append(status)
    total = c.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = c.execute(sql, params).fetchall()
    return {"total": total, "items": [_row_to_dict(r) for r in rows], "limit": limit, "offset": offset}


def get_command_events(cmd_id: str, limit: int = 50) -> list[dict]:
    """查询指令的状态流转历史。"""
    c = get_conn()
    rows = c.execute(
        "SELECT * FROM command_events WHERE cmd_id=? ORDER BY ts ASC LIMIT ?",
        (cmd_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_pending_count(target: str = "") -> int:
    """查询待处理指令数量。"""
    c = get_conn()
    if target:
        row = c.execute(
            "SELECT COUNT(*) FROM command_queue WHERE target=? AND status=?",
            (target, STATUS_PENDING),
        ).fetchone()
    else:
        row = c.execute(
            "SELECT COUNT(*) FROM command_queue WHERE status=?", (STATUS_PENDING,)
        ).fetchone()
    return int(row[0] or 0)
