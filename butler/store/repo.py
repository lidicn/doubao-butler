"""对话/指纹/唤醒日志的读写。所有方法同步，调用方在 async 上下文中用 run_in_threadpool 包裹。"""
from __future__ import annotations

import json
import time
from typing import Optional

from butler.store.db import get_conn


# ---------- 对话流水 ----------

def add_turn(
    member: str,
    role: str,
    text: str,
    *,
    voice: Optional[str] = None,
    engine: Optional[str] = None,
    duration_ms: Optional[int] = None,
    llm_ms: Optional[int] = None,
    dedup_hit: int = 0,
    source: str = "active",
    target: Optional[str] = None,
    ts: Optional[float] = None,
) -> int:
    c = get_conn()
    cur = c.execute(
        """INSERT INTO dialog_turns (ts, member, role, text, voice, engine, duration_ms, llm_ms, dedup_hit, source, target)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (ts or time.time(), member, role, text, voice, engine, duration_ms, llm_ms, dedup_hit, source, target),
    )
    c.commit()
    return int(cur.lastrowid)


def recent_turns(limit: int = 50, member: Optional[str] = None) -> list[dict]:
    c = get_conn()
    if member:
        rows = c.execute(
            "SELECT * FROM dialog_turns WHERE member=? ORDER BY ts DESC LIMIT ?",
            (member, limit),
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM dialog_turns ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_turn(turn_id: int) -> bool:
    c = get_conn()
    cur = c.execute("DELETE FROM dialog_turns WHERE id=?", (turn_id,))
    c.commit()
    return cur.rowcount > 0


# ---------- 防重复指纹 ----------

def add_fingerprint(member: str, text_hash: str, bigrams: set[str], ts: Optional[float] = None) -> None:
    c = get_conn()
    c.execute(
        "INSERT INTO dedup_fingerprints (ts, member, text_hash, bigrams) VALUES (?,?,?,?)",
        (ts or time.time(), member, text_hash, json.dumps(sorted(bigrams), ensure_ascii=False)),
    )
    c.commit()


def recent_fingerprints(member: str, since_ts: float) -> list[set[str]]:
    c = get_conn()
    rows = c.execute(
        "SELECT bigrams FROM dedup_fingerprints WHERE member=? AND ts>=? ORDER BY ts DESC",
        (member, since_ts),
    ).fetchall()
    return [set(json.loads(r["bigrams"])) for r in rows]


def cleanup_fingerprints(before_ts: float) -> int:
    c = get_conn()
    cur = c.execute("DELETE FROM dedup_fingerprints WHERE ts < ?", (before_ts,))
    c.commit()
    return cur.rowcount


# ---------- 唤醒日志 ----------

def log_wakeup(trigger: str, decision: str, *, room: str = "", member: str = "", reason: str = "", cost_ms: int = 0) -> None:
    c = get_conn()
    c.execute(
        "INSERT INTO wakeup_log (ts, trigger, room, member, decision, reason, cost_ms) VALUES (?,?,?,?,?,?,?)",
        (time.time(), trigger, room, member, decision, reason, cost_ms),
    )
    c.commit()


def recent_wakeups(limit: int = 50) -> list[dict]:
    c = get_conn()
    rows = c.execute("SELECT * FROM wakeup_log ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------- 推送通知历史 ----------

def add_notify(
    member: str,
    title: str,
    content: str,
    *,
    type: str = "info",
    important: bool = False,
    duration: int = 0,
    with_tts: bool = True,
    volume: int = 80,
    pause_media: bool = False,
    status: str = "ok",
    tts_url: str = "",
    ts: float | None = None,
) -> int:
    c = get_conn()
    cur = c.execute(
        """INSERT INTO notify_history
           (ts, member, title, content, type, important, duration, with_tts, volume, pause_media, status, tts_url)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ts or time.time(), member, title, content, type,
            1 if important else 0, duration, 1 if with_tts else 0, volume,
            1 if pause_media else 0, status, tts_url,
        ),
    )
    c.commit()
    return int(cur.lastrowid)


