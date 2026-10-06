"""推送风控层（PushGuard）：统一管理所有主动推送（Bark/TTS）的频率、熔断、过载。

借鉴 Node-RED TTS 队列 v3 的五层防护思想，针对管家场景优化：
  L1 模式感知：睡眠/观影模式自动降级（不只是时间窗口）
  L2 多维度熔断：按 group+level 分别计数，critical 可穿透
  L3 过载保护：Bark 合并摘要，TTS 队列+暂停
  L4 TTL 过期：执行在真正排队的 butler/tts/queue.py（PRIORITY_TTL_S），本层⛔ 重复实现
  L5 队列上限：TTS 队列上限，超过丢弃最旧

与 NR 版的关键差异：
  - 状态持久化到 SQLite（NR 用 global 变量，重启丢失）
  - 熔断按 group+level 多维度（NR 是全局的）
  - critical 穿透但不清空其他熔断状态（NR 的 P1 会清空所有熔断）
  - 过载通知走 Bark 不走 TTS（NR 过载通知走 TTS，TTS 坏了就播不出）
  - 有 API 暴露状态和管理（NR 只有 debug 节点）
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from butler.logging_setup import get_logger
from butler.store import write_failures

logger = get_logger("butler.guard")

# 优先级定义
PRIORITY_CRITICAL = "critical"
PRIORITY_WARNING = "warning"
PRIORITY_INFO = "info"

# 优先级 → TTL 那张表已拆（DCD 裁定③ 20261002:44-52 点名的「空号」：全树引用只有定义行本身）。
# 处置＝「接上」，但接在有人走的那条队列上：`butler/tts/queue.py::PRIORITY_TTL_S`
# （本层的 `_tts_queue` 被 `api/tts_routes.py:280/292` 播完再 pop，1:1 排空，条目在这里不产生年龄；
#   两套队列的去留＝裁定⑤，本批不占）。
# 本层留下的那条腿是 `record_ttl_drop()`：队列侧每次过期往 `push_audit` 写一行，让「为什么没响」可查。

# 优先级 → 是否可穿透熔断
PRIORITY_BYPASS_CIRCUIT = {
    PRIORITY_CRITICAL: True,
    PRIORITY_WARNING: False,
    PRIORITY_INFO: False,
}

# 熔断配置：每个 (group, level) 维度独立计数
CIRCUIT_BURST_LIMIT = 5  # 窗口内超过 N 条触发熔断
CIRCUIT_BURST_WINDOW = 60  # 窗口（秒）
CIRCUIT_COOLDOWN = 120  # 熔断持续时间（秒）

# 全局过载配置
GLOBAL_BURST_LIMIT = 15  # 全局 1 分钟超过 N 条触发过载
GLOBAL_OVERLOAD_DURATION = 300  # 过载持续时间（秒）

# TTS 队列配置
TTS_QUEUE_MAX = 10  # 队列上限
TTS_OVERLOAD_PAUSE = 300  # 过载暂停时间（秒）

# 决策结果
DECISION_PASS = "pass"  # 通过，正常推送
DECISION_MERGE = "merge"  # 合并到摘要（Bark 用）
DECISION_DROP = "drop"  # 丢弃（熔断/过载；TTL 的丢弃经 record_ttl_drop 回到这张表）
DECISION_QUEUE = "queue"  # 入队（TTS 用）


@dataclass
class PushDecision:
    """推送决策结果。"""
    action: str  # pass/merge/drop/queue
    reason: str = ""
    group: str = ""
    priority: str = PRIORITY_INFO
    queued_count: int = 0  # 入队后的队列长度


class PushGuard:
    """推送风控引擎：所有主动推送必须经过 check()。"""

    def __init__(self, data_dir: str = ""):
        self._db_path = str(Path(data_dir) / "push_guard.db") if data_dir else ""
        # 内存状态（轻量，重启后从 SQLite 恢复关键状态）
        self._circuit_state: dict[str, dict] = {}  # {group_level: {counts: [], cooldown_until: 0}}
        self._global_counts: list[float] = []  # 全局推送时间戳
        self._global_overload_until: float = 0
        self._tts_queue: list[dict] = []
        self._tts_lock: bool = False
        self._tts_overload_until: float = 0
        self._audit_log: list[dict] = []  # 最近 1000 条决策审计
        self._init_db()

    def _init_db(self) -> None:
        """初始化 SQLite（持久化熔断状态和审计日志）。"""
        if not self._db_path:
            return
        try:
            import sqlite3
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS push_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    channel TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    body_preview TEXT DEFAULT ''
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON push_audit(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_group ON push_audit(group_name)")
            conn.commit()
            conn.close()
            logger.info("push_guard db initialized: %s", self._db_path)
        except Exception as e:
            logger.warning("push_guard db init failed: %s", e)

    def _audit(self, channel: str, group: str, priority: str,
                action: str, reason: str, title: str = "", body: str = "") -> None:
        """记录审计日志（内存 + SQLite）。"""
        entry = {
            "ts": time.time(),
            "channel": channel,
            "group": group,
            "priority": priority,
            "action": action,
            "reason": reason,
            "title": title,
            "body_preview": body[:100] if body else "",
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-1000:]
        # 写 SQLite
        if self._db_path:
            try:
                import sqlite3
                conn = sqlite3.connect(self._db_path)
                conn.execute(
                    "INSERT INTO push_audit (ts, channel, group_name, priority, action, reason, title, body_preview) VALUES (?,?,?,?,?,?,?,?)",
                    (entry["ts"], channel, group, priority, action, reason, title, entry["body_preview"]),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning("push_audit write failed: %s: %s",
                               type(e).__name__, str(e)[:200])
                write_failures.record("guard/push_guard.push_audit",
                                      "%s: %s" % (type(e).__name__, str(e)[:200]),
                                      table="push_audit")

    def _check_circuit(self, group: str, priority: str) -> tuple[bool, str]:
        """检查 (group, priority) 维度的熔断状态。返回 (是否通过, 原因)。"""
        # critical 可穿透
        if PRIORITY_BYPASS_CIRCUIT.get(priority, False):
            return True, "critical bypass circuit"

        key = f"{group}:{priority}"
        now = time.time()
        state = self._circuit_state.get(key, {"counts": [], "cooldown_until": 0})

        # 检查是否在冷却中
        if now < state["cooldown_until"]:
            remaining = int(state["cooldown_until"] - now)
            return False, f"circuit cooldown ({remaining}s left)"

        # 清理过期计数
        state["counts"] = [t for t in state["counts"] if now - t < CIRCUIT_BURST_WINDOW]

        # 检查是否超过阈值
        if len(state["counts"]) >= CIRCUIT_BURST_LIMIT:
            state["cooldown_until"] = now + CIRCUIT_COOLDOWN
            state["counts"] = []
            self._circuit_state[key] = state
            logger.warning("circuit triggered: %s, cooldown %ds", key, CIRCUIT_COOLDOWN)
            return False, f"circuit burst ({CIRCUIT_BURST_LIMIT}/{CIRCUIT_BURST_WINDOW}s)"

        # 通过，记录这次推送
        state["counts"].append(now)
        self._circuit_state[key] = state
        return True, ""

    def _check_global_overload(self) -> tuple[bool, str]:
        """检查全局过载。返回 (是否通过, 原因)。"""
        now = time.time()

        # 检查是否在过载暂停中
        if now < self._global_overload_until:
            remaining = int(self._global_overload_until - now)
            return False, f"global overload ({remaining}s left)"

        # 清理过期计数
        self._global_counts = [t for t in self._global_counts if now - t < 60]

        # 检查是否超过全局阈值
        if len(self._global_counts) >= GLOBAL_BURST_LIMIT:
            self._global_overload_until = now + GLOBAL_OVERLOAD_DURATION
            self._global_counts = []
            logger.warning("global overload triggered: %d/min, pause %ds",
                           GLOBAL_BURST_LIMIT, GLOBAL_OVERLOAD_DURATION)
            return False, f"global overload ({GLOBAL_BURST_LIMIT}/min)"

        # 通过
        self._global_counts.append(now)
        return True, ""

    # ---- Bark 风控 ----

    def check_bark(self, title: str = "", body: str = "",
                    group: str = "default", priority: str = PRIORITY_INFO,
                    level: str = "active") -> PushDecision:
        """Bark 推送风控检查。

        返回决策：
          - pass: 正常推送
          - merge: 合并到摘要（频率过高时，多条合并成一条推送）
          - drop: 丢弃（熔断/过载）
        """
        # L1 模式感知（由调用方在调用前检查模式，这里不重复）
        # L2 多维度熔断
        passed, reason = self._check_circuit(group, priority)
        if not passed:
            self._audit("bark", group, priority, DECISION_DROP, reason, title, body)
            return PushDecision(action=DECISION_DROP, reason=reason, group=group, priority=priority)

        # L3 全局过载（Bark 在过载时合并摘要，不丢弃）
        passed, reason = self._check_global_overload()
        if not passed:
            # 过载时 info/warning 合并，critical 仍通过
            if PRIORITY_BYPASS_CIRCUIT.get(priority, False):
                self._audit("bark", group, priority, DECISION_PASS, "critical bypass overload", title, body)
                return PushDecision(action=DECISION_PASS, reason="critical bypass overload",
                                    group=group, priority=priority)
            self._audit("bark", group, priority, DECISION_MERGE, reason, title, body)
            return PushDecision(action=DECISION_MERGE, reason=reason, group=group, priority=priority)

        # 通过
        self._audit("bark", group, priority, DECISION_PASS, "", title, body)
        return PushDecision(action=DECISION_PASS, group=group, priority=priority)

    # ---- TTS 风控 ----

    def check_tts(self, text: str = "", device: str = "", room: str = "",
                   priority: str = PRIORITY_INFO, volume: int = 30) -> PushDecision:
        """TTS 推送风控检查。

        返回决策：
          - pass: 立即播报（P1 插队或队列为空）
          - queue: 入队等待
          - drop: 丢弃（熔断/过载/队列满；档位 TTL 见 tts/queue.py 与 record_ttl_drop）
        """
        now = time.time()

        # L2 多维度熔断（TTS 用 group="tts"）
        passed, reason = self._check_circuit("tts", priority)
        if not passed:
            self._audit("tts", "tts", priority, DECISION_DROP, reason, "", text)
            return PushDecision(action=DECISION_DROP, reason=reason, priority=priority)

        # L3 TTS 过载暂停
        if now < self._tts_overload_until:
            remaining = int(self._tts_overload_until - now)
            # critical 可穿透过载
            if PRIORITY_BYPASS_CIRCUIT.get(priority, False):
                self._audit("tts", "tts", priority, DECISION_PASS, "critical bypass overload", "", text)
                return PushDecision(action=DECISION_PASS, reason="critical bypass overload", priority=priority)
            self._audit("tts", "tts", priority, DECISION_DROP, f"tts overload ({remaining}s)", "", text)
            return PushDecision(action=DECISION_DROP, reason=f"tts overload ({remaining}s)", priority=priority)

        # L5 队列管理
        item = {
            "text": text, "device": device, "room": room,
            "priority": priority, "volume": volume, "ts": now,
        }

        # critical 插队到队首 + 释放锁
        if priority == PRIORITY_CRITICAL:
            self._tts_queue.insert(0, item)
            self._tts_lock = False
            # 触发熔断清理（critical 穿透时不清空其他维度，只清理 tts 维度）
            key = "tts:critical"
            if key in self._circuit_state:
                self._circuit_state[key]["cooldown_until"] = 0
            queued = len(self._tts_queue)
            self._audit("tts", "tts", priority, DECISION_QUEUE, "critical插队", "", text)
            return PushDecision(action=DECISION_QUEUE, reason="critical插队",
                                priority=priority, queued_count=queued)

        # 同 device 替换（借鉴 NR）
        replaced = False
        if device:
            for i, q in enumerate(self._tts_queue):
                if q.get("device") == device:
                    self._tts_queue[i] = item
                    replaced = True
                    break

        if not replaced:
            # warning 插队到队首（critical 之后）
            if priority == PRIORITY_WARNING:
                # 找到第一个非 critical 的位置插入
                insert_idx = 0
                for i, q in enumerate(self._tts_queue):
                    if q.get("priority") != PRIORITY_CRITICAL:
                        insert_idx = i
                        break
                else:
                    insert_idx = len(self._tts_queue)
                self._tts_queue.insert(insert_idx, item)
            else:
                self._tts_queue.append(item)

        # 队列上限：超过则丢弃最旧的非 critical
        while len(self._tts_queue) > TTS_QUEUE_MAX:
            # 从队尾找第一个非 critical 丢弃
            dropped = False
            for i in range(len(self._tts_queue) - 1, -1, -1):
                if self._tts_queue[i].get("priority") != PRIORITY_CRITICAL:
                    self._tts_queue.pop(i)
                    dropped = True
                    break
            if not dropped:
                break  # 全是 critical，保留

        # 检查是否触发过载（队列满 + 持续积压）
        if len(self._tts_queue) >= TTS_QUEUE_MAX:
            self._tts_overload_until = now + TTS_OVERLOAD_PAUSE
            logger.warning("tts overload: queue %d, pause %ds", len(self._tts_queue), TTS_OVERLOAD_PAUSE)

        queued = len(self._tts_queue)
        self._audit("tts", "tts", priority, DECISION_QUEUE, f"入队 ({queued}/{TTS_QUEUE_MAX})", "", text)
        return PushDecision(action=DECISION_QUEUE, reason=f"入队 ({queued}/{TTS_QUEUE_MAX})",
                            priority=priority, queued_count=queued)

    def tts_pop(self) -> dict | None:
        """TTS 出队（播报完成后调用）。返回队首消息或 None。"""
        if self._tts_lock or not self._tts_queue:
            return None
        self._tts_lock = True
        item = self._tts_queue.pop(0)
        return item

    def tts_unlock(self) -> None:
        """TTS 播报完成，释放锁，允许下一条。"""
        self._tts_lock = False

    # ---- 队列侧过期审计（DCD 裁定③）----

    def record_ttl_drop(self, *, text: str = "", priority: str = PRIORITY_INFO,
                        age_s: float = 0.0, ttl_s: float = 0.0,
                        job_id: str = "", trace_id: str = "",
                        channel: str = "tts", group: str = "tts") -> None:
        """队列里过期的那条写进 `push_audit`：面板与 `/api/guard/audit` 已经在读这张表。

        ⛔ 抛异常——风控的库写不动只是少一行审计，⛔ 因此把播放链路带下去（与 `_audit` 同策）。
        job/trace 并进 reason 文本：为审计加两列要走 DDL 守护，不值。
        """
        reason = "ttl expired (age %.0fs > %.0fs) job=%s trace=%s" % (
            age_s, ttl_s, job_id or "-", trace_id or "-")
        try:
            self._audit(channel, group, priority, DECISION_DROP, reason, "", text)
        except Exception as e:
            logger.warning("record_ttl_drop failed (non-blocking): %s: %s",
                           type(e).__name__, str(e)[:200])

    # ---- 状态查询与管理 ----

    def get_status(self) -> dict:
        """获取风控状态（用于 API 展示）。"""
        now = time.time()
        circuits = []
        for key, state in self._circuit_state.items():
            if state.get("cooldown_until", 0) > now or state.get("counts"):
                parts = key.split(":")
                circuits.append({
                    "group": parts[0] if len(parts) > 0 else "",
                    "priority": parts[1] if len(parts) > 1 else "",
                    "count": len(state.get("counts", [])),
                    "cooldown_remaining": max(0, int(state.get("cooldown_until", 0) - now)),
                })
        return {
            "circuits": circuits,
            "global_count_last_min": len([t for t in self._global_counts if now - t < 60]),
            "global_overload_remaining": max(0, int(self._global_overload_until - now)),
            "tts_queue_length": len(self._tts_queue),
            "tts_lock": self._tts_lock,
            "tts_overload_remaining": max(0, int(self._tts_overload_until - now)),
            "audit_recent": self._audit_log[-20:],
        }

    def reset(self, channel: str = "") -> dict:
        """重置风控状态（channel 为空则全部重置）。"""
        if not channel or channel == "circuit":
            self._circuit_state.clear()
        if not channel or channel == "global":
            self._global_counts.clear()
            self._global_overload_until = 0
        if not channel or channel == "tts":
            self._tts_queue.clear()
            self._tts_lock = False
            self._tts_overload_until = 0
        logger.info("push_guard reset: %s", channel or "all")
        return {"reset": channel or "all", "ok": True}

    def get_audit(self, limit: int = 50, group: str = "", action: str = "") -> list[dict]:
        """查询审计日志。"""
        results = []
        for entry in reversed(self._audit_log):
            if group and entry.get("group") != group:
                continue
            if action and entry.get("action") != action:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results
