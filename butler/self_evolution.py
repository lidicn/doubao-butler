"""自进化引擎（v2.0）。

整合 v1.1-v1.9 的所有能力，形成完整的自进化闭环：

1. 感知：收集用户行为、技能执行结果、性能数据
2. 分析：LLM 分析数据，发现改进机会
3. 生成：自动生成新技能/优化现有技能/修复冲突
4. 验证：沙箱测试 + 冲突检测 + 性能监控
5. 部署：用户确认后自动部署
6. 监控：持续监控效果，形成闭环

自进化模式：
- passive：只收集数据和建议，不自动执行
- semi：自动生成草稿，需用户确认后部署
- active：仅白名单内纯只读/纯展示类变更可自动部署，其余强制 semi
- active 白名单仅限：生成分析报告、统计展示、建议草稿（不落地）
- 禁止自动部署：技能修改/禁用、触发规则变更、设备控制、模式切换、任何写操作
- 风险等级由 Server 侧硬编码白名单判定，不由 LLM 判断（v1.8 加固）
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from collections import defaultdict, deque
from typing import Any

from butler.logging_setup import get_logger

logger = get_logger("butler.self_evolution")


class SelfEvolutionEngine:
    """自进化引擎。"""

    def __init__(self, runtime, data_dir: str = "/app/data"):
        self.rt = runtime
        self.data_dir = Path(data_dir) / "self_evolution"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 自进化模式：passive / semi / active
        self.mode = "semi"

        # 改进建议队列
        self._suggestions: deque = deque(maxlen=100)
        # 自进化历史
        self._history: deque = deque(maxlen=200)
        # 学习数据（用户行为、技能效果）
        self._learning_data: deque = deque(maxlen=1000)

        # 自动优化阈值（v1.4 调整：窗口扩大到7天，阈值更灵敏）
        self.thresholds = {
            "skill_failure_rate": 0.25,       # 技能失败率超过25%触发优化（原30%）
            "skill_avg_duration_ms": 8000,    # 技能平均耗时超过8秒触发优化（原10秒）
            "skill_usage_per_week": 0,        # 技能一周未使用建议删除
            "conflict_critical_count": 1,     # critical冲突超过1个触发修复
            "analysis_window_sec": 86400 * 7, # 分析窗口7天（原24小时）
            "min_run_count": 2,                # 最少运行次数（原3次）
        }

    def set_mode(self, mode: str) -> None:
        """设置自进化模式。"""
        if mode in ("passive", "semi", "active"):
            self.mode = mode
            logger.info("self evolution mode set to: %s", mode)

    def record_learning_data(self, data_type: str, data: dict) -> None:
        """记录学习数据。"""
        self._learning_data.append({
            "timestamp": time.time(),
            "type": data_type,
            "data": data,
        })

    def analyze(self) -> list[dict]:
        """分析系统状态，生成改进建议。"""
        # 清空旧的 pending 建议
        self._suggestions = deque([s for s in self._suggestions if s.get("status") not in ("pending",)], maxlen=100)
        suggestions = []

        # 1. 检查技能性能
        perf = getattr(self.rt, "perf_monitor", None)
        if perf:
            skill_stats = perf.get_skill_stats(time_window_sec=self.thresholds.get('analysis_window_sec', 86400*7))  # v1.4: 7天
            for sid, stats in skill_stats.items():
                if stats.get("count", 0) < self.thresholds.get('min_run_count', 2):
                    continue
                # 失败率过高
                if stats.get("success_rate", 1) < self.thresholds["skill_failure_rate"]:
                    suggestions.append({
                        "id": f"optimize_{sid}_failure",
                        "type": "skill_optimization",
                        "priority": "high",
                        "target": sid,
                        "title": f"技能 '{sid}' 失败率过高",
                        "description": f"失败率 {stats['success_rate']:.1%}，建议优化或隔离",
                        "action": "quarantine_or_optimize",
                        "auto_approvable": True,
                    })
                # 耗时过长
                if stats.get("avg_duration_ms", 0) > self.thresholds["skill_avg_duration_ms"]:
                    suggestions.append({
                        "id": f"optimize_{sid}_slow",
                        "type": "skill_optimization",
                        "priority": "medium",
                        "target": sid,
                        "title": f"技能 '{sid}' 响应过慢",
                        "description": f"平均耗时 {stats['avg_duration_ms']}ms，建议优化",
                        "action": "optimize_performance",
                        "auto_approvable": False,
                    })

        # 2. 检查冲突
        conflict = getattr(self.rt, "conflict_detector", None)
        if conflict:
            report = conflict.get_latest_report()
            if report and report.get("critical_count", 0) >= self.thresholds["conflict_critical_count"]:
                suggestions.append({
                    "id": "fix_conflicts",
                    "type": "conflict_resolution",
                    "priority": "high",
                    "target": "system",
                    "title": "检测到 critical 级冲突",
                    "description": f"存在 {report['critical_count']} 个 critical 冲突，建议修复",
                    "action": "auto_resolve_conflicts",
                    "auto_approvable": False,
                })

        # 3. 检查未使用技能
        store = getattr(getattr(self.rt, "runner", None), "store", None)
        if store and perf:
            all_skills = store.list() if store else []
            for skill in all_skills:
                sid = skill["id"]
                stats = skill_stats.get(sid, {})
                if stats.get("count", 0) == 0 and skill.get("status") == "enabled":
                    suggestions.append({
                        "id": f"unused_{sid}",
                        "type": "skill_cleanup",
                        "priority": "low",
                        "target": sid,
                        "title": f"技能 '{sid}' 长期未使用",
                        "description": "建议禁用或删除",
                        "action": "disable_skill",
                        "auto_approvable": True,
                    })

        # 4. 检查性能告警
        if perf:
            alerts = perf.get_alerts()
            for alert in alerts:
                if alert["level"] == "critical":
                    suggestions.append({
                        "id": f"perf_{alert['type']}",
                        "type": "performance_fix",
                        "priority": "high",
                        "target": alert.get("target", "system"),
                        "title": alert["message"],
                        "description": f"性能告警: {alert['message']}",
                        "action": "investigate_performance",
                        "auto_approvable": False,
                    })

        # 5. 分析学习数据：设备控制成功率
        device_stats = defaultdict(lambda: {"success": 0, "fail": 0})
        for entry in self._learning_data:
            if entry.get("type") == "device_command":
                d = entry.get("data", {})
                dev = d.get("device", "未知")
                if d.get("success"):
                    device_stats[dev]["success"] += 1
                else:
                    device_stats[dev]["fail"] += 1
        for dev, stats in device_stats.items():
            total = stats["success"] + stats["fail"]
            if total >= 3:  # 至少3次才分析
                fail_rate = stats["fail"] / total
                if fail_rate > 0.3:
                    suggestions.append({
                        "id": f"device_{dev}_fail",
                        "type": "device_health",
                        "priority": "high",
                        "target": dev,
                        "title": f"设备 '{dev}' 控制失败率高",
                        "description": f"共{total}次操作，失败{stats['fail']}次（{fail_rate:.0%}），建议检查设备连接",
                        "action": "check_device",
                        "auto_approvable": False,
                    })

        # 保存建议
        for s in suggestions:
            s["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            s["status"] = "pending"
            self._suggestions.append(s)

        logger.info("self evolution analysis: %d suggestions", len(suggestions))
        return suggestions

    async def llm_rewrite_suggestion(self, skill_id: str) -> dict:
        """M9: 用 LLM 为低质量技能生成具体的重写建议。
        输入：技能定义 + 执行统计（成功率/耗时/最近失败原因）
        输出：LLM 分析的问题诊断 + 具体的改进步骤 + 建议的新配置。
        """
        store = getattr(getattr(self.rt, "runner", None), "store", None)
        perf = getattr(self.rt, "perf_monitor", None)
        llm = getattr(self.rt, "llm", None)
        if not store or not llm:
            return {"ok": False, "reason": "skill_store or llm not available"}

        # 1. 获取技能定义
        skill = store.get(skill_id)
        if not skill:
            return {"ok": False, "reason": f"skill {skill_id} not found"}

        # 2. 获取执行统计（30天窗口）
        stats = {}
        if perf:
            all_stats = perf.get_skill_stats(time_window_sec=86400 * 30)
            stats = all_stats.get(skill_id, {})

        # 3. 获取最近失败记录
        recent_failures = []
        try:
            from butler.store import repo
            runs = await repo.get_skill_runs(skill_id, limit=5)
            for r in runs:
                if r.get("status") != "ok":
                    recent_failures.append({
                        "ts": r.get("ts"),
                        "status": r.get("status"),
                        "error": (r.get("error") or "")[:200],
                    })
        except Exception as e:
            logger.debug("get_skill_runs failed: %s", e)

        # 4. 构造 LLM prompt
        skill_json = json.dumps(skill, ensure_ascii=False, indent=2)[:3000]
        stats_json = json.dumps(stats, ensure_ascii=False, indent=2)
        failures_json = json.dumps(recent_failures, ensure_ascii=False, indent=2)

        prompt = f"""你是豆包管家的技能优化专家。请分析以下技能的问题，并给出具体的重写建议。

