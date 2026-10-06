"""Name the connection that is holding a write transaction open (P0 2026-10-01).

写面停摆的机理已被排除法收窄到"本进程内某个 sqlite3.Connection 开了写事务再没提交"：
外部持锁者排除（/proc/locks 的持锁 pid == 容器主进程）、I/O 卡死排除（13 线程无一 D 态）、
显式 BEGIN / isolation_level 全仓 0 命中（⇒ 只能是 python 的隐式事务被漏掉的 commit 悬住）。

但一个进程里有 10 条连接指向 butler.db，谁持有这把锁，进程外面看不见。本模块只做一件事：
把"当前处于事务中"的连接连同它的模块归属／所在栈帧指出来。

⛔ 不在别人的连接上写；除裁 C 那一下 rollback，对别人的连接只发只读 PRAGMA。

裁 C（DCD 裁定 20261001 §一-C）：`heal_open_transactions` 是本模块唯一会**动**别人连接的入口，
而它唯一的调用点是 `write_failures.record()` 的 locked 分支——⛔ 定期扫描、⛔ 主动杀。
"""

from __future__ import annotations

import gc
import sqlite3
import sys

from butler.logging_setup import get_logger

logger = get_logger("butler.store.txn_census")

# 只读，且只对"已经开着事务"的连接执行——那本来就是少数
_DB_LIST = "PRAGMA database_list"


def _module_label(conn: sqlite3.Connection) -> str:
    """该连接挂在哪个模块的哪个全局变量上（纯属性比对，不执行任何外部代码）。"""
    for mod_name in list(sys.modules):
        ns = getattr(sys.modules.get(mod_name), "__dict__", None)
        if not ns:
            continue
        for attr, val in list(ns.items()):
            if val is conn:
                return "%s.%s" % (mod_name, attr)
    return ""


# 本模块自己的帧⛔ 算"现场"：heal/snapshot 的局部变量里就有这条连接（那是探测者，不是持有人）
_CENSUS_FILES = ("txn_census.py", "runner50.py")


def _holder_frame(conn: sqlite3.Connection) -> str:
    """在所有线程的调用栈上找"把这条连接当局部变量握着"的最里层帧。

    ⛔ 用 `gc.get_referrers` 找帧：3.11 的帧把局部变量放在 fast-locals 数组里、不在任何 dict 里，
    连接→帧这条边 gc 看不见（10-02 容器实测：gc 版对帧内连接返回空串）。
    找不到就照实返回 ""——连接只躺在模块全局／实例属性里（现网最常见的形态）时本来就没有持有帧，
    ⛔ 不许为了报表好看编一个。
    """
    try:
        frames = list(sys._current_frames().values())
    except Exception as exc:
        return "unreadable(%s)" % type(exc).__name__
    for top in frames:
        f, depth = top, 0
        while f is not None and depth < 64:
            code = f.f_code
            if not any(code.co_filename.endswith(n) for n in _CENSUS_FILES):
                try:
                    for val in f.f_locals.values():
                        if val is conn:
                            return "%s:%s@%d" % (code.co_filename, code.co_name, f.f_lineno)
                except Exception:
                    pass            # 取局部变量本身可能触发 __delitem__ 类钩子，⛔ 带崩点名
            f, depth = f.f_back, depth + 1
    return ""


def _referrer_kinds(conn: sqlite3.Connection) -> str:
    """这条连接**被什么类型的事物**指着（module dict / 帧 / 实例 dict / 其它）。

    只是形状线索，⛔ 归因结论：归因看 label（模块全局名）＋ holder_frame（在栈上的帧）。
    """
    kinds = []
    try:
        refs = gc.get_referrers(conn)
    except Exception as exc:
        return "unreadable(%s)" % type(exc).__name__
    for ref in refs:
        if isinstance(ref, dict):
            owner = ref.get("__name__")
            kinds.append("module:%s" % owner if isinstance(owner, str) else "dict")
        elif getattr(ref, "f_code", None) is not None:
            kinds.append("frame")
        else:
            kinds.append(type(ref).__name__)
    return "|".join(sorted(set(kinds))[:6])


def _files(conn: sqlite3.Connection) -> str:
    try:
        rows = conn.execute(_DB_LIST).fetchall()
    except Exception as exc:
        return "unreadable(%s)" % type(exc).__name__
    return "|".join(str(r[2] or "") for r in rows)


def snapshot(exclude_ids: tuple = ()) -> dict:
    """返回 {"open": [每个未提交事务的连接一行], "unreadable": 读不动的Connection数}。

    绝不抛：单条连接的探测失败只让它自己那一行变成 error/unreadable。
    """
    open_rows = []
    unreadable = 0
    for obj in gc.get_objects():
        if not isinstance(obj, sqlite3.Connection) or id(obj) in exclude_ids:
            continue
        try:
            open_txn = obj.in_transaction
        except Exception:
            # 已 close 的连接不可能持锁，只计数，不算持锁者
            unreadable += 1
            continue
        if not open_txn:
            continue
        open_rows.append({
            "label": _module_label(obj),
            "holder_frame": _holder_frame(obj),
            "files": _files(obj),
            "isolation_level": repr(obj.isolation_level),
        })
    return {"open": open_rows, "unreadable": unreadable}


