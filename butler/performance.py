"""性能监控器（v1.8）。

监控维度：
1. 技能执行统计（耗时、成功率、频率）
2. LLM 调用统计（token、耗时、成功率、限流）
3. 触发器触发统计（频率、耗时）
4. 资源占用（内存、CPU）
5. 性能告警（超时、失败率过高、限流频繁）
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from collections import defaultdict, deque
from threading import Lock

from butler.logging_setup import get_logger

logger = get_logger("butler.performance")


class PerformanceMonitor:
    """性能监控器。"""

    def __init__(self, data_dir: str = "/app/data"):
        self.data_dir = Path(data_dir) / "performance"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._lock = Lock()

        # 技能执行统计（滑动窗口，最近1000次）
        self.skill_runs = defaultdict(lambda: deque(maxlen=1000))
        # LLM 调用统计
        self.llm_calls = deque(maxlen=1000)
        # 触发器触发统计
        self.trigger_runs = defaultdict(lambda: deque(maxlen=500))

        # 告警阈值
        self.thresholds = {
            "skill_timeout_ms": 30000,      # 技能超时30秒
            "skill_failure_rate": 0.3,       # 失败率超过30%
            "llm_timeout_ms": 15000,         # LLM超时15秒
            "llm_rate_limit_per_hour": 20,   # 每小时限流超过20次
            "trigger_per_hour": 100,         # 触发器每小时超过100次
        }

    def record_skill_run(self, skill_id: str, duration_ms: float, success: bool,
                         error: str = "", source: str = "") -> None:
        """记录技能执行。"""
        with self._lock:
            self.skill_runs[skill_id].append({
                "timestamp": time.time(),
                "duration_ms": duration_ms,
                "success": success,
                "error": error,
                "source": source,
            })

    def record_llm_call(self, model: str, duration_ms: float, success: bool,
                         prompt_tokens: int = 0, completion_tokens: int = 0,
                         error: str = "", rate_limited: bool = False) -> None:
        """记录LLM调用。"""
        with self._lock:
            self.llm_calls.append({
                "timestamp": time.time(),
                "model": model,
                "duration_ms": duration_ms,
                "success": success,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "error": error,
                "rate_limited": rate_limited,
            })

    def record_trigger_run(self, trigger_id: str, duration_ms: float,
                            success: bool, actions_count: int = 0) -> None:
        """记录触发器执行。"""
        with self._lock:
            self.trigger_runs[trigger_id].append({
                "timestamp": time.time(),
                "duration_ms": duration_ms,
                "success": success,
                "actions_count": actions_count,
            })

    def get_skill_stats(self, skill_id: str | None = None,
                         time_window_sec: int = 3600) -> dict:
        """获取技能统计。"""
        with self._lock:
            now = time.time()
            cutoff = now - time_window_sec

            if skill_id:
                skill_ids = [skill_id]
            else:
                skill_ids = list(self.skill_runs.keys())

            result = {}
            for sid in skill_ids:
                runs = [r for r in self.skill_runs[sid] if r["timestamp"] >= cutoff]
                if not runs:
                    result[sid] = {"count": 0}
                    continue

                durations = [r["duration_ms"] for r in runs]
                successes = [r for r in runs if r["success"]]
                failures = [r for r in runs if not r["success"]]
                timeouts = [r for r in failures if r["duration_ms"] >= self.thresholds["skill_timeout_ms"]]

                result[sid] = {
                    "count": len(runs),
                    "success_count": len(successes),
                    "failure_count": len(failures),
                    "success_rate": round(len(successes) / len(runs), 3) if runs else 0,
                    "avg_duration_ms": round(sum(durations) / len(durations), 1),
                    "max_duration_ms": round(max(durations), 1),
                    "min_duration_ms": round(min(durations), 1),
                    "timeout_count": len(timeouts),
                    "recent_errors": [r["error"] for r in failures[-5:] if r["error"]],
                }
            return result

    def get_llm_stats(self, time_window_sec: int = 3600) -> dict:
        """获取LLM调用统计。"""
        with self._lock:
            now = time.time()
            cutoff = now - time_window_sec
            calls = [c for c in self.llm_calls if c["timestamp"] >= cutoff]

            if not calls:
                return {"count": 0}

            successes = [c for c in calls if c["success"]]
            failures = [c for c in calls if not c["success"]]
            rate_limited = [c for c in calls if c.get("rate_limited")]
            durations = [c["duration_ms"] for c in calls]
            prompt_tokens = sum(c.get("prompt_tokens", 0) for c in calls)
            completion_tokens = sum(c.get("completion_tokens", 0) for c in calls)

            # 按模型分组
            by_model = defaultdict(lambda: {"count": 0, "success": 0, "avg_duration": 0})
            for c in calls:
                m = c.get("model", "unknown")
                by_model[m]["count"] += 1
                if c["success"]:
                    by_model[m]["success"] += 1
                by_model[m]["avg_duration"] += c["duration_ms"]
            for m in by_model:
                by_model[m]["avg_duration"] = round(by_model[m]["avg_duration"] / by_model[m]["count"], 1)
                by_model[m]["success_rate"] = round(by_model[m]["success"] / by_model[m]["count"], 3)

            return {
                "count": len(calls),
                "success_count": len(successes),
                "failure_count": len(failures),
                "success_rate": round(len(successes) / len(calls), 3),
                "rate_limited_count": len(rate_limited),
                "avg_duration_ms": round(sum(durations) / len(durations), 1),
                "max_duration_ms": round(max(durations), 1),
                "total_prompt_tokens": prompt_tokens,
                "total_completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "by_model": dict(by_model),
                "recent_errors": [c["error"] for c in failures[-5:] if c.get("error")],
            }

    def get_trigger_stats(self, trigger_id: str | None = None,
                           time_window_sec: int = 3600) -> dict:
        """获取触发器统计。"""
        with self._lock:
            now = time.time()
            cutoff = now - time_window_sec

            if trigger_id:
                trigger_ids = [trigger_id]
            else:
                trigger_ids = list(self.trigger_runs.keys())

            result = {}
            for tid in trigger_ids:
                runs = [r for r in self.trigger_runs[tid] if r["timestamp"] >= cutoff]
                if not runs:
                    result[tid] = {"count": 0}
                    continue

                durations = [r["duration_ms"] for r in runs]
                successes = [r for r in runs if r["success"]]

                result[tid] = {
                    "count": len(runs),
                    "success_count": len(successes),
                    "success_rate": round(len(successes) / len(runs), 3),
                    "avg_duration_ms": round(sum(durations) / len(durations), 1),
                    "max_duration_ms": round(max(durations), 1),
                    "avg_actions": round(sum(r["actions_count"] for r in runs) / len(runs), 1),
                }
            return result

    def get_alerts(self) -> list[dict]:
        """获取性能告警。"""
        alerts = []
        now = time.time()

        # 技能失败率告警
        skill_stats = self.get_skill_stats(time_window_sec=3600)
        for sid, stats in skill_stats.items():
            if stats.get("count", 0) < 5:
                continue
            if stats.get("success_rate", 1) < self.thresholds["skill_failure_rate"]:
                alerts.append({
                    "level": "critical",
                    "type": "skill_high_failure_rate",
                    "target": sid,
                    "message": f"技能 '{sid}' 失败率过高: {stats['success_rate']:.1%}（阈值 {self.thresholds['skill_failure_rate']:.0%}）",
                    "value": stats["success_rate"],
                    "threshold": self.thresholds["skill_failure_rate"],
                })
            if stats.get("timeout_count", 0) >= 3:
                alerts.append({
                    "level": "warning",
                    "type": "skill_timeout",
                    "target": sid,
                    "message": f"技能 '{sid}' 近1小时超时 {stats['timeout_count']} 次",
                    "value": stats["timeout_count"],
                })

        # LLM 限流告警
        llm_stats = self.get_llm_stats(time_window_sec=3600)
        if llm_stats.get("rate_limited_count", 0) >= self.thresholds["llm_rate_limit_per_hour"]:
            alerts.append({
                "level": "warning",
                "type": "llm_rate_limited",
                "target": "llm",
                "message": f"LLM 近1小时被限流 {llm_stats['rate_limited_count']} 次（阈值 {self.thresholds['llm_rate_limit_per_hour']}）",
                "value": llm_stats["rate_limited_count"],
            })
        if llm_stats.get("success_rate", 1) < 0.8 and llm_stats.get("count", 0) >= 10:
            alerts.append({
                "level": "critical",
                "type": "llm_high_failure_rate",
                "target": "llm",
                "message": f"LLM 调用成功率过低: {llm_stats['success_rate']:.1%}",
                "value": llm_stats["success_rate"],
            })

        # 触发器频繁触发告警
        trigger_stats = self.get_trigger_stats(time_window_sec=3600)
        for tid, stats in trigger_stats.items():
            if stats.get("count", 0) >= self.thresholds["trigger_per_hour"]:
                alerts.append({
                    "level": "warning",
                    "type": "trigger_too_frequent",
                    "target": tid,
                    "message": f"触发器 '{tid}' 近1小时触发 {stats['count']} 次（阈值 {self.thresholds['trigger_per_hour']}）",
                    "value": stats["count"],
                })

        return alerts

    def get_dashboard(self) -> dict:
        """获取性能仪表盘汇总数据。"""
        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "skills": self.get_skill_stats(time_window_sec=3600),
            "llm": self.get_llm_stats(time_window_sec=3600),
            "triggers": self.get_trigger_stats(time_window_sec=3600),
            "alerts": self.get_alerts(),
            "alert_count": len(self.get_alerts()),
        }

    def save_snapshot(self) -> None:
        """保存性能快照到文件（每小时一次）。"""
        snapshot = self.get_dashboard()
        hour = time.strftime("%Y%m%d_%H")
        path = self.data_dir / f"snapshot_{hour}.json"
        try:
            path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("performance snapshot save failed: %s", e)
