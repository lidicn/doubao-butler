"""SQLite 存储：对话流水、防重复指纹、唤醒日志。WAL 模式，零额外容器。"""

from __future__ import annotations



import sqlite3

import threading

import time

from pathlib import Path



from butler.config import get_settings

from butler.logging_setup import get_logger



logger = get_logger("butler.store")



_lock = threading.Lock()

_conn: sqlite3.Connection | None = None





def get_conn() -> sqlite3.Connection:
    """懒初始化：建连＋建表全在 `_lock` 内，且先跑完 `_init` 再发布 `_conn`。

    判定放在锁外时并发首调会各建一条，先建的那条被覆盖后就没人关；先发布再建表时别的线程
    能拿到「表还没建完」的连接，而 `_init` 一抛错那条坏连接还会被永久缓存。所以双检、建连、
    建表、发布全收进 `_lock`，`_init` 失败则关掉连接并保持 None（下次重跑建表）。连接已发布
    后走无锁快路径——热路径⛔ 排进全局锁（现网多模态 P95 已 6,259ms）。
    """
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:                # 双检：等锁期间可能已由别人发布
            return _conn
        s = get_settings()
        Path(s.data_dir).mkdir(parents=True, exist_ok=True)
        db_path = Path(s.data_dir) / "butler.db"
        c = sqlite3.connect(str(db_path), check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.row_factory = sqlite3.Row
        try:
            _init(c)                         # ★ 建表／ALTER 迁移跑完之前这条连接不对外可见
        except Exception:
            c.close()                        # ★ 坏连接⛔ 留在全局上、也⛔ 漏句柄
            raise
        _conn = c
        logger.info("sqlite opened at %s (WAL)", db_path)
        return _conn





_TRIGGER_EVALUATIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS trigger_evaluations ("
    " id            INTEGER PRIMARY KEY AUTOINCREMENT,"
    " ts            REAL NOT NULL,"
    " event_type    TEXT NOT NULL,"
    " matched_count INTEGER NOT NULL DEFAULT 0,"
    " duration_ms   INTEGER NOT NULL DEFAULT 0,"
    " dry_run       INTEGER NOT NULL DEFAULT 0,"
    " trace_id      TEXT NOT NULL DEFAULT '')"
)
_TRIGGER_EVALUATIONS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_trigger_evaluations_ts ON trigger_evaluations(ts)",
    "CREATE INDEX IF NOT EXISTS idx_trigger_evaluations_event ON trigger_evaluations(event_type, ts)",
)


def _ensure_trigger_evaluations(c: sqlite3.Connection) -> None:
    """格2 裁 A：建评估账的表＋两张索引。失败只 loud，⛔ 不把启动打断。

    读侧（ledger_freshness / /api/triggers/audit）拿不到这张表时各自回 read_error 标签，
    ⛔ 不折成 0；而第一笔评估写入失败会走 locked 分支去点名持锁连接（诊断价值不丢）。
    """
    try:
        c.execute(_TRIGGER_EVALUATIONS_DDL)
        for stmt in _TRIGGER_EVALUATIONS_IDX:
            c.execute(stmt)
        c.commit()
    except Exception as e:
        logger.warning("trigger_evaluations init failed: %s: %s"
                       " (read_error on the evaluation ledger in this process): %s",
                       type(e).__name__, str(e)[:200], e)


_TRIGGER_COOLDOWNS_DDL = (
    "CREATE TABLE IF NOT EXISTS trigger_cooldowns ("
    " cd_key     TEXT PRIMARY KEY,"
    " last_fired REAL NOT NULL)"
)
_TRIGGER_COOLDOWNS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_trigger_cooldowns_last ON trigger_cooldowns(last_fired)",
)


