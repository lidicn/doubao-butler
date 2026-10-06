"""技能沙箱测试与审批管理器（v1.5）。

工作流程：
1. Agent 生成的技能（source=agent）自动进入 pending_review 状态
2. 沙箱测试：dry_run 模式运行，检查输出是否合理
3. 测试报告：记录测试结果、耗时、输出、风险评估
4. 自动审批：简单技能（静态文本、已知引擎、无危险操作）可自动通过
5. 人工审批：复杂技能需要用户确认
6. 审批通过后技能才能启用

审批状态：
- pending_review: 待审批
- approved: 已通过（可启用）
- rejected: 已拒绝
- auto_approved: 自动通过
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from butler.logging_setup import get_logger

logger = get_logger("butler.skills.sandbox")

# 自动审批的安全引擎白名单
SAFE_ENGINES = {
    "static_text", "llm_text", "reminder_find", "decision",
}

# 沙箱测试超时（秒）
SANDBOX_TIMEOUT = 15


class SandboxManager:
    """技能沙箱测试与审批管理器。"""

    def __init__(self, runner, data_dir: str = "/app/data"):
        self.runner = runner
        self.reports_dir = Path(data_dir) / "skills" / "_sandbox"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def _report_path(self, skill_id: str) -> Path:
        return self.reports_dir / f"{skill_id}.json"

    def needs_review(self, skill: dict) -> bool:
        """判断技能是否需要人工审批。"""
        # Agent 生成的技能需要审批
        if skill.get("source") == "agent":
            return True
        # DCD 裁定④-B：能动设备的东西住在 brain.engine（output 白名单里没有那种类型），
        # 所以审批门按引擎判。改前这里查那个「危险输出」常量＝恒空＝真会动设备的技能直接免审。
        engine = (skill.get("brain") or {}).get("engine", "")
        if engine and engine not in SAFE_ENGINES:
            return True
        return False

    def assess_risk(self, skill: dict) -> dict:
        """风险评估：返回风险等级和原因。"""
        risks = []
        engine = (skill.get("brain") or {}).get("engine", "")
        trigger = (skill.get("trigger") or {}).get("entry", "")

        # 引擎风险
        if engine and engine not in SAFE_ENGINES:
            risks.append(f"引擎 '{engine}' 不在安全白名单")

        # 触发频率风险
        if trigger == "heartbeat":
            risks.append("心跳触发可能频繁执行")

        # 每日上限
        per_day = (skill.get("limits") or {}).get("per_day", 0)
        if per_day and per_day > 100:
            risks.append(f"每日上限 {per_day} 次较高")

        if not risks:
            level = "low"
        elif len(risks) <= 2:
            level = "medium"
        else:
            level = "high"

        return {"level": level, "risks": risks}

    async def test_skill(self, skill_id: str, payload: dict | None = None) -> dict:
        """沙箱测试：dry_run 模式运行技能，生成测试报告。"""
        skill = self.runner.store.get(skill_id)
        if skill is None:
            return {"ok": False, "error": "skill not found"}

        # 风险评估
        risk = self.assess_risk(skill)

        # dry_run 测试
        try:
            result = await self.runner.run(
                skill_id, source="sandbox", dry_run=True,
                payload=payload or {}, force=True,
            )
        except Exception as e:
            result = {"ok": False, "error": str(e), "status": "error"}

        # 生成报告
        report = {
            "skill_id": skill_id,
            "skill_name": skill.get("name", ""),
            "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "risk": risk,
            "result": {
                "ok": result.get("ok", False),
                "status": result.get("status", ""),
                "text": (result.get("text", "") or "")[:500],
                "error": result.get("error", ""),
                "duration_ms": (result.get("meta") or {}).get("duration_ms", 0),
            },
            "auto_approvable": self._can_auto_approve(skill, risk, result),
        }

        # 保存报告
        self._save_report(skill_id, report)

        # 如果可以自动审批，直接设置为 approved
        if report["auto_approvable"]:
            self.runner.store.set_approval(skill_id, "auto_approved")
            report["auto_approved"] = True

        return report

    def _can_auto_approve(self, skill: dict, risk: dict, result: dict) -> bool:
        """判断是否可以自动审批通过。"""
        # 必须测试成功
        if not result.get("ok"):
            return False
        # 风险必须低
        if risk["level"] != "low":
            return False
        # 引擎必须在安全白名单
        engine = (skill.get("brain") or {}).get("engine", "")
        if engine not in SAFE_ENGINES:
            return False
        # DCD 裁定④-B：这条恒空的 output 分支删掉——上面那句引擎白名单就是同一件事，
        # 而被删的那个「危险输出」常量里的两个类型，schema 白名单根本放不进来。
        return True

    def approve(self, skill_id: str) -> dict | None:
        """人工审批通过。"""
        skill = self.runner.store.set_approval(skill_id, "approved")
        if skill:
            logger.info("skill approved: %s", skill_id)
        return skill

    def reject(self, skill_id: str, reason: str = "") -> dict | None:
        """拒绝审批。"""
        skill = self.runner.store.set_approval(skill_id, "rejected")
        if skill:
            logger.info("skill rejected: %s reason=%s", skill_id, reason)
        return skill

    def get_report(self, skill_id: str) -> dict | None:
        """获取最新测试报告。"""
        path = self._report_path(skill_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_report(self, skill_id: str, report: dict) -> None:
        """保存测试报告。"""
        try:
            self._report_path(skill_id).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("sandbox report save failed: %s", e)

    def list_pending(self) -> list[dict]:
        """列出所有待审批的技能。"""
        return [
            s for s in self.runner.store.list()
            if s.get("approval") == "pending_review"
        ]
