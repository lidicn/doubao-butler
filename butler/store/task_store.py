"""任务看板存储：PM 调度 TP/DP 的任务状态跟踪（SQLite，零额外依赖）。

状态机：待接手 → 进行中 → 已完成 → 已关闭
TP/DP 通过 POST /api/task/{id}/status 主动上报状态变更。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from butler.config import get_settings
from butler.logging_setup import get_logger

logger = get_logger("butler.task_store")

VALID_STATUSES = ("待接手", "进行中", "已完成", "已关闭")
VALID_PRIORITIES = ("P0", "P1", "P2", "P3")
VALID_TYPES = ("需求", "变更", "问题", "联调")
VALID_ASSIGNEES = ("TP", "DP")

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        s = get_settings()
        Path(s.data_dir).mkdir(parents=True, exist_ok=True)
        db_path = Path(s.data_dir) / "butler.db"
        c = sqlite3.connect(str(db_path), check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.row_factory = sqlite3.Row
        _conn = c
        _init(c)
        logger.info("task store opened at %s (WAL)", db_path)
    return _conn


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id            TEXT PRIMARY KEY,           -- TASK-YYYYMMDD-NNN
            title         TEXT NOT NULL,
            description   TEXT NOT NULL DEFAULT '',
            priority      TEXT NOT NULL DEFAULT 'P2',
            task_type     TEXT NOT NULL DEFAULT '需求',
            assignee      TEXT NOT NULL,              -- TP | DP
            status        TEXT NOT NULL DEFAULT '待接手',
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL,
            started_at    REAL,
            completed_at  REAL,
            handoff_file  TEXT,
            reply_note    TEXT,
            report_count  INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
        CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);

        CREATE TABLE IF NOT EXISTS task_events (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id   TEXT NOT NULL,
            ts        REAL NOT NULL,
            status    TEXT NOT NULL,
            note      TEXT,
            source    TEXT NOT NULL DEFAULT 'report'  -- create | report | pm
        );
        CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, ts);
        """
    )


def _gen_id() -> str:
    """生成任务 ID：TASK-YYYYMMDD-NNN"""
    now = time.localtime()
    date_str = time.strftime("%Y%m%d", now)
    prefix = f"TASK-{date_str}-"
    c = get_conn()
    # R2-09 fix: use MAX(seq)+1 instead of COUNT(*)+1 (deleting a task no longer causes collision)
    # R2-09/N-14: SQLite substr is 1-based; skip prefix (includes trailing '-')
    row = c.execute(
        "SELECT MAX(CAST(substr(id, ?) AS INTEGER)) as max_seq FROM tasks WHERE id LIKE ?",
        (len(prefix) + 1, prefix + "%",)
    ).fetchone()
    seq = (row["max_seq"] if row and row["max_seq"] else 0) + 1
    return f"{prefix}{seq:03d}"


def create_task(
    title: str,
    description: str = "",
    priority: str = "P2",
    task_type: str = "需求",
    assignee: str = "TP",
    handoff_file: str = "",
) -> dict:
    """创建任务（PM 侧调用）。返回任务字典。"""
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority: {priority}")
    if task_type not in VALID_TYPES:
        raise ValueError(f"invalid task_type: {task_type}")
    if assignee not in VALID_ASSIGNEES:
        raise ValueError(f"invalid assignee: {assignee}")

    task_id = _gen_id()
    now = time.time()
    c = get_conn()
    c.execute(
        """INSERT INTO tasks
           (id, title, description, priority, task_type, assignee, status,
            created_at, updated_at, handoff_file, report_count)
           VALUES (?, ?, ?, ?, ?, ?, '待接手', ?, ?, ?, 0)""",
        (task_id, title, description, priority, task_type, assignee,
         now, now, handoff_file),
    )
    c.execute(
        "INSERT INTO task_events (task_id, ts, status, source) VALUES (?, ?, '待接手', 'create')",
        (task_id, now),
    )
    c.commit()
    logger.info("task created: %s (%s, %s)", task_id, assignee, title)
    return get_task(task_id)


