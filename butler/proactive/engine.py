"""主动问询引擎：管家主动发起语音提问，用户回复后执行动作。

v1.6 P0-3：
  - 触发条件（定位+模式+时间+设备状态）→ 管家判断需要问询
  - 小爱 TTS 主动提问 → 开启收音窗口 → 用户回复 → 管家理解意图 → 执行动作
  - 同一事件仅询问一次，睡眠/观影/会客模式禁用
  - 问询超时 30 秒自动退出

简化版实现（v1.6）：
  - 触发条件判断 + TTS 主动提问 + Bark 同步推送
  - 问询记录去重（SQLite）
  - 用户回复通过 API 接收（后续跟小爱耳朵集成语音回复）
  - 模式禁用规则
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from butler.config import get_settings
from butler.logging_setup import get_logger, warn_throttled

logger = get_logger("butler.proactive.engine")

_conn: sqlite3.Connection | None = None

# 问询状态
STATUS_PENDING = "pending"      # 已提问，等待回复
STATUS_ANSWERED = "answered"    # 已回复
STATUS_TIMEOUT = "timeout"      # 超时未回复
STATUS_DISMISSED = "dismissed"  # 用户忽略

# 禁用主动问询的模式
DISABLED_MODES = {"sleep", "movie", "guest"}


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        s = get_settings()
        Path(s.data_dir).mkdir(parents=True, exist_ok=True)
        db_path = Path(s.data_dir) / "butler.db"
        c = sqlite3.connect(str(db_path), check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        _conn = c
        _init(c)
    return _conn


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS proactive_daily_usage (
            role_id TEXT NOT NULL,
            date TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (role_id, date)
        );
        CREATE TABLE IF NOT EXISTS proactive_inquiries (
            id          TEXT PRIMARY KEY,            -- INQ-YYYYMMDD-NNN
            event_key   TEXT NOT NULL,               -- 事件去重键
            title       TEXT NOT NULL,               -- 问询标题
            question    TEXT NOT NULL,               -- 问询内容（TTS 播报）
            options     TEXT,                         -- JSON 选项列表（可选）
            status      TEXT NOT NULL DEFAULT 'pending',
            answer      TEXT,                         -- 用户回复
            answered_at REAL,
            created_at  REAL NOT NULL,
            expires_at  REAL NOT NULL,
            context     TEXT                          -- JSON 上下文（触发条件等）
        );
        CREATE INDEX IF NOT EXISTS idx_inquiry_status ON proactive_inquiries(status);
        CREATE INDEX IF NOT EXISTS idx_inquiry_event ON proactive_inquiries(event_key);
        CREATE INDEX IF NOT EXISTS idx_inquiry_created ON proactive_inquiries(created_at);
        """
    )
    # v2.8: 幂等补列 —— 老库无 role_id/feedback，预算与降频需要这两列
    # 格2（2026-09-30）：只吞「列已存在」，其余 raise
    from butler.store.ddl import add_column_if_absent
    for _col, _ddl in (("role_id", "TEXT NOT NULL DEFAULT 'butler'"),
                       ("feedback", "TEXT NOT NULL DEFAULT ''")):
        add_column_if_absent(c, "proactive_inquiries", _col, _ddl)
    c.execute("CREATE INDEX IF NOT EXISTS idx_inquiry_role_created "
                "ON proactive_inquiries(role_id, created_at)")
    c.commit()


@dataclass
class Inquiry:
    """主动问询。"""
    id: str
    event_key: str
    title: str
    question: str
    options: list[str] = field(default_factory=list)
    status: str = STATUS_PENDING
    answer: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    context: dict = field(default_factory=dict)


