"""Agent ReAct 轨迹记录：thought / action / observation 落库，可回放复盘。

一次 Agent.run 对应一条 trace（trace_id），每步 ReAct 循环对应一个 step。
轨迹是可观察性的地基：失败定位、技能沉淀、对账都依赖它。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from butler.store.db import get_conn
from butler.logging_setup import get_logger

logger = get_logger("butler.agent_trace")


@dataclass
class TraceStep:
    step: int
    role: str                # thought | action | observation | final | escape
    tool: str = ""
    args: str = ""
    result: str = ""
    error_code: str = ""
    cost_ms: int = 0
    ts: float = field(default_factory=time.time)


class AgentTracer:
    """一次 ReAct 运行的轨迹收集器。轻量、内存优先，finish 时落库。"""

    def __init__(self, user_text: str = "", member: str = "", source: str = "active",
                 trace_id: str = ""):
        # v2.6#2：上游（唤醒/TV 语音/触发器/HTTP）已给 id 就继承，一句话一条 trace
        self.trace_id = (trace_id or uuid.uuid4().hex[:16])[:32]
        self.user_text = user_text
        self.member = member
        self.source = source
        self.steps: list[TraceStep] = []
        self.start_ts = time.time()
        self.end_ts: float | None = None
        self.status: str = "running"   # running | ok | timeout | max_iter | error | escaped
        self.error: str = ""
        self.llm_calls: int = 0
        self.tool_calls: int = 0

    # ── 记录 ──────────────────────────────────────────────
    def thought(self, text: str) -> None:
        self.steps.append(TraceStep(step=len(self.steps) + 1, role="thought", result=text[:500]))

    def action(self, tool: str, args: dict | str, cost_ms: int = 0) -> None:
        self.tool_calls += 1
        a = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        self.steps.append(TraceStep(step=len(self.steps) + 1, role="action",
                                     tool=tool, args=a[:500], cost_ms=cost_ms))

    def observation(self, tool: str, result: str, error_code: str = "", cost_ms: int = 0) -> None:
        self.steps.append(TraceStep(step=len(self.steps) + 1, role="observation",
                                     tool=tool, result=str(result)[:800],
                                     error_code=error_code, cost_ms=cost_ms))

    def final(self, text: str) -> None:
        self.steps.append(TraceStep(step=len(self.steps) + 1, role="final", result=text[:1000]))

    def escape(self, reason: str) -> None:
        self.steps.append(TraceStep(step=len(self.steps) + 1, role="escape", result=reason[:500]))

    # ── 收尾落库 ──────────────────────────────────────────
    def finish(self, status: str = "ok", error: str = "") -> None:
        self.status = status
        self.error = error
        self.end_ts = time.time()
        self._persist()

    def _persist(self) -> None:
        try:
            db = get_conn()
            total_ms = int((self.end_ts - self.start_ts) * 1000) if self.end_ts else 0
            steps_json = json.dumps([asdict(s) for s in self.steps], ensure_ascii=False)
            db.execute(
                """INSERT INTO agent_traces
                   (trace_id, ts, member, source, user_text, status, error,
                    total_ms, llm_calls, tool_calls, step_count, steps_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self.trace_id, self.start_ts, self.member, self.source,
                 self.user_text, self.status, self.error, total_ms,
                 self.llm_calls, self.tool_calls, len(self.steps), steps_json),
            )
            db.commit()
            logger.info("trace %s saved status=%s steps=%d total_ms=%d",
                        self.trace_id, self.status, len(self.steps), total_ms)
        except Exception as e:
            logger.warning("persist trace failed: %s", e)

    # ── 查询（静态） ───────────────────────────────────────
    @staticmethod
    def list_traces(limit: int = 50, member: str = "", status: str = "") -> list[dict]:
        db = get_conn()
        q = "SELECT trace_id, ts, member, user_text, status, error, total_ms, llm_calls, tool_calls, step_count FROM agent_traces"
        conds, params = [], []
        if member:
            conds.append("member = ?"); params.append(member)
        if status:
            conds.append("status = ?"); params.append(status)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in db.execute(q, params).fetchall()]

    @staticmethod
    def get_trace(trace_id: str) -> dict | None:
        db = get_conn()
        row = db.execute("SELECT * FROM agent_traces WHERE trace_id = ?", (trace_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["steps"] = json.loads(d.get("steps_json") or "[]")
        except Exception:
            d["steps"] = []
        d.pop("steps_json", None)
        return d