## 技能定义
```json
{skill_json}
```

## 执行统计（30天）
```json
{stats_json}
```

## 最近失败记录
```json
{failures_json}
```

## 请输出以下内容（用中文）：
1. **问题诊断**：这个技能的主要问题是什么？（提示词不清晰/参数错误/引擎选择不当/缺少错误处理/超时设置不合理等）
2. **具体改进步骤**：列出3-5条可操作的改进建议，每条说明改哪里、怎么改
3. **建议的新配置**：如果需要修改技能配置，给出修改后的关键字段（brain/prompt/params/timeout 等）
4. **预期效果**：改进后预期成功率/耗时会有什么变化

请直接输出分析，不要用 markdown 代码块包裹整个回复。"""

        # 5. 调用 LLM
        try:
            text, _ = await llm.chat(
                "你是技能优化专家，输出简洁、具体、可操作的建议。",
                [{"role": "user", "content": prompt}],
                max_tokens=1000,
            )
            return {
                "ok": True,
                "skill_id": skill_id,
                "skill_name": skill.get("name", skill_id),
                "stats": stats,
                "suggestion": text,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        except Exception as e:
            logger.warning("llm_rewrite_suggestion failed: %s", e)
            return {"ok": False, "reason": f"llm call failed: {e}"}

    async def execute_suggestion(self, suggestion_id: str) -> dict:
        """执行改进建议。"""
        suggestion = None
        for s in self._suggestions:
            if s["id"] == suggestion_id:
                suggestion = s
                break

        if suggestion is None:
            return {"ok": False, "error": "suggestion not found"}

        action = suggestion.get("action")
        result = {"ok": False, "action": action}

        # v1.8 安全加固：active 模式白名单检查
        # 写操作类 action（修改技能/触发规则/设备控制）即使在 active 模式下也必须人工确认
        # 只有纯只读/纯展示类 action 可以在 active 模式下自动执行
        WRITE_ACTIONS = frozenset({
            "quarantine_or_optimize",  # 禁用技能（写操作）
            "disable_skill",            # 禁用技能（写操作）
            "auto_resolve_conflicts",   # 修改触发规则（写操作）
            "modify_skill",             # 修改技能配置（写操作）
            "create_trigger",           # 创建触发规则（写操作）
            "delete_trigger",           # 删除触发规则（写操作）
            "device_action",            # 设备控制（写操作）
            "switch_mode",              # 模式切换（写操作）
        })
        if self.mode == "active" and action in WRITE_ACTIONS:
            logger.warning("self_evolution active mode: action '%s' is write operation, forcing semi (requires manual confirm)", action)
            suggestion["status"] = "pending_confirm"
            suggestion["forced_semi_reason"] = "active_mode_write_operation_whitelist"
            self._history.append({
                "suggestion_id": suggestion_id,
                "action": action,
                "status": "blocked_by_active_whitelist",
                "reason": "write operation requires manual confirm even in active mode",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            return {
                "ok": False,
                "action": action,
                "error": "active_mode_whitelist_blocked",
                "message": "该变更属于写操作，即使在 active 模式下也需要人工确认。请在 WebUI 手动确认后执行。",
                "suggestion_id": suggestion_id,
            }

        try:
            if action == "quarantine_or_optimize":
                # 自动隔离高失败率技能
                quarantine = getattr(self.rt, "quarantine_mgr", None)
                if quarantine:
                    store = getattr(getattr(self.rt, "runner", None), "store", None)
                    if store:
                        store.set_status(suggestion["target"], "disabled")
                        result["ok"] = True
                        result["message"] = f"技能 '{suggestion['target']}' 已自动禁用"

            elif action == "disable_skill":
                store = getattr(getattr(self.rt, "runner", None), "store", None)
                if store:
                    store.set_status(suggestion["target"], "disabled")
                    result["ok"] = True
                    result["message"] = f"技能 '{suggestion['target']}' 已禁用"

            elif action == "auto_resolve_conflicts":
                # 自动修复重复触发器（禁用重复的）
                conflict = getattr(self.rt, "conflict_detector", None)
                if conflict:
                    report = conflict.detect_all()
                    for c in report.get("conflicts", []):
                        if c["type"] == "duplicate_trigger":
                            # 保留第一个，禁用其他
                            triggers = c.get("triggers", [])
                            if len(triggers) > 1:
                                trigger_engine = getattr(self.rt, "trigger_engine", None)
                                if trigger_engine:
                                    tstore = getattr(trigger_engine, "store", None)
                                    if tstore and hasattr(tstore, "set_enabled"):
                                        for tid in triggers[1:]:
                                            tstore.set_enabled(tid, False)
                    result["ok"] = True
                    result["message"] = "重复冲突已自动修复"

            else:
                result["message"] = f"action '{action}' 需要人工处理"

        except Exception as e:
            result["error"] = str(e)
            logger.error("execute suggestion failed: %s", e)

        # 记录历史
        suggestion["status"] = "executed" if result["ok"] else "failed"
        suggestion["executed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        suggestion["result"] = result
        self._history.append(suggestion)

        return result

    def get_suggestions(self, status: str | None = None) -> list[dict]:
        """获取改进建议。"""
        suggestions = list(self._suggestions)
        if status:
            suggestions = [s for s in suggestions if s.get("status") == status]
        return suggestions

    def get_history(self, limit: int = 50) -> list[dict]:
        """获取自进化历史。"""
        return list(self._history)[-limit:]

    def get_dashboard(self) -> dict:
        """获取自进化仪表盘。"""
        return {
            "mode": self.mode,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pending_suggestions": len([s for s in self._suggestions if s.get("status") == "pending"]),
            "total_suggestions": len(self._suggestions),
            "executed_count": len([s for s in self._history if s.get("status") == "executed"]),
            "failed_count": len([s for s in self._history if s.get("status") == "failed"]),
            "learning_data_points": len(self._learning_data),
            "suggestions": self.get_suggestions("pending")[:10],
            "recent_history": self.get_history(10),
        }

    def save_state(self) -> None:
        """保存状态。"""
        state = {
            "mode": self.mode,
            "suggestions": list(self._suggestions),
            "history": list(self._history),
            "saved_at": time.time(),
        }
        try:
            path = self.data_dir / "state.json"
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("self evolution state save failed: %s", e)

    def load_state(self) -> None:
        """加载状态。"""
        path = self.data_dir / "state.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self.mode = state.get("mode", "semi")
            self._suggestions = deque(state.get("suggestions", []), maxlen=100)
            self._history = deque(state.get("history", []), maxlen=200)
        except Exception as e:
            logger.warning("self evolution state load failed: %s", e)
