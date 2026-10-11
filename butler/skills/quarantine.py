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
from datetime import datetime
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
# 批201 DB53：删裸词 "format"——它是英文高频词（"Format the reply as one line"），
# 而磁盘格式化由 "mkfs" 覆盖。原实现把整个 skill JSON 序列化后做子串匹配，
# prompt 里一句 "format" 就把合法技能打成安全违规并持久化隔离。
DANGEROUS_PATTERNS = [
    "rm -rf", "sudo rm", "mkfs", "dd if=",
    "curl | bash", "wget | sh", "eval(", "exec(",
    "os.system", "subprocess", "shell=True",
    "DROP TABLE", "DELETE FROM", "TRUNCATE",
]

# 批201 DB53：自然语言字段——喂给 LLM 的文本 / 给人看的描述，本身不被执行，
# 扫它们语义上就是错的（"Format the reply" 里的 "format" 不是格式化磁盘）。
# 反过来：把危险命令藏进 prompt 也不构成威胁，因为 prompt 不会被当成命令跑。
# 取「跳过 NL 字段」而非「白名单可执行字段」的口径：新字段出现时仍被扫
# （⛔ 静默漏检），残余风险方向是「未预见的 NL 字段含 eval( 等特征串→可能误杀」，
# 比「新增可执行字段被静默放过」安全——后者才是 fail-open。
_NL_SKIP_KEYS = {
    "prompt", "system", "name", "text", "say", "speak",
    "content", "message", "title", "role", "message_template",
    "description", "quarantine_reason",  # 隔离原因本身是叙述文本
}


def _scannable_strings(node, key=None):
    """递归产出「该扫的字符串值」，跳过 NL 键下的内容。

    - dict：逐键下钻，键名在 _NL_SKIP_KEYS 里的整棵子树跳过（prompt 的值可能是
      长文本，也可能嵌套，一律不进扫描）。
    - list/tuple：逐元素下钻（key 透传，list 本身不改变所属字段名）。
    - str：yield（调用方自行 .lower() 比对）。
    - 其余类型（int/bool/None…）：跳过。
    """
    if key in _NL_SKIP_KEYS:
        return
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _scannable_strings(v, k)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _scannable_strings(item, key)


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
        self._quarantined_at: dict[str, float] = {} # skill_id -> 隔离时间（单调基准）
        # 批201 DB53b：原实现 _quarantined_at 是纯内存字典，进程重启后为空 ⇒
        # 盘上 status=quarantined 的技能永不自动恢复（auto_recover_check 只遍历内存）。
        # 构造时从 store 重建——app.py 在此前已跑完 store.load()，索引在位。
        self._rebuild_quarantined_at()

    def _rebuild_quarantined_at(self) -> None:
        """批201 DB53b：从 store 把 status=quarantined 的技能隔离时刻重建回内存。

        盘上的 `quarantined_at` 是 ISO 墙钟串（`store.quarantine()` 用 `time.strftime` 写），
        而 `_quarantined_at` 现在是单调基准（批201 DB54 改的）。⛔ 直接把 ISO 串当 float 比
        ——两个基准不同，24h 比较会完全失真（刚隔离的被算成「已隔离 N 年」立即恢复，
        或反之永不恢复）。先算墙钟域的真实 elapsed，再折回单调域。

        覆盖三条写入路径：①本进程 `quarantine()`（已是单调值，重建幂等重算）；
        ②`runner._record_failure` 直调 `store.quarantine()` 绕过管理器；
        ③`skill_routes` 的 mgr-None fallback。②③只在盘上留 ISO 串，
        从 store 读＝唯一真源，不依赖管理器记没记得记账。
        """
        try:
            rows = self.store.list(status="quarantined")
        except Exception as e:
            logger.warning("quarantine rebuild list failed: %s", e)
            return
        for skill in rows:
            sid = skill.get("id")
            raw = skill.get("quarantined_at")
            if not sid or not raw:
                continue
            try:
                ts_wall = datetime.fromisoformat(str(raw)).timestamp()
            except (TypeError, ValueError):
                # 坏 ISO 串：留痕跳过，⛔ 一条坏数据拖死整批重建
                logger.warning("quarantine rebuild: bad quarantined_at for %s: %r", sid, raw)
                continue
            elapsed = max(0.0, time.time() - ts_wall)
            self._quarantined_at[sid] = time.monotonic() - elapsed

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
        """安全违规检测：检查技能定义是否包含危险操作。

        批201 DB53：原实现 `json.dumps(skill).lower()` 整体序列化后做子串匹配，
        裸词 "format"（删了）会命中 prompt 里一句 "Format the reply as one line"，
        把合法技能打成安全违规并持久化隔离。现改为递归下钻、跳过 NL 字段
        （prompt/system/name…只读不执行），只扫可能被当命令/参数执行的字符串。
        """
        for s in _scannable_strings(skill):
            s_low = s.lower()
            for pattern in DANGEROUS_PATTERNS:
                if pattern.lower() in s_low:
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
        # 批201 DB54：1h 窗口是进程内时长，必须用单调钟。用 time.time() 时 NTP 前跳
        # 会把 now - dq[0] 顶过 3600 ⇒ 旧记录被当场清掉 ⇒ 危险技能永不达阈值（fail-open）。
        now = time.monotonic()
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
        # 批201 DB54：24h 窗口同上，单调钟。
        now = time.monotonic()
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
        # 批201 DB54：单调基准——与 auto_recover_check 的 now 同钟可比。
        # 注：store 盘上写的 quarantined_at 仍是 ISO 墙钟串（给人看的，不动），
        # 重启后由 _rebuild_quarantined_at 折回单调域。
        self._quarantined_at[skill_id] = time.monotonic()
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
        # 批201 DB54：24h 是比较值，必须与 _quarantined_at 同基准（单调）。
        now = time.monotonic()
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
