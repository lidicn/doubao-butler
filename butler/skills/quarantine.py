"""技能隔离管理器：6种自动隔离条件 + 审计日志 + 恢复机制。

隔离条件：
1. 连续失败（连续3次执行失败）
2. 触发频率过高（熔断连续触发3次）
3. 执行超时（单次执行超过30秒）
4. 安全违规（技能定义包含危险操作）
5. 依赖缺失（引擎/设备不存在）
6. 资源占用（执行时间持续过高）

恢复机制：
- 手动恢复：API 调用
- 自动恢复：隔离24小时后自动降为 disabled（需手动启用）
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from collections import deque

from butler.logging_setup import get_logger

logger = get_logger("butler.skills.quarantine")

# 批24（表行 8 RUF006 那族）：loop 对被调度任务只持弱引用⇒fire-and-forget 的 task 必须存进
# 这张强引用注册表，跑完由 done_callback 自己摘掉（同批18 dialog._idle_tasks／批21 cron_task）。
_BG_TASKS: set = set()

# 隔离阈值
QUARANTINE_THRESHOLDS = {
    "consecutive_failures": 3,       # 连续失败次数
    "breaker_trips": 3,              # 熔断触发次数（1小时内）
    "timeout_ms": 30000,             # 单次执行超时（毫秒）
    "timeout_count": 2,              # 超时次数（24小时内）
    "avg_duration_ms": 15000,        # 平均执行时间阈值（毫秒）
    "avg_duration_count": 5,         # 连续高耗时次数
}

# 自动恢复时间（秒）
AUTO_RECOVER_AFTER = 24 * 3600  # 24小时

# 危险关键词（安全违规检测）
DANGEROUS_PATTERNS = [
    "rm -rf", "sudo rm", "format", "mkfs", "dd if=",
    "curl | bash", "wget | sh", "eval(", "exec(",
    "os.system", "subprocess", "shell=True",
    "DROP TABLE", "DELETE FROM", "TRUNCATE",
]


class QuarantineManager:
    """技能隔离管理器。"""

    def __init__(self, store, data_dir: str = "/app/data"):
        self.store = store
        self.audit_dir = Path(data_dir) / "skills" / "_quarantine"
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        # 运行时统计
        self._breaker_trips: dict[str, deque] = {}  # skill_id -> 熔断时间戳
        self._timeouts: dict[str, deque] = {}       # skill_id -> 超时时间戳
        self._durations: dict[str, deque] = {}      # skill_id -> 最近执行耗时
        self._quarantined_at: dict[str, float] = {} # skill_id -> 隔离时间

    def _audit_path(self, skill_id: str) -> Path:
        return self.audit_dir / f"{skill_id}.jsonl"

    def _log_audit(self, skill_id: str, action: str, reason: str, detail: dict = None) -> None:
        """记录隔离审计日志。"""
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "action": action,
            "reason": reason,
            "detail": detail or {},
        }
        try:
            with open(self._audit_path(skill_id), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("quarantine audit log failed: %s", e)

    def check_security(self, skill: dict) -> tuple[bool, str]:
        """安全违规检测：检查技能定义是否包含危险操作。"""
        skill_str = json.dumps(skill, ensure_ascii=False).lower()
        for pattern in DANGEROUS_PATTERNS:
            if pattern.lower() in skill_str:
                return False, f"安全违规：检测到危险模式 '{pattern}'"
        return True, ""

    def check_dependencies(self, skill: dict, registry) -> tuple[bool, str]:
        """依赖缺失检测：检查引擎是否存在。"""
        engine_name = (skill.get("brain") or {}).get("engine", "")
        if engine_name and registry.get(engine_name) is None:
            return False, f"依赖缺失：引擎 '{engine_name}' 不存在"
        return True, ""

    def record_breaker_trip(self, skill_id: str) -> None:
        """记录熔断触发，连续触发则隔离。"""
        now = time.time()
        if skill_id not in self._breaker_trips:
            self._breaker_trips[skill_id] = deque()
        self._breaker_trips[skill_id].append(now)
        # 只保留1小时内的
        while self._breaker_trips[skill_id] and now - self._breaker_trips[skill_id][0] > 3600:
            self._breaker_trips[skill_id].popleft()
        if len(self._breaker_trips[skill_id]) >= QUARANTINE_THRESHOLDS["breaker_trips"]:
            self.quarantine(skill_id, f"1小时内熔断触发 {len(self._breaker_trips[skill_id])} 次，触发频率过高")
            self._breaker_trips[skill_id].clear()

    def record_timeout(self, skill_id: str, duration_ms: int) -> None:
        """记录执行超时。"""
        now = time.time()
        if skill_id not in self._timeouts:
            self._timeouts[skill_id] = deque()
        self._timeouts[skill_id].append(now)
        # 只保留24小时内的
        while self._timeouts[skill_id] and now - self._timeouts[skill_id][0] > 86400:
            self._timeouts[skill_id].popleft()
        if len(self._timeouts[skill_id]) >= QUARANTINE_THRESHOLDS["timeout_count"]:
            self.quarantine(skill_id, f"24小时内执行超时 {len(self._timeouts[skill_id])} 次")
            self._timeouts[skill_id].clear()

    def record_duration(self, skill_id: str, duration_ms: int) -> None:
        """记录执行耗时，持续高耗时则隔离。"""
        if skill_id not in self._durations:
            self._durations[skill_id] = deque(maxlen=10)
        self._durations[skill_id].append(duration_ms)
        # 连续N次超过阈值
        recent = list(self._durations[skill_id])[-QUARANTINE_THRESHOLDS["avg_duration_count"]:]
        if len(recent) >= QUARANTINE_THRESHOLDS["avg_duration_count"]:
            if all(d > QUARANTINE_THRESHOLDS["avg_duration_ms"] for d in recent):
                avg = sum(recent) / len(recent)
                self.quarantine(skill_id, f"连续 {len(recent)} 次执行耗时过高（平均 {avg:.0f}ms）")
                self._durations[skill_id].clear()

    def quarantine(self, skill_id: str, reason: str) -> bool:
        """执行隔离。"""
        skill = self.store.get(skill_id)
        if skill is None:
            return False
        if skill.get("status") == "quarantined":
            return False  # 已经隔离
        # 保存隔离前快照
        snapshot = dict(skill)
        self.store.quarantine(skill_id, reason)
        self._quarantined_at[skill_id] = time.time()
        self._log_audit(skill_id, "quarantine", reason, {"snapshot": {k: snapshot[k] for k in ["id", "name", "status", "source"] if k in snapshot}})
        logger.warning("skill quarantined: %s reason=%s", skill_id, reason)
        # 通知用户
        self._notify(skill_id, reason)
        return True

    def restore(self, skill_id: str, to_status: str = "disabled") -> bool:
        """恢复技能（默认恢复为 disabled，需手动启用）。"""
        skill = self.store.get(skill_id)
        if skill is None or skill.get("status") != "quarantined":
            return False
        self.store.set_status(skill_id, to_status)
        self._quarantined_at.pop(skill_id, None)
        self._log_audit(skill_id, "restore", f"恢复为 {to_status}")
        logger.info("skill restored: %s -> %s", skill_id, to_status)
        return True

    def auto_recover_check(self) -> list[str]:
        """检查是否有隔离超过24小时的技能，自动恢复为 disabled。"""
        recovered = []
        now = time.time()
        for skill_id, quarantined_at in list(self._quarantined_at.items()):
            if now - quarantined_at >= AUTO_RECOVER_AFTER:
                if self.restore(skill_id, "disabled"):
                    recovered.append(skill_id)
        return recovered

    def _notify(self, skill_id: str, reason: str) -> None:
        """隔离通知：通过 bark 推送（fire-and-forget）。"""
        try:
            import asyncio
            from butler.runtime import get_runtime
            rt = get_runtime()
            if rt and rt.bark:
                # 两处死腿同在一行：`reason=` 不是 Bark.push 的形参（容器里 inspect.signature 现读
                # 17 个形参⛔ 这个键），而外层 get_event_loop() 在无循环线程先抛 RuntimeError，
                # 把它遮了整整一代。顺带：本函数形参 reason 之前一次都没进过通知正文。
                text = f"技能隔离：{skill_id}（{reason}）"
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    task = loop.create_task(rt.bark.push(text, title="技能隔离"))
                    _BG_TASKS.add(task)
                    task.add_done_callback(_BG_TASKS.discard)
                else:
                    # 没有跑着的循环：当场投出去。⛔ 像原写法那样在这条路上抛 RuntimeError、
                    # 再被下面 except → debug 咽掉＝隔离通知静默丢失
                    asyncio.run(rt.bark.push(text, title="技能隔离"))
        except Exception as e:
            logger.warning("QUARANTINE_NOTIFY_FAILED skill=%s err=%r", skill_id, e)

    def get_audit_log(self, skill_id: str, limit: int = 20) -> list[dict]:
        """获取隔离审计日志。"""
        path = self._audit_path(skill_id)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except Exception as e:
                # P2-9 ②类（批42 组7）：坏行无声丢＝风控永远问不出「熔断前堆了几条」
                logger.warning("quarantine audit log entry unparsable [%s]: %s", skill_id, e)
        return entries

    def get_stats(self) -> dict:
        """获取隔离统计。"""
        quarantined = [s for s in self.store.list() if s.get("status") == "quarantined"]
        return {
            "quarantined_count": len(quarantined),
            "quarantined": [{"id": s["id"], "name": s.get("name"), "reason": s.get("quarantine_reason", "")} for s in quarantined],
            "auto_recover_after_hours": AUTO_RECOVER_AFTER // 3600,
            "thresholds": QUARANTINE_THRESHOLDS,
        }
