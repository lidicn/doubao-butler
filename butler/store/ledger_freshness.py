"""账本新鲜度读数（DCD 裁定 20261001-A）。

口径钉死（详见 doc/双账本语义比对-20261001.md；三本记的**不是同一件事**，⛔ 合并、⛔ 混算）：
- `trigger_runs`  = 真执行：一次 trigger 命中并把动作跑完才写一行，落在 `butler.db`。
- `trigger_evaluations` = 评估：事件走到匹配器就一行（含 0 命中；30s 防抖丢弃的没走到 ⇒ 本账是事件数**下界**），落在 `butler.db`；DCD 裁定 20261001 格2 A。
- `trigger_audit` = 调度心跳：被 `wrap_scheduler_job` 包起来的定时源**每轮写一行**，
  与该轮有没有命中 trigger 无关，落在 `push_guard.db`；2026-09-30 实测 24h 内 3,971 行 vs 27 行。

读不到时⛔ 折成 0：`0` 会被读成"这本账空了"，而实情可能是"我根本没读到"——
按纪律 4，一个 0 在被证明它代表什么之前不算读数，所以失败一律记 `read_error`。
"""
from __future__ import annotations

import time
from typing import Any

DAY_S = 86400


def _age(now: float, ts: float):
    """新鲜度：最后一行离 now 多少秒；账本空＝None（⛔ 0，0 会被读成"刚刚写过"）。"""
    return round(now - ts, 1) if ts else None


def trigger_runs_block(now: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "db": "butler.db",
        "table": "trigger_runs",
        "meaning": "真执行（每次触发跑完动作才一行）",
    }
    try:
        from butler.store import repo
        ts = repo.latest_trigger_run_ts()
        out["last_ts"] = ts
        out["last_age_s"] = _age(now, ts)
        out["rows_24h"] = repo.count_trigger_runs_since(now - DAY_S)
    except Exception as e:
        out["read_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return out


def trigger_audit_block(now: float, auditor: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "db": "push_guard.db",
        "table": "trigger_audit",
        "meaning": "调度心跳（每个定时源每轮一行，与是否命中 trigger 无关）",
    }
    if auditor is None:
        out["unavailable"] = "trigger auditor 未初始化（本进程读不到心跳账本）"
        return out
    try:
        ts = auditor.latest_ts()
        out["last_ts"] = ts
        out["last_age_s"] = _age(now, ts)
    except Exception as e:
        out["read_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return out


def write_failures_block(now: float) -> dict[str, Any]:
    """写面失败台账（DCD 裁定 E）：记的是“别处写库失败了几次”，不是业务流水。"""
    out: dict[str, Any] = {
        "db": "db_write_failures.db",
        "table": "db_write_failures",
        "meaning": "写面失败台账（独立库文件，与 butler.db 不同库：被监视者不许当自己的账本）",
    }
    try:
        from butler.store import write_failures
        ts = write_failures.latest_ts()
        out["last_ts"] = ts
        out["last_age_s"] = _age(now, ts)
        out["rows_24h"] = write_failures.count_since(now - DAY_S)
        out["total_rows"] = write_failures.count_total()
    except Exception as e:
        out["read_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return out


def trigger_evaluations_block(now: float) -> dict[str, Any]:
    """评估账（DCD 裁定 20261001 格2 A）：走到匹配器的事件数，含 0 命中。

    ⛔ 与 `trigger_runs`（真执行）混算：一次执行前面必有 ≥1 次评估，但 0 命中的评估没有执行行。
    """
    out: dict[str, Any] = {
        "db": "butler.db",
        "table": "trigger_evaluations",
        "meaning": "评估（事件走到匹配器就一行，含 0 命中；防抖丢弃的不计＝事件数下界）",
    }
    try:
        from butler.store import repo
        ts = repo.latest_trigger_evaluation_ts()
        out["last_ts"] = ts
        out["last_age_s"] = _age(now, ts)
        out["rows_24h"] = repo.count_trigger_evaluations_since(now - DAY_S)
    except Exception as e:
        out["read_error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return out


def snapshot(rt: Any = None, now: float | None = None) -> dict[str, Any]:
    """三本账各自的新鲜度（同屏对照，谁停了看得见）。

    `rt` 只用来取 `trigger_registry.auditor`；拿不到 rt 时心跳那格标 unavailable，
    ⛔ 不假装有读数。
    """
    if now is None:
        now = time.time()
    auditor = None
    auditor_note = ""
    reg = getattr(rt, "trigger_registry", None) if rt is not None else None
    if reg is not None:
        try:
            auditor = reg.auditor
        except Exception as e:
            auditor_note = "auditor 取用失败: %s: %s" % (type(e).__name__, str(e)[:120])
    else:
        auditor_note = "runtime/trigger_registry 不可用"
    return {
        "generated_at": now,
        "statistic_key": "统计口径：触发次数用 trigger_runs；评估次数用 trigger_evaluations；定时腿心跳用 trigger_audit",
        "trigger_runs": trigger_runs_block(now),
        "trigger_evaluations": trigger_evaluations_block(now),
        "trigger_audit": trigger_audit_block(now, auditor),
        "write_failures": write_failures_block(now),
        "note": auditor_note,
    }