def _ensure_trigger_cooldowns(c: sqlite3.Connection) -> None:
    """跟办 2（DCD 20261002:107＝20260928 第 7 条「JSON 收口进 SQLite」的欠执行）：建冷却表。
    形制照 `_ensure_trigger_evaluations`：独立受防的 DDL，⛔ 挤进 `_init` 那条 executescript
    （老库上那条脚本全是 no-op 不吃写锁，而新表的 CREATE 在现网库上是真写）。
    与评估账的差别在**后果**：这张表读不到＝重启后所有触发器冷却清零（会提前再响一次），
    所以读侧⛔ 把失败折成空 dict，`repo.load_cooldowns` 直接抛，engine 接住并进台账。
    """
    try:
        c.execute(_TRIGGER_COOLDOWNS_DDL)
        for stmt in _TRIGGER_COOLDOWNS_IDX:
            c.execute(stmt)
        c.commit()
    except Exception as e:
        logger.warning("trigger_cooldowns init failed: %s: %s"
                       " (cooldowns will NOT survive a restart until this is fixed): %s",
                       type(e).__name__, str(e)[:200], e)


def _init(c: sqlite3.Connection) -> None:

    c.executescript(

        """

        CREATE TABLE IF NOT EXISTS dialog_turns (

            id        INTEGER PRIMARY KEY AUTOINCREMENT,

            ts        REAL NOT NULL,

            member    TEXT NOT NULL,

            role      TEXT NOT NULL,            -- butler | user

            text      TEXT NOT NULL,

            voice     TEXT,

            engine    TEXT,

            duration_ms INTEGER,

            llm_ms    INTEGER,

            dedup_hit INTEGER DEFAULT 0,

            source    TEXT DEFAULT 'active'     -- active | test | proactive

        );

        CREATE INDEX IF NOT EXISTS idx_turns_member_ts ON dialog_turns(member, ts);

        CREATE INDEX IF NOT EXISTS idx_turns_ts ON dialog_turns(ts);



        CREATE TABLE IF NOT EXISTS dedup_fingerprints (

            id        INTEGER PRIMARY KEY AUTOINCREMENT,

            ts        REAL NOT NULL,

            member    TEXT NOT NULL,

            text_hash TEXT NOT NULL,

            bigrams   TEXT NOT NULL               -- JSON 数组

        );

        CREATE INDEX IF NOT EXISTS idx_fp_member_ts ON dedup_fingerprints(member, ts);



        CREATE TABLE IF NOT EXISTS wakeup_log (

            id        INTEGER PRIMARY KEY AUTOINCREMENT,

            ts        REAL NOT NULL,

            trigger   TEXT NOT NULL,              -- face | timer | voice | manual

            room      TEXT,

            member    TEXT,

            decision  TEXT NOT NULL,              -- pass | cooldown | dnd | muted

            reason    TEXT,

            cost_ms   INTEGER

        );

        CREATE INDEX IF NOT EXISTS idx_wake_ts ON wakeup_log(ts);



        CREATE TABLE IF NOT EXISTS notify_history (

            id        INTEGER PRIMARY KEY AUTOINCREMENT,

            ts        REAL NOT NULL,

            member    TEXT NOT NULL,

            title     TEXT NOT NULL,

            content   TEXT NOT NULL,

            type      TEXT NOT NULL DEFAULT 'info',

            important INTEGER NOT NULL DEFAULT 0,

            duration  INTEGER NOT NULL DEFAULT 0,

            with_tts  INTEGER NOT NULL DEFAULT 1,

            volume    INTEGER NOT NULL DEFAULT 80,

            pause_media INTEGER NOT NULL DEFAULT 0,

            status    TEXT NOT NULL DEFAULT 'ok',   -- ok | fail

            tts_url   TEXT

        );

        CREATE INDEX IF NOT EXISTS idx_notify_ts ON notify_history(ts);



        CREATE TABLE IF NOT EXISTS skill_runs (

            id          INTEGER PRIMARY KEY AUTOINCREMENT,

            ts          REAL NOT NULL,

            skill_id    TEXT NOT NULL,

            source      TEXT NOT NULL DEFAULT 'api',  -- api | mqtt | test

            status      TEXT NOT NULL DEFAULT 'ok',   -- ok | dry | breaker | daily_limit |

                                                      -- dedup | tv_offline | error ...

            duration_ms INTEGER NOT NULL DEFAULT 0,

            text        TEXT,

            error       TEXT,

            meta_json   TEXT

        );

        CREATE INDEX IF NOT EXISTS idx_skill_runs_skill_ts ON skill_runs(skill_id, ts);

        CREATE INDEX IF NOT EXISTS idx_skill_runs_ts ON skill_runs(ts);

        

        CREATE TABLE IF NOT EXISTS trigger_runs (

            id          INTEGER PRIMARY KEY AUTOINCREMENT,

            ts          REAL NOT NULL,

            trigger_id  TEXT NOT NULL,

            event       TEXT NOT NULL,

            status      TEXT NOT NULL DEFAULT 'ok',  -- ok | no_match | cooldown | error

            actions_json TEXT,

            error       TEXT

        );

        CREATE INDEX IF NOT EXISTS idx_trigger_runs_trigger_ts ON trigger_runs(trigger_id, ts);

        CREATE INDEX IF NOT EXISTS idx_trigger_runs_ts ON trigger_runs(ts);



        CREATE TABLE IF NOT EXISTS decision_runs (

            id            INTEGER PRIMARY KEY AUTOINCREMENT,

            ts            REAL NOT NULL,

            summary       TEXT,

            action        TEXT NOT NULL DEFAULT 'no_action',  -- no_action|speak|notify|reminder|suggest_automation|filtered

            room          TEXT,

            member        TEXT,

            text          TEXT,

            reason        TEXT,

            confidence    REAL DEFAULT 0,

            status        TEXT NOT NULL DEFAULT 'ok',  -- ok | filtered | no_action | error

            filter_reason TEXT

        );

        CREATE INDEX IF NOT EXISTS idx_decision_ts ON decision_runs(ts);

        CREATE INDEX IF NOT EXISTS idx_decision_action_ts ON decision_runs(action, ts);



        CREATE TABLE IF NOT EXISTS schedules (

            id          INTEGER PRIMARY KEY AUTOINCREMENT,

            member      TEXT NOT NULL,

            title       TEXT NOT NULL,

            description TEXT DEFAULT '',

            start_time  TEXT NOT NULL,            -- ISO format YYYY-MM-DD HH:MM

            end_time    TEXT,

            location    TEXT DEFAULT '',

            recurrence  TEXT DEFAULT 'none',       -- none | daily | weekly | monthly

            done        INTEGER NOT NULL DEFAULT 0,

            created_at  REAL NOT NULL,

            updated_at  REAL NOT NULL

        );

        CREATE INDEX IF NOT EXISTS idx_schedules_member_start ON schedules(member, start_time);

        CREATE INDEX IF NOT EXISTS idx_schedules_start ON schedules(start_time);



        CREATE TABLE IF NOT EXISTS agent_traces (

            id          INTEGER PRIMARY KEY AUTOINCREMENT,

            trace_id    TEXT NOT NULL,

            ts          REAL NOT NULL,

            member      TEXT NOT NULL DEFAULT '',

            source      TEXT NOT NULL DEFAULT 'active',  -- active | test | api

            user_text   TEXT NOT NULL DEFAULT '',

            status      TEXT NOT NULL DEFAULT 'ok',       -- ok | timeout | max_iter | error | escaped

            error       TEXT NOT NULL DEFAULT '',

            total_ms    INTEGER NOT NULL DEFAULT 0,

            llm_calls   INTEGER NOT NULL DEFAULT 0,

            tool_calls  INTEGER NOT NULL DEFAULT 0,

            step_count  INTEGER NOT NULL DEFAULT 0,

            steps_json  TEXT NOT NULL DEFAULT '[]'

        );

        CREATE INDEX IF NOT EXISTS idx_traces_trace_id ON agent_traces(trace_id);

        CREATE INDEX IF NOT EXISTS idx_traces_ts ON agent_traces(ts);

        CREATE INDEX IF NOT EXISTS idx_traces_member_ts ON agent_traces(member, ts);

        CREATE INDEX IF NOT EXISTS idx_traces_status ON agent_traces(status);



        CREATE TABLE IF NOT EXISTS chat_logs (

            id              INTEGER PRIMARY KEY AUTOINCREMENT,

            ts              REAL NOT NULL,

            user_msg        TEXT NOT NULL,           -- 用户说的话

            assistant_reply TEXT,                    -- 管家回复的

            room            TEXT,                    -- 哪个房间

            role            TEXT,                    -- 哪个角色

            source          TEXT DEFAULT 'mobile_app', -- mobile_app | xiaoai | tv_voice

            tools_called    TEXT,                    -- 调用了哪些工具（JSON）

            success         INTEGER DEFAULT 1,       -- 成功没（0/1）

            meta_json       TEXT DEFAULT '{}'        -- 额外信息

        );

        CREATE INDEX IF NOT EXISTS idx_chat_logs_ts ON chat_logs(ts);

        CREATE INDEX IF NOT EXISTS idx_chat_logs_role_ts ON chat_logs(role, ts);

        CREATE INDEX IF NOT EXISTS idx_chat_logs_source ON chat_logs(source);



        CREATE TABLE IF NOT EXISTS memory_facts (

            id          INTEGER PRIMARY KEY AUTOINCREMENT,

            ts          REAL NOT NULL,

            fact_type   TEXT NOT NULL,            -- habit | preference | family | event

            content     TEXT NOT NULL,            -- 具体内容

            confidence  REAL DEFAULT 0.5,         -- 置信度（0-1）

            status      TEXT DEFAULT 'pending',   -- pending | approved | rejected（审核）

            source      TEXT DEFAULT 'extraction', -- extraction | user_import | manual

            meta_json   TEXT DEFAULT '{}',        -- 额外信息（JSON）

            reviewed_ts REAL,

            target_role TEXT DEFAULT '',          -- v1.1.1 投喂目标角色（butler/jarvis/caesar/luna/gu_anheng/xiaoyue）

            feed_status TEXT DEFAULT 'pending',   -- v1.1.1 pending | fed | changed | failed

            fed_ts      REAL,                     -- v1.1.1 投喂时间

            feed_error  TEXT DEFAULT '',          -- v1.1.1 最近一次投喂错误

            fed_content TEXT DEFAULT ''           -- v1.1.1 投喂时内容快照（检测变更）

        );

        CREATE INDEX IF NOT EXISTS idx_memory_status ON memory_facts(status);

        CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_facts(fact_type);

        CREATE INDEX IF NOT EXISTS idx_memory_ts ON memory_facts(ts);

        CREATE INDEX IF NOT EXISTS idx_memory_target_role ON memory_facts(target_role);

        CREATE INDEX IF NOT EXISTS idx_memory_feed_status ON memory_facts(feed_status);

        """

    )


    # 格2（DCD 裁定 20261001 A）：新表走独立受防的 DDL，不进上面那条 executescript。
    # 原因：老库上那条脚本全是 no-op（不吃写锁），而新表的 CREATE 在现网库上是真写；
    # 现网此刻有一条未提交的持锁写事务（第二窗在点名它），挤在启动路径上等于
    # 把「缺这张表」升级成「容器起不来」。失败一律 loud（logger.warning），⛔ 静默。
    _ensure_trigger_evaluations(c)
    _ensure_trigger_cooldowns(c)

    # v2.6: trace_id 幂等迁移。格2（2026-09-30）：只吞「列已存在」，其余 OperationalError
    # 一律 raise —— 静 pass 会把「库被锁/表缺失/只读盘」读成「早迁移过」，
    # 启动成功而迁移未发生，正是写面静默失真的可见性根因。
    from butler.store.ddl import add_column_if_absent
    add_column_if_absent(c, "trigger_runs", "trace_id", "TEXT NOT NULL DEFAULT ''")
    c.execute("CREATE INDEX IF NOT EXISTS idx_trigger_runs_trace_id ON trigger_runs(trace_id)")
    add_column_if_absent(c, "skill_runs", "trace_id", "TEXT NOT NULL DEFAULT ''")
    c.execute("CREATE INDEX IF NOT EXISTS idx_skill_runs_trace_id ON skill_runs(trace_id)")
    c.commit()

    # ---- v1.1.1 迁移：给已有 memory_facts 表补新列 ----
    _migrate_memory_facts(c)
    _migrate_dialog_turns_target(c)


