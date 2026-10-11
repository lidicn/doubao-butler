"""决策请示存储：PM 推送 A/B/C 选项，用户在 PWA 上投票。"""
from __future__ import annotations

import sqlite3
import time
import json
from pathlib import Path

from butler.config import get_settings
from butler.core import atomic_json, singleton
from butler.logging_setup import get_logger

logger = get_logger("butler.decision_store")

VALID_STATUSES = ("待投票", "已投票", "已关闭")

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    """懒初始化：建连＋建表全收进 `butler.core.singleton` 的双检锁，先跑完 `_init` 再发布 `_conn`。

    旧码＝判定在锁外＋`_conn = c` 先发布再 `_init(c)`：并发首调各建一条（先建的那条被覆盖后
    ⛔ 关＝句柄泄漏）、表还没建完的连接已对外可见、`_init` 抛错后坏连接被永久缓存。
    批19 在 `store/db.py` 上钉的同一件事，本批收敛到 helper（D14/D15），站点⛔ 各写一遍双检锁。
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
        _init(c)                         # ★ 建表／迁移跑完之前这条连接不对外可见
    except Exception:
        c.close()                        # ★ 坏连接⛔ 留在全局上、也⛔ 漏句柄
        raise
    logger.info("decision store opened at %s", db_path)
    return c


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id          TEXT PRIMARY KEY,            -- DEC-YYYYMMDD-NNN
            title       TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            options     TEXT NOT NULL,               -- JSON array ["A...","B...","C..."]
            status      TEXT NOT NULL DEFAULT '待投票',
            choice      INTEGER,                      -- 选中的选项索引
            choice_text TEXT,
            callback_url TEXT,                        -- 投票后回调地址（P0-3）
            channel     TEXT NOT NULL DEFAULT 'bark', -- 推送渠道：bark/dashboard/both
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            voted_at    REAL,
            priority    TEXT NOT NULL DEFAULT 'normal' -- normal | urgent
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);
        CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at);
        """
    )
    # 兼容旧表：格2（2026-09-30）只吞「列已存在」，其余 raise（启动期不许静默没迁移）
    from butler.store.ddl import add_column_if_absent
    add_column_if_absent(c, "decisions", "callback_url", "TEXT")
    add_column_if_absent(c, "decisions", "channel", "TEXT NOT NULL DEFAULT 'bark'")
    c.commit()


def _gen_id() -> str:
    now = time.localtime()
    date_str = time.strftime("%Y%m%d", now)
    prefix = f"DEC-{date_str}-"
    c = get_conn()
    row = c.execute("SELECT MAX(CAST(SUBSTR(id, ?) AS INTEGER)) as max_seq FROM decisions WHERE id LIKE ?", (len(prefix)+1, prefix + "%",)).fetchone()
    seq = (row["max_seq"] if row and row["max_seq"] is not None else 0) + 1
    return f"{prefix}{seq:03d}"


def create_decision(title: str, description: str = "", options: list = None,
                    priority: str = "normal", callback_url: str = None,
                    channel: str = "bark") -> dict:
    """创建决策请示。options 是选项列表，如 ["方案A","方案B","方案C"]"""
    if not options or len(options) < 2:
        raise ValueError("至少需要 2 个选项")
    if priority not in ("normal", "urgent"):
        raise ValueError("priority must be normal or urgent")
    if channel not in ("bark", "dashboard", "both"):
        raise ValueError("channel must be bark/dashboard/both")

    dec_id = _gen_id()
    now = time.time()
    c = get_conn()
    c.execute(
        """INSERT INTO decisions (id, title, description, options, status, created_at, updated_at, priority, callback_url, channel)
           VALUES (?, ?, ?, ?, '待投票', ?, ?, ?, ?, ?)""",
        (dec_id, title, description, json.dumps(options, ensure_ascii=False), now, now, priority, callback_url, channel),
    )
    c.commit()
    logger.info("decision created: %s (%s), callback=%s", dec_id, title, callback_url or "none")
    return get_decision(dec_id)


def vote_decision(dec_id: str, choice: int) -> dict:
    """用户投票。choice 是选项索引（从 0 开始）。"""
    c = get_conn()
    dec = get_decision(dec_id)
    if not dec:
        raise KeyError(f"decision not found: {dec_id}")
    if dec["status"] != "待投票":
        raise ValueError(f"decision already {dec['status']}")
    options = dec["options"]
    if choice < 0 or choice >= len(options):
        raise ValueError(f"choice out of range: {choice}, total {len(options)}")

    now = time.time()
    c.execute(
        """UPDATE decisions SET status='已投票', choice=?, choice_text=?, voted_at=?, updated_at=?
           WHERE id=?""",
        (choice, options[choice], now, now, dec_id),
    )
    c.commit()
    logger.info("decision %s voted: %d (%s)", dec_id, choice, options[choice])
    return get_decision(dec_id)


def close_decision(dec_id: str) -> dict:
    """关闭决策（PM 操作）。"""
    c = get_conn()
    now = time.time()
    c.execute("UPDATE decisions SET status='已关闭', updated_at=? WHERE id=?", (now, dec_id))
    c.commit()
    return get_decision(dec_id)


def get_decision(dec_id: str) -> dict | None:
    c = get_conn()
    row = c.execute("SELECT * FROM decisions WHERE id=?", (dec_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["options"] = atomic_json.loads_column(d["options"], [], site="decisions.options")
    return d


def list_decisions(status: str = None, limit: int = 50, offset: int = 0) -> list:
    c = get_conn()
    query = "SELECT * FROM decisions WHERE 1=1"
    params = []
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = c.execute(query, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["options"] = atomic_json.loads_column(d["options"], [], site="decisions.options")
        result.append(d)
    return result


def get_pending_count() -> int:
    """获取待投票的决策数量（用于 PWA 角标）。"""
    c = get_conn()
    row = c.execute("SELECT COUNT(*) as cnt FROM decisions WHERE status='待投票'").fetchone()
    return row["cnt"] if row else 0
