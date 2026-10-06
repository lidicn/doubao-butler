"""§13.4 ADM 公共收件箱：butler/inbox/{speak,notify,tv}——任何仓投递事件，DB 异步过闸后播出/分发。

闸（fail-closed）：
  1. 载荷 schema 校验：text/source/trace_id 必填、长度受限，违规丢弃 + 审计日志；
  2. 每来源限流（滑动窗口，默认 10 条/分钟）；
  3. 每来源×通道冷却（同文本 60s 内不重复）；
  4. 每来源 speak 每日预算（决策 11 口径，默认 3 次/日）。

格12（H2 2026-09-30）：一次投件恰好落一行 `inbox_events`（通道/来源/trace/原文/结果/
原因）。此前"是哪句话"只存在于 docker logs，而日志窗只有 30 分钟 ⇒ 事后不可查。
新表 `DROP TABLE inbox_events` 即回滚，⛔ 动已落盘的 inbox_daily_usage。

夜间/静默时段由 TTSQueue 自身负责（quiet window），本模块不重复实现。

偏差记录：adm/* 状态的离线语义由「60s retained 心跳 + 优雅停机写 offline」承载；
paho 单一 will 已被 PUB_STATUS 占用（既有 butler/status/state LWT 不动），无法对
adm 主题双写 LWT。已在 v2.5 交付记录标注，待 DCD 认可或换独立状态上报通道。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path

from butler.config import get_settings
from butler.logging_setup import get_logger
from butler.notify.singleton import get_router as get_notify_router
from butler.tts.helper import enqueue_tts

logger = get_logger("butler.bus.inbox")

CHANNELS = ("speak", "notify", "tv")
MAX_TEXT_LEN = 1000
MAX_FIELD_LEN = 64

# 格12：投件事件台账的取值域与留存口径
OUTCOME_ACCEPTED = "accepted"
OUTCOME_DROPPED = "dropped"
OUTCOME_REJECTED = "rejected"
OUTCOME_FAILED = "failed"
EVENT_TEXT_CAP = 500
EVENT_KEEP_DAYS_DEFAULT = 14
EVENT_PRUNE_INTERVAL_S = 3600.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


class InboxGate:
    """收件箱闸：校验 → 限流 → 冷却 → 预算 → 分发。"""

    def __init__(self, rt=None):
        self.rt = rt
        self._rate: dict[str, deque] = defaultdict(deque)
        self._cool: dict[tuple, float] = {}
        self._conn: sqlite3.Connection | None = None
        self._last_prune = 0.0
        self.record_errors = 0          # 格12：台账写失败必须可数，⛔ 静默

    # ── 配置（边界可配，默认符合 §13.4） ──
    @property
    def enabled(self) -> bool:
        return _env_flag("BUTLER_INBOX_ENABLED", True)

    @property
    def rate_per_min(self) -> int:
        return _env_int("BUTLER_INBOX_RATE_PER_MIN", 10)

    @property
    def cooldown_s(self) -> float:
        return float(_env_int("BUTLER_INBOX_COOLDOWN_S", 60))

    @property
    def speak_daily_limit(self) -> int:
        return _env_int("BUTLER_INBOX_SPEAK_DAILY_LIMIT", 3)

    # ── 审计：sqlite 记账（speak 预算） ──
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            s = get_settings()
            Path(s.data_dir).mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(str(Path(s.data_dir) / "inbox.db"), check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS inbox_daily_usage (
                    source TEXT NOT NULL,
                    date   TEXT NOT NULL,
                    count  INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (source, date)
                );
                CREATE TABLE IF NOT EXISTS inbox_events (
                    ts        REAL    NOT NULL,
                    day       TEXT    NOT NULL,
                    channel   TEXT    NOT NULL,
                    source    TEXT    NOT NULL,
                    trace_id  TEXT    NOT NULL,
                    outcome   TEXT    NOT NULL,
                    reason    TEXT    NOT NULL DEFAULT '',
                    text_len  INTEGER NOT NULL DEFAULT 0,
                    text_sha  TEXT    NOT NULL DEFAULT '',
                    text      TEXT    NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_inbox_events_ts ON inbox_events(ts);
                CREATE INDEX IF NOT EXISTS idx_inbox_events_trace ON inbox_events(trace_id);
                """
            )
            self._conn = c
        return self._conn

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def budget_used(self, source: str) -> int:
        row = self._db().execute(
            "SELECT count FROM inbox_daily_usage WHERE source=? AND date=?",
            (source, self._today()),
        ).fetchone()
        return int(row[0]) if row else 0

    def budget_consume(self, source: str) -> int:
        c = self._db()
        c.execute(
            """INSERT INTO inbox_daily_usage (source, date, count) VALUES (?, ?, 1)
               ON CONFLICT(source, date) DO UPDATE SET count = count + 1""",
            (source, self._today()),
        )
        c.commit()
        return self.budget_used(source)

    # ── 格12：投件事件台账（一次投件恰好一行，含被拒的） ──
    @staticmethod
    def _peek(payload) -> tuple[str, str, str]:
        """尽量从原始载荷里抠出 source/trace/text——被 validate 拒掉的投件最需要留原文。"""
        if not isinstance(payload, dict):
            return "", "", str(payload)
        source = str(payload.get("source", ""))[:MAX_FIELD_LEN]
        trace = str(payload.get("trace_id", ""))[:MAX_FIELD_LEN]
        text = str(payload.get("text", ""))
        return source, trace, text

    def _record(self, topic: str, payload, outcome: str, reason: str) -> None:
        channel = topic.rsplit("/", 1)[-1] if "/" in topic else topic
        source, trace, text = self._peek(payload)
        row = (time.time(), self._today(), channel[:MAX_FIELD_LEN], source, trace,
               outcome, reason[:200], len(text),
               hashlib.sha1(text.encode("utf-8")).hexdigest()[:12] if text else "",
               text[:EVENT_TEXT_CAP])
        try:
            c = self._db()
            c.execute(
                """INSERT INTO inbox_events
                   (ts, day, channel, source, trace_id, outcome, reason,
                    text_len, text_sha, text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
            c.commit()
            self._prune_if_due(c)
        except Exception as e:
            # 台账写不进去⛔把分发打回（投件已处理完），但必须响：纪律 4。
            self.record_errors += 1
            logger.error("INBOX_EVENT_WRITE_FAILED channel=%s source=%s trace=%s "
                         "outcome=%s err=%s cumulative=%d",
                         channel, source, trace, outcome, e, self.record_errors)

    def _prune_if_due(self, c: sqlite3.Connection) -> None:
        """留存窗外的行按小时清一次（默认 14 天，0=不清）。无界堆积是另一种失真。"""
        days = _env_int("BUTLER_INBOX_EVENT_DAYS", EVENT_KEEP_DAYS_DEFAULT)
        if days <= 0:
            return
        now = time.time()
        if now - self._last_prune < EVENT_PRUNE_INTERVAL_S:
            return
        self._last_prune = now
        c.execute("DELETE FROM inbox_events WHERE ts < ?", (now - days * 86400.0,))
        c.commit()

    # ── 闸 ──
    def validate(self, payload) -> tuple[bool, str, dict]:
        if not isinstance(payload, dict):
            return False, "payload 非对象", {}
        text = str(payload.get("text", "")).strip()
        if not text:
            return False, "text 缺失", {}
        if len(text) > MAX_TEXT_LEN:
            return False, f"text 超长({len(text)}>{MAX_TEXT_LEN})", {}
        source = str(payload.get("source", "")).strip()
        if not source or len(source) > MAX_FIELD_LEN:
            return False, "source 缺失或超长", {}
        trace = str(payload.get("trace_id", "")).strip()
        if not trace or len(trace) > MAX_FIELD_LEN:
            return False, "trace_id 缺失或超长", {}
        title = str(payload.get("title", "")).strip()[:MAX_FIELD_LEN]
        try:
            priority = min(5, max(1, int(payload.get("priority", 3))))
        except (TypeError, ValueError):
            priority = 3
        room = str(payload.get("room", "")).strip()[:32]
        return True, "", {
            "text": text, "source": source, "trace_id": trace,
            "title": title, "priority": priority, "room": room,
            "override_quiet": bool(payload.get("override_quiet", False)),
        }

    def rate_ok(self, source: str) -> bool:
        now = time.time()
        dq = self._rate[source]
        while dq and now - dq[0] > 60.0:
            dq.popleft()
        if len(dq) >= self.rate_per_min:
            return False
        dq.append(now)
        return True

    def cool_ok(self, channel: str, source: str, text: str) -> bool:
        key = (channel, source, hashlib.sha1(text.encode("utf-8")).hexdigest()[:12])
        now = time.time()
        last = self._cool.get(key, 0.0)
        if now - last < self.cooldown_s:
            return False
        if len(self._cool) > 4096:
            for k in [k for k, v in self._cool.items() if now - v > self.cooldown_s * 10]:
                self._cool.pop(k, None)
        self._cool[key] = now
        return True

    # ── 分发 ──
    async def handle(self, topic: str, payload: dict) -> None:
        """格12：唯一记账点。`_dispatch` 的每个出口都带 (outcome, reason)，
        所以新增分支不可能悄悄漏掉台账——漏 return 就编译不过/返回 None 被测出。"""
        outcome, reason = await self._dispatch(topic, payload)
        self._record(topic, payload, outcome, reason)

    async def _dispatch(self, topic: str, payload: dict) -> tuple[str, str]:
        channel = topic.rsplit("/", 1)[-1]
        if channel not in CHANNELS:
            return OUTCOME_DROPPED, "未知通道"
        if not self.enabled:
            logger.info("INBOX_DROP channel=%s reason=inbox_disabled", channel)
            return OUTCOME_DROPPED, "收件箱已关闭"
        ok, why, note = self.validate(payload)
        if not ok:
            logger.warning("INBOX_DROP channel=%s reason=%s topic=%s", channel, why, topic)
            return OUTCOME_DROPPED, why
        if not self.rate_ok(note["source"]):
            logger.warning("INBOX_DROP channel=%s source=%s trace=%s reason=限流(%d/分钟)",
                           channel, note["source"], note["trace_id"], self.rate_per_min)
            return OUTCOME_DROPPED, "限流(%d/分钟)" % self.rate_per_min
        if not self.cool_ok(channel, note["source"], note["text"]):
            logger.info("INBOX_DROP channel=%s source=%s trace=%s reason=冷却中",
                        channel, note["source"], note["trace_id"])
            return OUTCOME_DROPPED, "冷却中"

        if channel == "speak":
            used = self.budget_used(note["source"])
            if used >= self.speak_daily_limit:
                logger.warning("INBOX_DROP channel=speak source=%s trace=%s reason=每日预算用尽(%d/%d)",
                               note["source"], note["trace_id"], used, self.speak_daily_limit)
                return OUTCOME_DROPPED, "每日预算用尽(%d/%d)" % (used, self.speak_daily_limit)
            accepted = enqueue_tts(
                note["text"], priority=note["priority"], room=note["room"],
                member=note["source"], override_quiet=note["override_quiet"],
                trace_id=note["trace_id"],
            )
            if accepted:
                today = self.budget_consume(note["source"])
                logger.info("INBOX_DONE channel=speak source=%s trace=%s budget=%d/%d",
                            note["source"], note["trace_id"], today, self.speak_daily_limit)
                return OUTCOME_ACCEPTED, "budget=%d/%d" % (today, self.speak_daily_limit)
            logger.info("INBOX_DROP channel=speak source=%s trace=%s reason=队列拒绝",
                        note["source"], note["trace_id"])
            return OUTCOME_REJECTED, "队列拒绝"

        router = get_notify_router()
        if router is None:
            logger.warning("INBOX_DROP channel=%s source=%s trace=%s reason=路由未初始化",
                           channel, note["source"], note["trace_id"])
            return OUTCOME_DROPPED, "路由未初始化"
        try:
            if channel == "notify":
                res = await router.notify(
                    "bark", note["text"],
                    title=note["title"] or f"管家收件箱 · {note['source']}",
                    bark_kwargs={"group": note["source"]},
                )
                delivered = res.results.get("bark")
                okflag = bool(delivered and delivered.ok)
            else:  # tv：与 notify_routes 现行弹窗载荷同形
                payload_tv = {
                    "title": note["title"] or note["source"],
                    "content": note["text"],
                    "type": "info",
                    "duration": 8000,
                    "important": False,
                    "tts_url": "",
                    "tts_volume": 80,
                    "pause_media": False,
                    "avatar_url": "",
                }
                res = await router.notify("tv", note["text"], tv_payload=payload_tv)
                delivered = res.results.get("tv")
                okflag = bool(delivered and delivered.ok)
            logger.info("INBOX_DONE channel=%s source=%s trace=%s ok=%s",
                        channel, note["source"], note["trace_id"], okflag)
            if okflag:
                return OUTCOME_ACCEPTED, "下游 ok"
            return OUTCOME_FAILED, "下游 not ok"
        except Exception as e:
            logger.warning("INBOX_FAIL channel=%s source=%s trace=%s err=%s",
                           channel, note["source"], note["trace_id"], e)
            return OUTCOME_FAILED, "分发异常 %s" % type(e).__name__