def _migrate_memory_facts(conn) -> None:
    """幂等迁移：给老库的 memory_facts 表加投喂相关列。"""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(memory_facts)").fetchall()}
    adds = [
        ("target_role", "TEXT DEFAULT ''"),
        ("feed_status", "TEXT DEFAULT 'pending'"),
        ("fed_ts", "REAL"),
        ("feed_error", "TEXT DEFAULT ''"),
        ("fed_content", "TEXT DEFAULT ''"),
    ]
    _allowed_cols = {c for c, _ in adds}
    _allowed_ddl = {d for _, d in adds}
    for col, ddl in adds:
        if col not in existing and col in _allowed_cols and ddl in _allowed_ddl:
            conn.execute(f"ALTER TABLE memory_facts ADD COLUMN {col} {ddl}")
            logger.info("memory_facts: added column %s", col)
    conn.commit()


def _migrate_dialog_turns_target(conn) -> None:
    # WO-DB-111: idempotent migration - add target column to dialog_turns
    existing = {r[1] for r in conn.execute("PRAGMA table_info(dialog_turns)").fetchall()}
    if "target" not in existing:
        conn.execute("ALTER TABLE dialog_turns ADD COLUMN target TEXT")
        logger.info("dialog_turns: added column target")
    conn.commit()


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