def update_status(task_id: str, status: str, note: str = "", source: str = "report") -> dict:
    """更新任务状态（TP/DP 上报或 PM 操作）。"""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")

    c = get_conn()
    task = get_task(task_id)
    if not task:
        raise KeyError(f"task not found: {task_id}")

    now = time.time()
    updates = ["status = ?", "updated_at = ?", "report_count = report_count + 1"]
    params = [status, now]

    if status == "进行中" and not task.get("started_at"):
        updates.append("started_at = ?")
        params.append(now)
    if status == "已完成":
        updates.append("completed_at = ?")
        params.append(now)
    if note:
        updates.append("reply_note = ?")
        params.append(note)

    params.append(task_id)
    c.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params)
    c.execute(
        "INSERT INTO task_events (task_id, ts, status, note, source) VALUES (?, ?, ?, ?, ?)",
        (task_id, now, status, note or None, source),
    )
    c.commit()
    logger.info("task %s -> %s (note=%s)", task_id, status, note[:50] if note else "")
    # 写入通知中心
    try:
        from butler.core import notification_store
        level = "critical" if status == "待接手" else "info"
        notification_store.add_notification(
            ntype="task", level=level,
            title=f"任务{status}：{task.get('title', '')}",
            body=f"负责人：{task.get('assignee', '')} | 优先级：{task.get('priority', '')}" + (f" | {note}" if note else ""),
            ref_id=task_id,
        )
    except Exception as e:
        logger.warning("add notification failed: %s", e)
    return get_task(task_id)


def get_task(task_id: str) -> dict | None:
    """获取单个任务详情。"""
    c = get_conn()
    row = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    # 附加事件历史
    events = c.execute(
        "SELECT ts, status, note, source FROM task_events WHERE task_id = ? ORDER BY ts",
        (task_id,),
    ).fetchall()
    d["events"] = [dict(e) for e in events]
    return d


def list_tasks(
    status: str | None = None,
    assignee: str | None = None,
    priority: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """获取任务列表，支持筛选。"""
    c = get_conn()
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if assignee:
        query += " AND assignee = ?"
        params.append(assignee)
    if priority:
        query += " AND priority = ?"
        params.append(priority)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = c.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_board() -> dict:
    """看板视图：按状态分组的任务统计 + 最近任务。"""
    c = get_conn()
    result: dict = {s: [] for s in VALID_STATUSES}
    stats: dict = {s: 0 for s in VALID_STATUSES}

    for status in VALID_STATUSES:
        rows = c.execute(
            """SELECT id, title, assignee, priority, task_type, created_at,
                      started_at, completed_at, reply_note
               FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT 20""",
            (status,),
        ).fetchall()
        result[status] = [dict(r) for r in rows]
        cnt = c.execute("SELECT COUNT(*) as cnt FROM tasks WHERE status = ?", (status,)).fetchone()
        stats[status] = cnt["cnt"] if cnt else 0

    # 超时检测
    now = time.time()
    overdue_pending = c.execute(
        "SELECT id, title, assignee, created_at FROM tasks WHERE status = '待接手' AND ? - created_at > 1800",
        (now,),
    ).fetchall()
    overdue_active = c.execute(
        "SELECT id, title, assignee, started_at FROM tasks WHERE status = '进行中' AND ? - started_at > 86400",
        (now,),
    ).fetchall()

    return {
        "columns": result,
        "stats": stats,
        "total": sum(stats.values()),
        "overdue": {
            "pending_over_30min": [dict(r) for r in overdue_pending],
            "active_over_24h": [dict(r) for r in overdue_active],
        },
    }


def delete_task(task_id: str) -> bool:
    """删除任务（PM 操作）。"""
    c = get_conn()
    c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    c.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
    c.commit()
    return True