class ProactiveEngine:
    """主动问询引擎。"""

    def __init__(self, rt=None):
        self.rt = rt
        self._event_counter: dict[str, int] = {}
        self._scenes: list[dict] = []
        self._scenes_loaded: float = 0
        self._load_scenes()

    @property
    def current_mode(self) -> str:
        if self.rt and hasattr(self.rt, "mode_engine"):
            return self.rt.mode_engine.current
        return "daily"

    def is_enabled(self) -> bool:
        """当前模式是否允许主动问询。"""
        return self.current_mode not in DISABLED_MODES

    def is_event_asked(self, event_key: str, cooldown_hours: int = 24) -> bool:
        """检查事件是否已经问询过（去重）。"""
        c = get_conn()
        cutoff = time.time() - cooldown_hours * 3600
        row = c.execute(
            "SELECT 1 FROM proactive_inquiries WHERE event_key=? AND created_at>=? LIMIT 1",
            (event_key, cutoff),
        ).fetchone()
        return row is not None

    # v2.8: 每角色每日主动服务预算（默认3次）
    DEFAULT_DAILY_LIMIT = 3

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def get_daily_count(self, role_id: str = "butler") -> int:
        """今日该角色已发起主动服务次数。"""
        c = get_conn()
        row = c.execute(
            "SELECT count FROM proactive_daily_usage WHERE role_id=? AND date=?",
            (role_id, self._today()),
        ).fetchone()
        return row[0] if row else 0

    def sweep_expired(self, when: float = None) -> int:
        """把已过 expires_at 的 pending 问询落为 timeout，返回改动条数。

        决策 11 的"被忽略"要先成为一条状态记录，才可能被降频读到；此前只有
        get_pending()（HTTP 轮询路径）会写这个状态，现网 timeout 行数恒为 0。
        """
        if when is None:
            when = time.time()
        c = get_conn()
        cur = c.execute(
            "UPDATE proactive_inquiries SET status=? "
            "WHERE status=? AND expires_at>0 AND expires_at<?",
            (STATUS_TIMEOUT, STATUS_PENDING, when),
        )
        n = int(cur.rowcount or 0)
        # ⛔ 提交不许挂在 n 上：pysqlite 旧式隔离在 DML 之前就先发 BEGIN，命中 0 行的
        # UPDATE 同样让连接带着未结束的事务持有 WAL 写锁（现网 09:14:30 的写面停摆）。
        c.commit()
        if n:
            logger.info("proactive expiry sweep: %d pending -> timeout", n)
        return n

    def can_proactive(self, role_id: str = "butler", limit: int = None) -> bool:
        """检查今日是否还有预算。"""
        if limit is None:
            limit = self.effective_daily_limit(role_id)
        used = self.get_daily_count(role_id)
        if used >= limit:
            logger.info("proactive budget exhausted: role=%s used=%d limit=%d", role_id, used, limit)
            return False
        return True

    def consume_budget(self, role_id: str = "butler") -> None:
        """消耗一次预算。"""
        c = get_conn()
        today = self._today()
        c.execute(
            """INSERT INTO proactive_daily_usage (role_id, date, count)
               VALUES (?, ?, 1)
               ON CONFLICT(role_id, date) DO UPDATE SET count = count + 1""",
            (role_id, today),
        )
        c.commit()
        logger.info("proactive budget consumed: role=%s today=%d", role_id, self.get_daily_count(role_id))

    def effective_daily_limit(self, role_id: str = "butler") -> int:
        """v2.8: 当日实际上限 = 配置值 - 近 7 天负反馈次数（下限可配，0=关闭）。"""
        s = get_settings()
        base = s.proactive_role_limits.get(role_id)
        if base is None:
            base = s.proactive_daily_limit
        if base <= 0:
            return 0
        neg = self.negative_count(role_id)
        return max(max(0, s.proactive_reduce_floor), base - neg)

    def negative_count(self, role_id: str = "butler", days: int = 7) -> int:
        """近 N 天该角色的负反馈条数。"""
        c = get_conn()
        cutoff = time.time() - days * 86400
        row = c.execute(
            "SELECT COUNT(*) FROM proactive_inquiries "
            "WHERE role_id=? AND feedback='negative' AND created_at>=?",
            (role_id, cutoff),
        ).fetchone()
        return int(row[0] or 0)

    def budget_snapshot(self, role_id: str = "butler") -> dict:
        used = self.get_daily_count(role_id)
        lim = self.effective_daily_limit(role_id)
        s = get_settings()
        return {
            "role_id": role_id,
            "used_today": used,
            "limit_today": lim,
            "remaining": max(0, lim - used),
            "negative_7d": self.negative_count(role_id),
            "base_limit": s.proactive_daily_limit,
            "role_override": s.proactive_role_limits.get(role_id),
            "reduce_floor": s.proactive_reduce_floor,
        }

    @staticmethod
    def classify_feedback(text: str) -> str:
        """命中负反馈词表即 negative；纯确定性，不走 LLM。"""
        t = (text or "").strip()
        if not t:
            return ""
        for w in get_settings().proactive_negative_words:
            if w and w in t:
                return "negative"
        return ""

    def set_feedback(self, inquiry_id: str, kind: str) -> dict:
        """显式标注反馈（语音外的通道也能降频）。"""
        if kind not in ("negative", "positive", ""):
            return {"ok": False, "error": "kind must be negative|positive|empty"}
        c = get_conn()
        row = c.execute(
            "SELECT id FROM proactive_inquiries WHERE id=?", (inquiry_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "inquiry not found"}
        c.execute("UPDATE proactive_inquiries SET feedback=? WHERE id=?",
                  (kind, inquiry_id))
        c.commit()
        return {"ok": True, "id": inquiry_id, "feedback": kind}

    async def ask(self, event_key: str, title: str, question: str,
                  options: list[str] = None, context: dict = None,
                  tts_device: str = None, cooldown_hours: int = 24) -> Inquiry | None:
        """发起主动问询。返回 None 如果被禁用或已问询过。"""
        # 模式检查
        if not self.is_enabled():
            logger.debug("proactive ask disabled in mode %s", self.current_mode)
            return None

        # 去重检查
        if self.is_event_asked(event_key, cooldown_hours):
            logger.debug("proactive event already asked: %s", event_key)
            return None

        # v2.8: 每日预算检查（context 里带 role_id）
        _role = (context or {}).get("role_id", "butler")
        self.sweep_expired()
        if not self.can_proactive(_role):
            logger.info("proactive ask skipped (budget): role=%s event=%s", _role, event_key)
            return None
        self.consume_budget(_role)

        # 生成问询 ID
        inq_id = self._gen_id()
        now = time.time()
        expires_at = now + get_settings().proactive_inquiry_timeout_sec

        inquiry = Inquiry(
            id=inq_id,
            event_key=event_key,
            title=title,
            question=question,
            options=options or [],
            status=STATUS_PENDING,
            created_at=now,
            expires_at=expires_at,
            context=context or {},
        )

        # 存储
        c = get_conn()
        c.execute(
            """INSERT INTO proactive_inquiries (id, event_key, title, question, options, status, created_at, expires_at, context, role_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (inq_id, event_key, title, question,
             __import__("json").dumps(options or [], ensure_ascii=False),
             STATUS_PENDING, now, expires_at,
             __import__("json").dumps(context or {}, ensure_ascii=False), _role),
        )
        c.commit()

        logger.info("proactive ask: %s - %s", inq_id, title)

        # TTS 播报（主动提问）
        if self.rt and hasattr(self.rt, "tts"):
            try:
                # 组装播报文本：问题 + 选项
                speak_text = question
                if options:
                    opt_str = "，".join([f"{chr(65+i)}. {opt}" for i, opt in enumerate(options)])
                    speak_text += f"。可选：{opt_str}"
                await self.rt.tts.speak(
                    text=speak_text,
                    voice="xiaoyi",  # 小爱音色
                    device_id=tts_device or "xiao_living",  # 默认客厅音箱
                )
            except Exception as e:
                logger.warning("proactive tts failed: %s", e)

        # Bark 同步推送（文字版）
        if self.rt and getattr(self.rt, "bark", None):
            try:
                body = question
                if options:
                    body += "\n" + "\n".join([f"{chr(65+i)}. {opt}" for i, opt in enumerate(options)])
                await self.rt.bark.push(
                    body=body,
                    title=f"【主动问询】{title}",
                    level="active",
                    group="proactive",
                )
            except Exception as e:
                logger.debug("proactive bark failed: %s", e)

        return inquiry

    async def answer(self, inquiry_id: str, answer: str) -> dict:
        """用户回复问询。"""
        c = get_conn()
        row = c.execute(
            "SELECT * FROM proactive_inquiries WHERE id=?",
            (inquiry_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "inquiry not found"}

        if row["status"] != STATUS_PENDING:
            return {"ok": False, "error": f"inquiry already {row['status']}"}

        now = time.time()
        c.execute(
            "UPDATE proactive_inquiries SET status=?, answer=?, answered_at=?, feedback=? WHERE id=?",
            (STATUS_ANSWERED, answer, now, self.classify_feedback(answer), inquiry_id),
        )
        c.commit()

        logger.info("proactive answered: %s - %s feedback=%s",
                    inquiry_id, answer[:50], self.classify_feedback(answer) or "-")
        return {"ok": True, "id": inquiry_id, "answer": answer, "status": STATUS_ANSWERED}

    def get_pending(self, limit: int = 10) -> list[dict]:
        """获取待回复的问询。"""
        c = get_conn()
        # 先清理超时的
        now = time.time()
        c.execute(
            "UPDATE proactive_inquiries SET status=? WHERE status=? AND expires_at<?",
            (STATUS_TIMEOUT, STATUS_PENDING, now),
        )
        c.commit()

        rows = c.execute(
            "SELECT * FROM proactive_inquiries WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (STATUS_PENDING, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def list(self, status: str = None, limit: int = 50, offset: int = 0) -> list[dict]:
        """查询问询历史。"""
        c = get_conn()
        query = "SELECT * FROM proactive_inquiries WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = c.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ---- 场景库（v1.6 P1-2）----

    def _load_scenes(self) -> None:
        """加载主动问询场景库（JSON 配置文件）。"""
        try:
            from butler.config import get_settings
            s = get_settings()
            scenes_path = Path(s.data_dir) / "proactive_scenes.json"
            if scenes_path.exists():
                import json
                data = json.loads(scenes_path.read_text(encoding="utf-8"))
                self._scenes = data.get("scenes", [])
                self._scenes_loaded = time.time()
                logger.info("proactive scenes loaded: %d scenes", len(self._scenes))
            else:
                logger.debug("proactive_scenes.json not found, using empty scenes")
        except Exception as e:
            logger.warning("load proactive scenes failed: %s", e)

    def get_scenes(self, enabled_only: bool = True) -> list[dict]:
        """获取场景列表。"""
        # 每 5 分钟重新加载一次（支持热更新）
        if time.time() - self._scenes_loaded > 300:
            self._load_scenes()
        if enabled_only:
            return [s for s in self._scenes if s.get("enabled", True)]
        return self._scenes

    def get_scene(self, scene_id: str) -> dict | None:
        """根据 ID 获取场景。"""
        for s in self._scenes:
            if s.get("id") == scene_id:
                return s
        return None

    async def check_scenes(self) -> list[dict]:
        """检查所有启用的场景，触发满足条件的问询。返回触发的问询列表。"""
        if not self.is_enabled():
            return []

        triggered = []
        for scene in self.get_scenes(enabled_only=True):
            try:
                if await self._check_scene_conditions(scene):
                    inquiry_cfg = scene.get("inquiry", {})
                    event_key = f"scene_{scene['id']}"
                    cooldown = int(inquiry_cfg.get("cooldown_hours", 24))

                    if self.is_event_asked(event_key, cooldown_hours=cooldown):
                        continue

                    inquiry = await self.ask(
                        event_key=event_key,
                        title=inquiry_cfg.get("title", scene.get("name", "")),
                        question=inquiry_cfg.get("question", ""),
                        options=inquiry_cfg.get("options", []),
                        context={"scene_id": scene["id"], "scene_name": scene.get("name", "")},
                        tts_device=inquiry_cfg.get("tts_device"),
                        cooldown_hours=cooldown,
                    )
                    if inquiry:
                        triggered.append({"scene_id": scene["id"], "inquiry_id": inquiry.id})
                        logger.info("scene triggered: %s -> %s", scene["id"], inquiry.id)
            except Exception as e:
                logger.warning("check scene %s failed: %s", scene.get("id"), e)

        return triggered

    async def _check_scene_conditions(self, scene: dict) -> bool:
        """检查场景触发条件（简化版：只检查类型为 scheduled 的场景，基于时间间隔）。"""
        trigger = scene.get("trigger", {})
        trigger_type = trigger.get("type", "scheduled")

        # 事件类型场景（如 mode_changed/appliance_finished）由外部事件触发，这里跳过
        if trigger_type == "event":
            return False

        # 定时类型场景：检查是否满足基本条件
        conditions = trigger.get("conditions", [])
        for cond in conditions:
            cond_type = cond.get("type", "")

            # 室温条件
            if cond_type == "indoor_temp":
                if not await self._check_indoor_temp(cond):
                    return False

            # 用户在房间
            elif cond_type == "user_in_room":
                if not self._check_user_in_room(cond):
                    return False

            # 用户静止（久坐）
            elif cond_type == "user_still":
                if not self._check_user_still(cond):
                    return False

            # 其他条件类型暂时跳过（简化版）
            # 未知条件默认通过（避免误拦截）

        return True

    async def _check_indoor_temp(self, cond: dict) -> bool:
        """检查室温条件。"""
        if not self.rt or not hasattr(self.rt, "ha"):
            return False
        try:
            states = await self.rt.ha.get_states()
            for entity in states:
                eid = entity.get("entity_id", "")
                attrs = entity.get("attributes", {})
                if eid.startswith("sensor.") and "温度" in attrs.get("friendly_name", ""):
                    try:
                        temp = float(entity.get("state", 0))
                        op = cond.get("operator", ">=")
                        value = float(cond.get("value", 28))
                        if op == ">=" and temp >= value:
                            return True
                        if op == ">" and temp > value:
                            return True
                        if op == "<=" and temp <= value:
                            return True
                        if op == "<" and temp < value:
                            return True
                    except (ValueError, TypeError):
                        warn_throttled(logger, "proactive.indoor_cond", "proactive 室内温度条件里有值不是数（该实体跳过）")
                        continue
        except Exception as e:
            logger.debug("check indoor temp failed: %s", e)
        return False

    def _check_user_in_room(self, cond: dict) -> bool:
        """检查用户是否在房间（简化版：检查定位引擎是否有用户在家）。"""
        if not self.rt or not hasattr(self.rt, "presence_engine"):
            return False
        try:
            snapshot = self.rt.presence_engine.snapshot()
            users = snapshot.get("users", {})
            min_conf = float(cond.get("confidence", 0.6))
            for user, loc in users.items():
                if loc.get("confidence", 0) >= min_conf and loc.get("room"):
                    return True
        except Exception as e:
            logger.debug("check user in room failed: %s", e)
        return False

    def _check_user_still(self, cond: dict) -> bool:
        """检查用户是否久坐（简化版：检查用户是否在指定房间）。"""
        room = cond.get("room", "")
        if not self.rt or not hasattr(self.rt, "presence_engine"):
            return False
        try:
            snapshot = self.rt.presence_engine.snapshot()
            users = snapshot.get("users", {})
            for user, loc in users.items():
                if room in loc.get("room", "") and loc.get("confidence", 0) >= 0.6:
                    return True
        except Exception as e:
            logger.debug("check user still failed: %s", e)
        return False

    def _gen_id(self) -> str:
        date_str = time.strftime("%Y%m%d")
        key = f"inq_{date_str}"
        self._event_counter[key] = self._event_counter.get(key, 0) + 1
        return f"INQ-{date_str}-{self._event_counter[key]:03d}"