def recent_notifies(limit: int = 20) -> list[dict]:
    c = get_conn()
    rows = c.execute("SELECT * FROM notify_history ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_notify(notify_id: int) -> bool:
    c = get_conn()
    cur = c.execute("DELETE FROM notify_history WHERE id=?", (notify_id,))
    c.commit()
    return cur.rowcount > 0


def _row_to_dict(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in r.keys()}


# ---- 技能运行历史（skill_runs） ----

def add_skill_run(skill_id: str, source: str, status: str, duration_ms: int,
                  text: str, error: str, meta: dict | None = None,
                  trace_id: str = "") -> int:
    import json as _json

    c = get_conn()
    cur = c.execute(
        """INSERT INTO skill_runs (ts, skill_id, source, status, duration_ms, text, error, meta_json, trace_id)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (time.time(), skill_id, source, status, duration_ms, text, error,
         _json.dumps(meta, ensure_ascii=False) if meta else None,
         trace_id),
    )
    c.commit()
    return int(cur.lastrowid)


def recent_skill_runs(skill_id: str = "", limit: int = 50) -> list[dict]:
    c = get_conn()
    if skill_id:
        rows = c.execute(
            "SELECT * FROM skill_runs WHERE skill_id=? ORDER BY ts DESC LIMIT ?",
            (skill_id, limit),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM skill_runs ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---- trigger 运行历史（trigger_runs） ----

def add_trigger_run(trigger_id: str, event: str, status: str,
                    actions: list | None = None, error: str = "",
                    trace_id: str = "") -> int:
    import json as _json
    c = get_conn()
    cur = c.execute(
        """INSERT INTO trigger_runs (ts, trigger_id, event, status, actions_json, error, trace_id)
           VALUES (?,?,?,?,?,?,?)""",
        (time.time(), trigger_id, event, status,
         _json.dumps(actions, ensure_ascii=False) if actions else None, error or None,
         trace_id),
    )
    c.commit()
    return int(cur.lastrowid)


def latest_trigger_run_ts() -> float:
    """trigger_runs 最后一行的 ts（执行口径账本的新鲜度读数）。

    表空返回 0.0；读失败**直接抛**——调用方（/api/status 的 ledgers 块）把它记成
    read_error，⛔ 折成 0.0（0.0 会被读成"账本空了"，而实情是"我没读到"）。
    """
    c = get_conn()
    r = c.execute("SELECT MAX(ts) FROM trigger_runs").fetchone()
    return float(r[0]) if r and r[0] is not None else 0.0


def count_trigger_runs_since(since_ts: float) -> int:
    """since_ts 之后写了多少行 trigger_runs（口径：真执行，⛔ 与 trigger_audit 混算）。"""
    c = get_conn()
    return int(c.execute("SELECT COUNT(*) FROM trigger_runs WHERE ts>=?", (since_ts,)).fetchone()[0])


# ---- 触发链评估流水（trigger_evaluations，DCD 裁定 20261001 格2 裁 A） ----

def add_trigger_evaluation(event_type: str, matched_count: int,
                           duration_ms: int = 0, dry_run: bool = False,
                           trace_id: str = "") -> int:
    """记一次「事件走到了匹配器」，命中 0 条也记。

    防抖（30s）在 `_match` 之前就 return，被丢掉的事件不在本账里 ⇒ 本账是事件数的
    **下界**，⛔ 把它读成「总共来了多少事件」。dry_run 那几次带标记，⛔ 与真执行混算。
    """
    c = get_conn()
    cur = c.execute(
        """INSERT INTO trigger_evaluations (ts, event_type, matched_count, duration_ms, dry_run, trace_id)
           VALUES (?,?,?,?,?,?)""",
        (time.time(), event_type, int(matched_count), int(duration_ms),
         1 if dry_run else 0, trace_id),
    )
    c.commit()
    return int(cur.lastrowid)


def latest_trigger_evaluation_ts() -> float:
    """评估账最后一行的 ts；表空返 0.0，读失败**直接抛**（⛔ 折成 0.0，那会被读成「账空了」）。"""
    c = get_conn()
    r = c.execute("SELECT MAX(ts) FROM trigger_evaluations").fetchone()
    return float(r[0]) if r and r[0] is not None else 0.0


def count_trigger_evaluations_since(since_ts: float) -> int:
    c = get_conn()
    return int(c.execute("SELECT COUNT(*) FROM trigger_evaluations WHERE ts>=?",
                          (since_ts,)).fetchone()[0])


# ---- 触发冷却（trigger_cooldowns，DCD 20260928 第 7 条 / 20261002 跟办 2） ----
COOLDOWN_RETENTION_S = 86400 * 7   # 旧 JSON 时代就是 `86400*7`，原样搬，⛔ 悄悄改宽改窄


def load_cooldowns(retention_s: float = COOLDOWN_RETENTION_S,
                   now: float | None = None) -> dict[str, float]:
    """读未过窗的冷却键。读失败**直接抛**：空 dict 和"没读到"是两件事。
    折成空 dict 会被上游读成「所有触发器都没冷却」⇒ 全员提前再响一次；
    抛出去则由 `TriggerEngine._load_cooldowns` 接住、进 `db_write_failures` 台账并 loud。
    """
    c = get_conn()
    cutoff = (time.time() if now is None else now) - retention_s
    return {r["cd_key"]: float(r["last_fired"])
            for r in c.execute("SELECT cd_key, last_fired FROM trigger_cooldowns"
                               " WHERE last_fired>=?", (cutoff,))}


def save_cooldowns(values: dict[str, float], retention_s: float = COOLDOWN_RETENTION_S,
                   now: float | None = None) -> int:
    """整批 upsert ＋顺手清掉过窗的行。
    旧码每轮把整个 dict 重写进 JSON，天然不会长歪；收进库里若只 INSERT 就变成**只增账**，
    所以过期清理必须跟写同批（⛔ 另起一个"以后再来扫"的腿）。
    INSERT OR REPLACE⛔ 换成 ON CONFLICT DO UPDATE：后者要 SQLite>=3.24，现网镜像的库版本没现读过。
    """
    c = get_conn()
    cutoff = (time.time() if now is None else now) - retention_s
    c.execute("DELETE FROM trigger_cooldowns WHERE last_fired<?", (cutoff,))
    c.executemany("INSERT OR REPLACE INTO trigger_cooldowns (cd_key, last_fired) VALUES (?,?)",
                  [(str(k), float(v)) for k, v in values.items()])
    c.commit()
    return len(values)


EVALUATION_CALIBER_NOTE = (
    "此块＝trigger_evaluations 评估口径（事件走到匹配器就一行，含 0 命中；30s 防抖丢弃的没走到 ⇒ 本账是事件数下界）；"
    "「真执行了几次」用 execution 块＝butler.db.trigger_runs，「定时腿每轮心跳」用 heartbeat 块＝push_guard.db.trigger_audit；"
    "三本账⛔ 互换，见路线图 §14.5。"
)


def trigger_evaluation_stats_since(since_ts: float | None = None) -> dict:
    """评估口径计数（butler.db.trigger_evaluations）。读失败直接抛。"""
    c = get_conn()
    where, params = "", ()
    if since_ts is not None:
        where, params = "WHERE ts>=?", (since_ts,)
    row = c.execute(
        "SELECT COUNT(*) AS total,"
        "       COALESCE(SUM(matched_count), 0) AS matched_sum,"
        "       COALESCE(SUM(CASE WHEN matched_count>0 THEN 1 ELSE 0 END), 0) AS with_match,"
        "       COALESCE(SUM(dry_run), 0) AS dry_run_rows,"
        "       COALESCE(MAX(duration_ms), 0) AS max_duration_ms"
        f"  FROM trigger_evaluations {where}", params).fetchone()
    by_event = c.execute(
        f"SELECT event_type, COUNT(*) AS n FROM trigger_evaluations {where}"
        " GROUP BY event_type ORDER BY n DESC LIMIT 8", params).fetchall()
    return {
        "table": "trigger_evaluations",
        "caliber": "evaluation",
        "db": "butler.db",
        "since_ts": since_ts,
        "evaluations": int(row["total"]),
        "with_match": int(row["with_match"]),
        "zero_match": int(row["total"]) - int(row["with_match"]),
        "matched_sum": int(row["matched_sum"]),
        "dry_run_rows": int(row["dry_run_rows"]),
        "max_duration_ms": int(row["max_duration_ms"]),
        "by_event": [{"event_type": r["event_type"], "evaluations": int(r["n"])}
                     for r in by_event],
        "caliber_note": EVALUATION_CALIBER_NOTE,
    }


QUARANTINED_ERROR_MARK = "skill quarantined"


def trigger_run_stats_since(since_ts: float | None = None) -> dict:
    """执行口径计数（butler.db.trigger_runs）。路线图 §14.5 裁定 A：「触发/失败多少次」只认这本。

    since_ts=None＝不限窗（全表）。失败数用 error_rows − quarantined_skips 相减，⛔ 写
    `status='error' AND error NOT LIKE ?`：error 列允许 NULL（add_trigger_run 就写 `error or None`），
    NOT LIKE 在 NULL 上整条判 NULL ⇒ 会把真失败静默漏掉。隔离态（runner 回 `skill quarantined: …`，
    engine 原样拼进 error 列）是人工按下去的终态、不是一次运行失败，所以单列一格。
    读失败**直接抛**，⛔ 折成 0（0 会被读成「没失败」，同 ledger_freshness 的规矩）。
    """
    c = get_conn()
    conds = []
    params = []
    if since_ts is not None:
        conds.append("ts>=?")
        params.append(since_ts)
    def count(extra="", extra_params=()):
        clauses = list(conds)
        if extra:
            clauses.append(extra)
        sql = "SELECT COUNT(*) FROM trigger_runs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return int(c.execute(sql, tuple(params) + tuple(extra_params)).fetchone()[0])

    like = "%" + QUARANTINED_ERROR_MARK + "%"
    error_rows = count("status='error'")
    skips = count("status='error' AND error LIKE ?", (like,))
    return {
        "table": "trigger_runs",
        "db": "butler.db",
        "caliber": "execution",
        "since_ts": since_ts,
        "total": count(),
        "ok": count("status='ok'"),
        "error_rows": error_rows,
        "quarantined_skips": skips,
        "failures_excl_quarantined": error_rows - skips,
    }

def recent_trigger_runs(trigger_id: str = "", limit: int = 50) -> list[dict]:
    c = get_conn()
    if trigger_id:
        rows = c.execute(
            "SELECT * FROM trigger_runs WHERE trigger_id=? ORDER BY ts DESC LIMIT ?",
            (trigger_id, limit),
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM trigger_runs ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def trigger_logs(trigger_id: str = "", trace_id: str = "", status: str = "",
                 limit: int = 50) -> list[dict]:
    """v2.6: 触发日志页数据源，按 trigger/trace/status 过滤，时间倒序。"""
    c = get_conn()
    conds: list[str] = []
    params: list = []
    if trigger_id:
        conds.append("trigger_id=?")
        params.append(trigger_id)
    if trace_id:
        conds.append("trace_id=?")
        params.append(trace_id)
    if status:
        conds.append("status=?")
        params.append(status)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    params.append(max(1, min(int(limit), 500)))
    rows = c.execute(
        f"SELECT * FROM trigger_runs{where} ORDER BY ts DESC LIMIT ?", params
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_skill_runs_today(skill_id: str, ok_only: bool = True) -> int:
    """当日（本地时区）该技能运行计数，用于每日硬上限。"""
    c = get_conn()
    midnight = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    sql = "SELECT COUNT(*) FROM skill_runs WHERE skill_id=? AND ts>=?"
    if ok_only:
        sql += " AND status NOT IN ('breaker','daily_limit','disabled','not_found','engine_missing')"
    row = c.execute(sql, (skill_id, midnight)).fetchone()
    return int(row[0] or 0)


# ---- 决策层运行历史（decision_runs，v0.9） ----

def add_decision_run(action: str, *, summary: str = "", room: str = "", member: str = "",
                     text: str = "", reason: str = "", confidence: float = 0.0,
                     status: str = "ok", filter_reason: str = "") -> int:
    c = get_conn()
    cur = c.execute(
        """INSERT INTO decision_runs (ts, summary, action, room, member, text, reason, confidence, status, filter_reason)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (time.time(), summary or None, action, room or None, member or None,
         text or None, reason or None, confidence, status, filter_reason or None),
    )
    c.commit()
    return int(cur.lastrowid)


def recent_decision_runs(limit: int = 30, action: str = "") -> list[dict]:
    c = get_conn()
    if action:
        rows = c.execute(
            "SELECT * FROM decision_runs WHERE action=? ORDER BY ts DESC LIMIT ?",
            (action, limit),
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM decision_runs ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def recent_decision_since(action: str, since_ts: float, room: str = "", member: str = "") -> list[dict]:
    """查某类行动最近记录（决策层冷却用）。"""
    c = get_conn()
    sql = "SELECT * FROM decision_runs WHERE action=? AND ts>=?"
    params: list = [action, since_ts]
    if room:
        sql += " AND room=?"
        params.append(room)
    if member:
        sql += " AND member=?"
        params.append(member)
    sql += " ORDER BY ts DESC"
    rows = c.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---- 技能执行统计（M6 质量评估） ----

_OK_STATUSES = ("ok", "success")
_FAIL_STATUSES = ("error", "failed", "exception")


def get_skill_stats(skill_id, days=30):
    """统计单个技能的执行数据。返回 total/success/fail/skipped/success_rate/avg_duration_ms/last_run_at/last_status。"""
    c = get_conn()
    since = time.time() - days * 86400
    rows = c.execute(
        "SELECT status, duration_ms, ts FROM skill_runs WHERE skill_id=? AND ts>=?",
        (skill_id, since),
    ).fetchall()
    total = len(rows)
    success = sum(1 for r in rows if r[0] in _OK_STATUSES)
    fail = sum(1 for r in rows if r[0] in _FAIL_STATUSES)
    skipped = total - success - fail
    success_rate = round(success / (success + fail), 3) if (success + fail) > 0 else None
    durations = [r[1] for r in rows if r[0] in _OK_STATUSES and r[1] is not None]
    avg_duration = round(sum(durations) / len(durations)) if durations else None
    last_run = max((r[2] for r in rows), default=None)
    last_status = next((r[0] for r in sorted(rows, key=lambda x: x[2], reverse=True)), None)
    return {
        "skill_id": skill_id, "days": days, "total": total,
        "success": success, "fail": fail, "skipped": skipped,
        "success_rate": success_rate, "avg_duration_ms": avg_duration,
        "last_run_at": last_run, "last_status": last_status,
    }


def get_all_skill_stats(days=30):
    """统计所有有执行记录的技能，按成功率升序。"""
    c = get_conn()
    since = time.time() - days * 86400

    # P1 修复：用一条 SQL 一次性统计所有技能，避免 N+1 查询
    rows = c.execute("""
        SELECT
            skill_id,
            COUNT(*) as total,
            SUM(CASE WHEN status IN ('ok', 'success', 'done') THEN 1 ELSE 0 END) as success,
            SUM(CASE WHEN status IN ('error', 'failed', 'exception') THEN 1 ELSE 0 END) as fail,
            AVG(CASE WHEN status IN ('ok', 'success', 'done') THEN duration_ms ELSE NULL END) as avg_duration,
            MAX(ts) as last_run_at
        FROM skill_runs
        WHERE ts>=?
        GROUP BY skill_id
    """, (since,)).fetchall()

    stats = []
    for r in rows:
        skill_id = r[0]
        total = r[1]
        success = r[2] or 0
        fail = r[3] or 0
        avg_duration = round(r[4]) if r[4] else None
        last_run = r[5]
        skipped = total - success - fail
        success_rate = round(success / (success + fail), 3) if (success + fail) > 0 else None

        # 获取最后一次的状态
        last_row = c.execute(
            "SELECT status FROM skill_runs WHERE skill_id=? AND ts>=? ORDER BY ts DESC LIMIT 1",
            (skill_id, since),
        ).fetchone()
        last_status = last_row[0] if last_row else None

        stats.append({
            "skill_id": skill_id, "days": days, "total": total,
            "success": success, "fail": fail, "skipped": skipped,
            "success_rate": success_rate, "avg_duration_ms": avg_duration,
            "last_run_at": last_run, "last_status": last_status,
        })

    stats.sort(key=lambda s: (s["success_rate"] is None, s["success_rate"] or 1, -s["total"]))
    return stats


def get_low_quality_skills(days=30, min_runs=5, max_rate=0.6):
    """发现低质量技能：执行次数>=min_runs且成功率<=max_rate。"""
    return [
        s for s in get_all_skill_stats(days)
        if s["total"] >= min_runs and s["success_rate"] is not None and s["success_rate"] <= max_rate
    ]

# ---- 记忆事实（memory_facts，v1.1 记忆系统） ----

def list_facts(status: str = "", fact_type: str = "", limit: int = 100) -> list[dict]:
    """列出记忆事实，可按状态/类型过滤。pending 优先，新的在前。"""
    c = get_conn()
    sql = "SELECT * FROM memory_facts WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if fact_type:
        sql += " AND fact_type=?"
        params.append(fact_type)
    sql += " ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, ts DESC LIMIT ?"
    params.append(limit)
    rows = c.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_fact(fact_id: int) -> dict | None:
    c = get_conn()
    r = c.execute("SELECT * FROM memory_facts WHERE id=?", (fact_id,)).fetchone()
    return _row_to_dict(r) if r else None


def update_fact(fact_id: int, *, content: str = "", fact_type: str = "",
                confidence: float | None = None) -> bool:
    """编辑记忆事实内容/类型/置信度。"""
    c = get_conn()
    sets: list[str] = []
    params: list = []
    if content:
        sets.append("content=?")
        params.append(content)
    if fact_type:
        sets.append("fact_type=?")
        params.append(fact_type)
    if confidence is not None:
        sets.append("confidence=?")
        params.append(confidence)
    if not sets:
        return False
    sets.append("reviewed_ts=?")
    params.append(time.time())
    params.append(fact_id)
    cur = c.execute(f"UPDATE memory_facts SET {', '.join(sets)} WHERE id=?", params)
    c.commit()
    # v1.1.1: 如果改了已投喂事实的内容，标记为 changed（需重新投喂）
    if cur.rowcount > 0 and content:
        detect_content_change(fact_id)
    return cur.rowcount > 0


def set_fact_status(fact_id: int, status: str) -> bool:
    """审核：approve / reject。"""
    if status not in ("pending", "approved", "rejected"):
        return False
    c = get_conn()
    cur = c.execute(
        "UPDATE memory_facts SET status=?, reviewed_ts=? WHERE id=?",
        (status, time.time(), fact_id),
    )
    c.commit()
    return cur.rowcount > 0


def delete_fact(fact_id: int) -> bool:
    c = get_conn()
    cur = c.execute("DELETE FROM memory_facts WHERE id=?", (fact_id,))
    c.commit()
    return cur.rowcount > 0


def count_facts_by_status() -> dict:
    """统计各状态数量：{pending: n, approved: n, rejected: n}。"""
    c = get_conn()
    rows = c.execute(
        "SELECT status, COUNT(*) AS n FROM memory_facts GROUP BY status"
    ).fetchall()
    out = {"pending": 0, "approved": 0, "rejected": 0}
    for r in rows:
        out[r["status"]] = r["n"]
    return out



# ---- 投喂状态（v1.1.1） ----

def mark_fact_fed(fact_id: int, fed_ts: float) -> bool:
    """投喂成功：feed_status=fed，记录时间和内容快照。"""
    c = get_conn()
    # 先取当前 content 做快照
    row = c.execute("SELECT content FROM memory_facts WHERE id=?", (fact_id,)).fetchone()
    if not row:
        return False
    cur = c.execute(
        "UPDATE memory_facts SET feed_status='fed', fed_ts=?, fed_content=?, feed_error='' WHERE id=?",
        (fed_ts, row["content"], fact_id),
    )
    c.commit()
    return cur.rowcount > 0


def mark_fact_failed(fact_id: int, error: str) -> bool:
    """投喂失败。"""
    c = get_conn()
    cur = c.execute(
        "UPDATE memory_facts SET feed_status='failed', feed_error=? WHERE id=?",
        (error[:500], fact_id),
    )
    c.commit()
    return cur.rowcount > 0


def detect_content_change(fact_id: int) -> bool:
    """如果已 fed 的事实 content 和 fed_content 不一致，标记为 changed。
    返回是否发生了变更。"""
    c = get_conn()
    row = c.execute(
        "SELECT content, fed_content, feed_status FROM memory_facts WHERE id=?",
        (fact_id,),
    ).fetchone()
    if not row:
        return False
    if row["feed_status"] == "fed" and row["fed_content"] and row["content"] != row["fed_content"]:
        c.execute(
            "UPDATE memory_facts SET feed_status='changed' WHERE id=?",
            (fact_id,),
        )
        c.commit()
        return True
    return False
