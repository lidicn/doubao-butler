"""技能与触发器冲突检测器（v1.6）。

检测6类冲突：
1. trigger_overlap    多个触发器监听同一事件，可能同时触发
2. action_conflict    多个动作控制同一设备，可能矛盾
3. resource_contention 多个技能同时竞争同一资源（LLM/设备）
4. priority_ambiguity 相同优先级同时触发，执行顺序不确定
5. duplicate_trigger  功能重复的触发器
6. output_collision   多个技能同时输出到同一设备

冲突等级：critical / warning / info
"""
from __future__ import annotations

import time
from pathlib import Path
from collections import defaultdict

from butler.logging_setup import get_logger

logger = get_logger("butler.skills.conflict")


class ConflictDetector:
    """技能与触发器冲突检测器。"""

    def __init__(self, store, data_dir: str = "/app/data"):
        self.store = store
        self.reports_dir = Path(data_dir) / "skills" / "_conflict"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def detect_all(self) -> dict:
        """执行全部冲突检测，返回报告。"""
        skills = self.store.list()
        triggers = self._get_triggers()

        conflicts = []
        conflicts.extend(self._detect_trigger_overlap(triggers))
        conflicts.extend(self._detect_duplicate_trigger(triggers))
        conflicts.extend(self._detect_priority_ambiguity(triggers, skills))
        conflicts.extend(self._detect_action_conflict(triggers, skills))
        conflicts.extend(self._detect_output_collision(triggers, skills))
        conflicts.extend(self._detect_resource_contention(triggers, skills))

        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_skills": len(skills),
            "total_triggers": len(triggers),
            "conflict_count": len(conflicts),
            "critical_count": sum(1 for c in conflicts if c["severity"] == "critical"),
            "warning_count": sum(1 for c in conflicts if c["severity"] == "warning"),
            "info_count": sum(1 for c in conflicts if c["severity"] == "info"),
            "conflicts": conflicts,
        }

        self._save_report(report)
        return report

    def _get_triggers(self) -> list[dict]:
        """从触发器存储获取所有触发器。"""
        try:
            from butler.runtime import get_runtime
            rt = get_runtime()
            trigger_engine = getattr(rt, "trigger_engine", None)
            if trigger_engine:
                trigger_store = getattr(trigger_engine, "store", None)
                if trigger_store and hasattr(trigger_store, "list"):
                    return trigger_store.list()
            # 兼容：直接找 trigger_store
            trigger_store = getattr(rt, "trigger_store", None)
            if trigger_store and hasattr(trigger_store, "list"):
                return trigger_store.list()
        except Exception as e:
            logger.warning("trigger store not available: %s", e)
        return []

    def _detect_trigger_overlap(self, triggers: list[dict]) -> list[dict]:
        """检测多个触发器监听同一事件。"""
        conflicts = []
        by_event = defaultdict(list)
        for t in triggers:
            if not t.get("enabled", True):
                continue
            event = t.get("event", "")
            by_event[event].append(t)

        for event, group in by_event.items():
            if len(group) <= 1:
                continue
            # 检查条件是否可能同时满足
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    t1, t2 = group[i], group[j]
                    if self._conditions_overlap(t1, t2):
                        conflicts.append({
                            "type": "trigger_overlap",
                            "severity": "warning",
                            "event": event,
                            "triggers": [t1["id"], t2["id"]],
                            "message": f"触发器 '{t1['id']}' 和 '{t2['id']}' 都监听 '{event}' 事件，条件可能同时满足，导致重复触发",
                            "suggestion": "合并触发器，或增加互斥条件（如不同时间段/不同成员）",
                        })
        return conflicts

    def _conditions_overlap(self, t1: dict, t2: dict) -> bool:
        """判断两个触发器的条件是否可能同时满足。"""
        c1 = t1.get("conditions") or {}
        c2 = t2.get("conditions") or {}

        # 如果都没有时间/成员/房间限制，必然重叠
        if not c1 and not c2:
            return True

        # 时间范围重叠检测
        tr1 = c1.get("time_range", "")
        tr2 = c2.get("time_range", "")
        if tr1 and tr2:
            if not self._time_ranges_overlap(tr1, tr2):
                return False  # 时间不重叠，不会同时触发

        # 成员不同则不重叠
        m1 = c1.get("member", "")
        m2 = c2.get("member", "")
        if m1 and m2 and m1 != m2:
            return False

        # 房间不同则不重叠
        r1 = c1.get("room", "")
        r2 = c2.get("room", "")
        if r1 and r2 and r1 != r2:
            return False

        return True

    def _time_ranges_overlap(self, r1: str, r2: str) -> bool:
        """判断两个时间范围是否重叠。格式：HH:MM-HH:MM"""
        try:
            def parse(r):
                s, e = r.split("-")
                sh, sm = map(int, s.split(":"))
                eh, em = map(int, e.split(":"))
                return sh * 60 + sm, eh * 60 + em
            s1, e1 = parse(r1)
            s2, e2 = parse(r2)
            return s1 < e2 and s2 < e1
        except Exception:
            return True  # 解析失败默认重叠

    def _detect_duplicate_trigger(self, triggers: list[dict]) -> list[dict]:
        """检测功能重复的触发器（同一事件+同一动作+相同条件）。"""
        conflicts = []
        seen = defaultdict(list)
        for t in triggers:
            if not t.get("enabled", True):
                continue
            actions = tuple(sorted(a.get("skill", "") for a in (t.get("actions") or [])))
            # 条件也纳入比较，不同 time_range/member/room 不算重复
            cond = t.get("conditions", {}) or {}
            cond_key = (
                cond.get("time_range", ""),
                cond.get("member", ""),
                cond.get("room", ""),
                cond.get("button_id", ""),
            )
            key = (t.get("event", ""), actions, cond_key)
            seen[key].append(t)

        for (event, actions, cond), group in seen.items():
            if len(group) > 1:
                conflicts.append({
                    "type": "duplicate_trigger",
                    "severity": "critical",
                    "event": event,
                    "triggers": [t["id"] for t in group],
                    "actions": list(actions),
                    "conditions": cond,
                    "message": f"触发器 {[t['id'] for t in group]} 功能完全重复（同一事件 '{event}' + 同一动作 {list(actions)} + 相同条件），会导致重复执行",
                    "suggestion": "保留一个，禁用或删除其他重复触发器",
                })
        return conflicts

    def _detect_priority_ambiguity(self, triggers: list[dict], skills: list[dict]) -> list[dict]:
        """检测相同优先级的技能可能同时触发。"""
        conflicts = []
        skill_pri = {s["id"]: s.get("priority", 50) for s in skills}

        by_event = defaultdict(list)
        for t in triggers:
            if not t.get("enabled", True):
                continue
            by_event[t.get("event", "")].append(t)

        for event, group in by_event.items():
            if len(group) <= 1:
                continue
            # 收集所有可能同时触发的技能
            all_skills = set()
            for t in group:
                for a in (t.get("actions") or []):
                    all_skills.add(a.get("skill", ""))

            # 检查是否有相同优先级
            pri_groups = defaultdict(list)
            for sid in all_skills:
                pri_groups[skill_pri.get(sid, 50)].append(sid)

            for pri, sids in pri_groups.items():
                if len(sids) > 1:
                    conflicts.append({
                        "type": "priority_ambiguity",
                        "severity": "info",
                        "event": event,
                        "priority": pri,
                        "skills": sids,
                        "message": f"事件 '{event}' 可能同时触发技能 {sids}，它们优先级相同（{pri}），执行顺序不确定",
                        "suggestion": "为技能设置不同优先级，或在触发器中指定执行顺序",
                    })
        return conflicts

    def _detect_action_conflict(self, triggers: list[dict], skills: list[dict]) -> list[dict]:
        """检测多个动作控制同一设备，可能矛盾。"""
        conflicts = []
        # 提取每个技能控制的设备
        skill_devices = defaultdict(set)
        for s in skills:
            # DCD 裁定④-B：设备控制住在 brain.engine=ha_action，实体在 brain.entity。
            # 改前这里找 output 里的 ha_service.entity_id＝schema 白名单永不放行该词⇒ 这道
            # 冲突检测恒空（两个相反动作控制同一盏灯也检不出来）。
            brain = s.get("brain") or {}
            if brain.get("engine") != "ha_action":
                continue
            entity = (brain.get("entity") or "").strip()
            # 带占位符的实体（{{entity}} 之类）此刻不是具体设备，⛔ 拿模板串当设备比＝造假冲突
            if entity and "{{" not in entity:
                skill_devices[s["id"]].add(entity)

        # 检查同一触发器内的动作是否控制同一设备
        for t in triggers:
            if not t.get("enabled", True):
                continue
            devices_in_trigger = defaultdict(list)
            for a in (t.get("actions") or []):
                sid = a.get("skill", "")
                for dev in skill_devices.get(sid, set()):
                    devices_in_trigger[dev].append(sid)

            for dev, sids in devices_in_trigger.items():
                if len(sids) > 1:
                    conflicts.append({
                        "type": "action_conflict",
                        "severity": "critical",
                        "trigger": t["id"],
                        "device": dev,
                        "skills": sids,
                        "message": f"触发器 '{t['id']}' 中的技能 {sids} 都控制设备 '{dev}'，可能产生矛盾指令",
                        "suggestion": "合并为一个技能，或明确执行顺序和条件",
                    })
        return conflicts

    def _detect_output_collision(self, triggers: list[dict], skills: list[dict]) -> list[dict]:
        """检测多个技能同时输出到同一设备。"""
        conflicts = []
        skill_outputs = defaultdict(set)
        for s in skills:
            for o in (s.get("output") or []):
                skill_outputs[s["id"]].add(o.get("type", ""))

        for t in triggers:
            if not t.get("enabled", True):
                continue
            actions = t.get("actions") or []
            if len(actions) <= 1:
                continue

            output_types = defaultdict(list)
            for a in actions:
                sid = a.get("skill", "")
                for otype in skill_outputs.get(sid, set()):
                    output_types[otype].append(sid)

            for otype, sids in output_types.items():
                if len(sids) > 1:
                    conflicts.append({
                        "type": "output_collision",
                        "severity": "warning",
                        "trigger": t["id"],
                        "output_type": otype,
                        "skills": sids,
                        "message": f"触发器 '{t['id']}' 中的技能 {sids} 都输出到 '{otype}'，可能叠加/覆盖显示",
                        "suggestion": "合并输出，或设置延迟/队列，避免同时输出",
                    })
        return conflicts

    def _detect_resource_contention(self, triggers: list[dict], skills: list[dict]) -> list[dict]:
        """检测多个技能同时竞争同一资源（LLM/摄像头）。"""
        conflicts = []
        llm_engines = {"llm_text", "decision", "reminder_find"}
        skill_uses_llm = set()
        skill_uses_camera = set()

        for s in skills:
            engine = (s.get("brain") or {}).get("engine", "")
            if engine in llm_engines:
                skill_uses_llm.add(s["id"])
            # 摄像头相关技能
            if "camera" in s["id"].lower() or "food" in s["id"].lower() or "monitor" in s["id"].lower():
                skill_uses_camera.add(s["id"])

        for t in triggers:
            if not t.get("enabled", True):
                continue
            actions = t.get("actions") or []
            if len(actions) <= 1:
                continue

            llm_skills = [a.get("skill", "") for a in actions if a.get("skill", "") in skill_uses_llm]
            cam_skills = [a.get("skill", "") for a in actions if a.get("skill", "") in skill_uses_camera]

            if len(llm_skills) > 1:
                conflicts.append({
                    "type": "resource_contention",
                    "severity": "warning",
                    "trigger": t["id"],
                    "resource": "llm",
                    "skills": llm_skills,
                    "message": f"触发器 '{t['id']}' 中的技能 {llm_skills} 都需要调用 LLM，可能导致响应延迟和限流",
                    "suggestion": "串行执行，或合并为一次 LLM 调用",
                })

            if len(cam_skills) > 1:
                conflicts.append({
                    "type": "resource_contention",
                    "severity": "warning",
                    "trigger": t["id"],
                    "resource": "camera",
                    "skills": cam_skills,
                    "message": f"触发器 '{t['id']}' 中的技能 {cam_skills} 都需要访问摄像头，可能竞争资源",
                    "suggestion": "共享一次摄像头抓拍结果，避免重复抓拍",
                })

        return conflicts

    def _save_report(self, report: dict) -> None:
        """保存冲突报告。"""
        import json
        try:
            path = self.reports_dir / "latest.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("conflict report save failed: %s", e)

    def get_latest_report(self) -> dict | None:
        """获取最新冲突报告。"""
        import json
        path = self.reports_dir / "latest.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
