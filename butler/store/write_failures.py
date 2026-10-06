"""写面失败台账（DCD 裁定 20261001-E）。

**为什么是独立库文件**：被监视的对象就是 `butler.db` 自己。2026-09-30 那次写面全线停摆
（`butler.db` 自 08:10:28Z 起再没写进任何东西）而 health / healthy / RestartCount / 日志窗
四格全绿——如果台账写在同一个库里，出事的那一刻恰好就是账本空白的那一刻，等于没有台账。
所以这里落在 data 目录下的**独立文件** `db_write_failures.db`：⛔ 与 butler.db 同库，
⛔ 不用 WAL（WAL 会多两枚 -wal/-shm 边车，取证要三件套一起拷才读得全，反而给"读不到"多造一条路）。

形状（这是留痕链的最后一条腿，它自己不许再把调用方带崩）：

- `record()` **永不抛异常**：写成功返回 True；写失败返回 False，并退到 **error 级日志**——
  日志是台账唯一的备份通道，所以那条备份日志⛔ 用 debug。
- 读侧（`latest_ts` / `count_since` / `count_total`）**读不到就直接抛**：`0` 与"我根本没读到"
  必须分得开（纪律 4：一个 0 在被证明它代表什么之前不算读数）。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from butler.logging_setup import get_logger

logger = get_logger("butler.store.write_failures")

DB_FILENAME = "db_write_failures.db"
TABLE = "db_write_failures"
MAX_ROWS = 5000
_TRIM_EVERY = 200

_DDL = (
    "CREATE TABLE IF NOT EXISTS db_write_failures ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " ts REAL NOT NULL,"
    " site TEXT NOT NULL,"
    " table_name TEXT NOT NULL DEFAULT '',"
    " error TEXT NOT NULL DEFAULT '',"
    " trace_id TEXT NOT NULL DEFAULT '')"
)
_IDX = "CREATE INDEX IF NOT EXISTS idx_dbwf_ts ON db_write_failures (ts)"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_path: str = ""
_writes_since_trim = 0


def db_path() -> Path:
    """台账文件路径（跟随 settings.data_dir，⛔ 硬编码 /app/data）。"""
    from butler.config import get_settings
    return Path(get_settings().data_dir) / DB_FILENAME


def _reset_conn() -> None:
    global _conn, _conn_path
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None
    _conn_path = ""


def _get_conn() -> sqlite3.Connection:
    global _conn, _conn_path
    path = str(db_path())
    if _conn is not None and _conn_path == path:
        return _conn
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, check_same_thread=False, timeout=3.0)
    c.execute("PRAGMA journal_mode=DELETE")
    c.execute("PRAGMA busy_timeout=3000")
    c.execute(_DDL)
    c.execute(_IDX)
    c.commit()
    c.row_factory = sqlite3.Row
    _reset_conn()
    _conn = c
    _conn_path = path
    logger.info("db_write_failures ledger opened at %s", path)
    return _conn


def record(site: str, error: str, *, table: str = "", trace_id: str = "",
           allow_heal: bool = True) -> bool:
    """记一条"某处写库失败"。永不抛：台账 own 失败是它自己的失败，退到 error 日志。

    allow_heal=False 只由 `txn_census.heal_open_transactions` 自己传：它落现场时写的这条
    如果也算 locked，record↔heal 会互相触发（裁 C 护栏 1 的"只从 locked 分支进"必须是**单程**）。
    """
    global _writes_since_trim
    try:
        with _lock:
            c = _get_conn()
            c.execute(
                "INSERT INTO db_write_failures (ts, site, table_name, error, trace_id)"
                " VALUES (?,?,?,?,?)",
                (time.time(), str(site)[:120], str(table)[:60],
                 str(error)[:500], str(trace_id)[:64]),
            )
            _writes_since_trim += 1
            if _writes_since_trim >= _TRIM_EVERY:
                c.execute(
                    "DELETE FROM db_write_failures WHERE id NOT IN"
                    " (SELECT id FROM db_write_failures ORDER BY id DESC LIMIT ?)",
                    (MAX_ROWS,),
                )
                _writes_since_trim = 0
            c.commit()
        if "locked" in str(error).lower():
            # 台账不只记账数，还要点名：谁开着写事务没提交（见 txn_census）
            from butler.store.txn_census import log_snapshot, heal_open_transactions
            log_snapshot(str(site)[:120], exclude_conns=(_conn,))
            # 裁 C（DCD 裁定 20261001 §一-C）：点完名代它回滚，写面立刻放出来。
            # 这是 heal 在生产里唯一的入口（护栏 1）；heal 自己写台账时带 allow_heal=False 回来，
            # 于是这条分支对自愈是单程的——⛔ 定期扫描、⛔ 主动杀。
            if allow_heal:
                heal_open_transactions(str(site)[:120], exclude_conns=(_conn,))
        return True
    except Exception as e:
        logger.error("db_write_failures LEDGER ITSELF FAILED (site=%s): %s: %s",
                     site, type(e).__name__, str(e)[:200])
        return False


def latest_ts() -> float:
    """最后一行时刻；账本为空返回 0.0，**读不到直接抛**（⛔ 把读失败折成 0）。"""
    with _lock:
        r = _get_conn().execute("SELECT MAX(ts) FROM db_write_failures").fetchone()
    return float(r[0]) if r and r[0] is not None else 0.0


def count_since(since_ts: float) -> int:
    with _lock:
        r = _get_conn().execute(
            "SELECT COUNT(*) FROM db_write_failures WHERE ts>=?", (since_ts,)
        ).fetchone()
    return int(r[0])


def count_total() -> int:
    with _lock:
        r = _get_conn().execute("SELECT COUNT(*) FROM db_write_failures").fetchone()
    return int(r[0])


def recent(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        rows = _get_conn().execute(
            "SELECT ts, site, table_name, error, trace_id FROM db_write_failures"
            " ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [dict(r) for r in rows]