# ---- WO-ME-220：鉴权兼容账本（跨重启可测，替代「只进 docker logs」）----
# 存在理由：WO-ME-201 第二步的放行判据是「记账名单清零」，而那份账此前只在
# 日志流里，任何一次 restart/重建都会把它静默重置成「看起来没人用」。
_COMPAT_INITED = False


def _ensure_compat_ledger(c: sqlite3.Connection) -> None:
    global _COMPAT_INITED
    if _COMPAT_INITED:
        return
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS auth_compat_ledger (
            kind       TEXT NOT NULL,      -- pw_bearer | service_token
            ip         TEXT NOT NULL,
            path       TEXT NOT NULL,
            first_seen REAL NOT NULL,
            last_seen  REAL NOT NULL,
            hits       INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (kind, ip, path)
        );
        CREATE INDEX IF NOT EXISTS idx_compat_last ON auth_compat_ledger(last_seen);
        """
    )
    _COMPAT_INITED = True


def note_compat(kind: str, ip: str, path: str) -> None:
    """记一笔（来源, 接口）用过某条兼容通道。调用方负责节流。"""
    c = get_conn()
    now = time.time()
    with _lock:
        _ensure_compat_ledger(c)
        c.execute(
            "INSERT INTO auth_compat_ledger(kind, ip, path, first_seen, last_seen, hits) "
            "VALUES(?,?,?,?,?,1) "
            "ON CONFLICT(kind, ip, path) DO UPDATE SET last_seen=excluded.last_seen, hits=hits+1",
            (kind, ip, path, now, now),
        )
        c.commit()


def compat_open(since: float) -> list[dict]:
    """自 since 之后仍活跃过的兼容通道调用清单（观测用，不作授权）。"""
    c = get_conn()
    with _lock:
        _ensure_compat_ledger(c)
        rows = c.execute(
            "SELECT kind, ip, path, first_seen, last_seen, hits FROM auth_compat_ledger "
            "WHERE last_seen >= ? ORDER BY last_seen DESC",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]