def log_snapshot(site: str, exclude_conns: tuple = ()) -> dict:
    """失败现场打一行可 grep 的点名；返回同 snapshot。"""
    data = {"open": [], "unreadable": 0}
    try:
        data = snapshot(tuple(id(c) for c in exclude_conns if c is not None))
        if not data["open"]:
            logger.warning("txn-census site=%s open_txn_connections=0 unreadable=%d "
                           "(锁不在本进程的未提交事务里)",
                           site, data["unreadable"])
        for r in data["open"]:
            logger.warning("txn-census site=%s holder label=%s frame=%s files=%s "
                           "isolation=%s",
                           site, r["label"] or "-", r["holder_frame"] or "-",
                           r["files"] or "-", r["isolation_level"] or "-")
    except Exception as exc:
        logger.warning("txn-census site=%s census failed: %s: %s",
                       site, type(exc).__name__, str(exc)[:160])
    return data


def heal_open_transactions(site: str, exclude_conns: tuple = ()) -> dict:
    """裁 C（DCD 裁定 20261001 §一-C）：代杀"开着写事务却一直没提交"的连接，把写面放出来。

    四条护栏，逐条在 tests/test_txn_self_heal.py 有腿：
      1. 只有 `write_failures.record()` 的 locked 分支会调到这里（⛔ 定期扫描、⛔ 主动杀）；
      2. 只 ROLLBACK 当前 `in_transaction` 为真的连接，空闲连接只计数（skipped_idle）；
      3. 动手**之前**先取现场：模块归属 + 栈帧 + 库文件，连同结果落 `db_write_failures` 台账；
      4. 该连接若"改过行却没提交"⇒ warning 级 in-flight + 计数。⛔ 把它读成"确实丢了一行"：
         `total_changes` 是连接的终身累计、回滚不清零，所以这个标签只支持"疑似"。

    永不抛：单条连接失败只让它自己那行 result 变成 rollback-failed，其余连接照办。
    """
    out = {"site": str(site)[:120], "rolled_back": [], "skipped_idle": 0,
           "in_flight_suspects": 0, "errors": []}
    exclude_ids = set(id(c) for c in exclude_conns if c is not None)
    _wf = None
    try:
        from butler.store import write_failures as _wf  # 延迟 import：本模块由 record 反向调用
        if _wf._conn is not None:
            exclude_ids.add(id(_wf._conn))              # ⛔ 把台账自己回滚掉＝现场没地方落
    except Exception as exc:
        out["errors"].append("ledger-exclude(%s)" % type(exc).__name__)
    conns = [obj for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection)]
    for obj in conns:
        if id(obj) in exclude_ids:
            continue
        try:
            if not obj.in_transaction:                  # 护栏 2：空闲连接一行不碰
                out["skipped_idle"] += 1
                continue
        except Exception as exc:
            out["errors"].append("unreadable(%s)" % type(exc).__name__)
            continue
        # 护栏 3：现场必须在 rollback 之前取完——事务一散，栈帧归属就散了
        rec = {"label": _module_label(obj), "holder_frame": _holder_frame(obj),
               "referrer_kinds": _referrer_kinds(obj),
               "files": _files(obj), "isolation_level": repr(obj.isolation_level),
               "total_changes": -1, "in_flight_suspect": False, "result": ""}
        try:
            rec["total_changes"] = int(obj.total_changes)
        except Exception as exc:
            rec["result"] = "changes-unreadable(%s)" % type(exc).__name__
        rec["in_flight_suspect"] = rec["total_changes"] >= 1   # 护栏 4：疑似，⛔ 定论
        try:
            obj.rollback()
            rec["result"] = "rolled_back"
        except Exception as exc:
            rec["result"] = "rollback-failed(%s)" % type(exc).__name__
            out["errors"].append(rec["result"])
        out["rolled_back"].append(rec)
        if rec["in_flight_suspect"]:
            out["in_flight_suspects"] += 1
            logger.warning("txn-heal site=%s in-flight-write-rolled-back label=%s frame=%s"
                           " kinds=%s files=%s changes=%d result=%s",
                           out["site"], rec["label"] or "-", rec["holder_frame"] or "-",
                           rec["referrer_kinds"] or "-", rec["files"] or "-",
                           rec["total_changes"], rec["result"])
        else:
            logger.warning("txn-heal site=%s healed label=%s frame=%s kinds=%s files=%s"
                           " changes=%d result=%s",
                           out["site"], rec["label"] or "-", rec["holder_frame"] or "-",
                           rec["referrer_kinds"] or "-", rec["files"] or "-",
                           rec["total_changes"], rec["result"])
        try:
            # allow_heal=False 是递归闸门：heal 自己这条台账写⛔ 再触发一次自愈
            _wf.record("store/txn_census.heal_open_transactions",
                       "txn-heal site=%s label=%s frame=%s kinds=%s files=%s changes=%d"
                       " suspect=%d result=%s"
                       % (out["site"], rec["label"] or "-", rec["holder_frame"] or "-",
                          rec["referrer_kinds"] or "-", rec["files"] or "-",
                          rec["total_changes"],
                          int(rec["in_flight_suspect"]), rec["result"]),
                       table="db_write_failures", allow_heal=False)
        except Exception as exc:
            out["errors"].append("ledger-record(%s)" % type(exc).__name__)
    return out
