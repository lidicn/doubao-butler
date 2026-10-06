"""启动期幂等补列的纪律闸（P0 2026-09-30 格2）。

存在理由：仓里三处 `except sqlite3.OperationalError: pass` 把「库被锁 / 表缺失 /
只读盘」一律读成「列早就加过了」，于是启动宣称成功、迁移其实没发生、写面静默失真。
这里只允许吞 SQLite 对「列已存在」的那一句原话（duplicate column name），其余
OperationalError 原样 raise —— 启动期补列失败必须让启动失败。

table/column/ddl 由调用方以代码字面量传入（不接外部输入），因此不做参数化占位。
"""
from __future__ import annotations

import sqlite3

from butler.logging_setup import get_logger

logger = get_logger("butler.store.ddl")

# SQLite 对「列已存在」的唯一原话，比裸 errno 稳：跨版本不变。
_IDEMPOTENT_MARK = "duplicate column name"


def add_column_if_absent(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
    """幂等补列。True=本次真加了；False=列已存在（唯一允许被吞的情形）。

    其余 OperationalError（database is locked / no such table / attempt to write a
    readonly database 等）一律 raise，不许伪装成「已迁移」。
    """
    stmt = "ALTER TABLE %s ADD COLUMN %s %s" % (table, column, ddl)
    try:
        conn.execute(stmt)
    except sqlite3.OperationalError as exc:
        if _IDEMPOTENT_MARK in str(exc):
            logger.info("ddl: %s.%s already exists, skip", table, column)
            return False
        logger.error("ddl: add column %s.%s failed for a non-idempotent reason: %s",
                     table, column, exc)
        raise
    logger.info("ddl: added column %s.%s", table, column)
    return True
