"""2026-11-06 四件拍板项的验收（DB26 / DB27 / DB29 / DB23 + 播报口径 B）。

出资人口径（2026-11-06「都按你建议执行」）：
- DB26 B：LLM/agent 生成的技能默认 `pending_review`；用户显式创建/内置保持 `approved`；
  用户点「确认草稿」＝人工审批通过，approval 抬成 `approved`。
- DB27：`needs_review()` 接进保存路径（agent 来源那条腿；用户显式创建按 B 决议免审）。
- DB29 A：`actions[].skill` 不存在 ⇒ 拒绝落盘（fail-closed）。
- DB23 A：`desk_system_run` 走程序白名单，白名单空＝拒绝一切。
- 播报口径 B：早间简报 7:10 → 7:30（错开起床场景）。

每条都配负控：旧形状在同一批输入上必须给出**相反**结论，否则尺子恒绿。
"""
from __future__ import annotations

import ast
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from butler.config import Settings, set_settings
from butler.skills import creator as creator_mod
from butler.skills.schema import validate_skill
from butler.tools import desk_pilot
from butler.triggers.schema import validate_trigger

REPO = pathlib.Path(__file__).resolve().parents[1]
APP_PY = REPO / "butler" / "app.py"
SKILL_ROUTES = REPO / "butler" / "api" / "skill_routes.py"

_BASE_SKILL = {
    "id": "demo_skill",
    "name": "演示技能",
    "trigger": {"entry": "webhook"},
    "brain": {"type": "static_text", "engine": "static_text", "mode": "text",
              "prompt": "", "text": "hello"},
    "output": [{"type": "bark"}],
}


def _skill(**over):
    d = json.loads(json.dumps(_BASE_SKILL))
    d.update(over)
    return d


class _RestoreSettings:
    def __init__(self, **kv):
        self.kv = kv
        self.prev = None

    def __enter__(self):
        self.prev = None
        from butler import config as config_mod
        self.prev = config_mod._settings
        set_settings(Settings(**self.kv))
        return self

    def __exit__(self, *exc):
        from butler import config as config_mod
        set_settings(self.prev)
        return False


# ── DB26：默认审批按来源分档 ──────────────────────────────────────────

class ApprovalDefaultBySource(unittest.TestCase):
    def test_user_source_stays_approved(self):
        normalized, err = validate_skill(_skill())
        self.assertEqual(err, "")
        self.assertEqual(normalized["approval"], "approved")

    def test_agent_source_defaults_to_pending_review(self):
        normalized, err = validate_skill(_skill(source="agent"))
        self.assertEqual(err, "")
        self.assertEqual(normalized["approval"], "pending_review",
                         "LLM 生成的技能拿到 approved＝审批门又被默认值架空")

    def test_builtin_source_stays_approved(self):
        normalized, err = validate_skill(_skill(source="builtin"))
        self.assertEqual(err, "")
        self.assertEqual(normalized["approval"], "approved")

    def test_explicit_approval_is_preserved(self):
        """用户/调用方自己写了 approval 的，⛔ 被默认值盖掉（人工表态优先）。"""
        normalized, err = validate_skill(_skill(source="agent", approval="auto_approved"))
        self.assertEqual(err, "")
        self.assertEqual(normalized["approval"], "auto_approved")

    def test_old_shape_ruler_negative_control(self):
        """负控：旧实现（一律 approved）在同一输入上会放过 agent 技能。"""
        old = "approved"  # 旧默认
        normalized, _ = validate_skill(_skill(source="agent"))
        self.assertNotEqual(normalized["approval"], old)


class CreatorDraftApproval(unittest.TestCase):
    class _Store:
        def __init__(self):
            self.saved = []

        def save(self, skill):
            self.saved.append(skill)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = self._Store()
        self.creator = creator_mod.SkillCreator(self.store, data_dir=self.tmp.name)

    def test_draft_source_and_approval_pinned_before_validate(self):
        """校验之前就得把 source 钉成 agent —— 否则默认审批按 user 档给 approved。"""
        out = self.creator.create_draft(_skill())
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["draft"]["source"], "agent")
        self.assertEqual(out["draft"]["approval"], "pending_review")

    def test_confirm_raises_approval_to_approved(self):
        """用户点确认＝人工审批通过；否则状态 enabled 却永远跑不起来。"""
        self.creator.create_draft(_skill())
        out = self.creator.confirm("butler")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["skill"]["approval"], "approved")
        self.assertTrue(out["skill"]["enabled"])


# ── DB27：needs_review 接线（形状腿 + 行为腿）───────────────────────────

class NeedsReviewWired(unittest.TestCase):
    def test_skills_save_calls_needs_review(self):
        src = SKILL_ROUTES.read_text(encoding="utf-8")
        tree = ast.parse(src)
        body = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "skills_save":
                body = ast.get_source_segment(src, node)
                break
        self.assertIsNotNone(body, "skills_save 不在源码里（文件被搬过？）")
        self.assertIn("needs_review", body, "审批判定又断线了（DB27 回退）")
        self.assertIn("pending_review", body)

    def test_needs_review_flags_agent_and_dangerous_engine(self):
        from butler.skills.sandbox import SandboxManager
        mgr = SandboxManager(runner=None, data_dir=tempfile.mkdtemp(prefix="nr_"))
        self.assertTrue(mgr.needs_review({"source": "agent", "brain": {"engine": "static_text"}}))
        self.assertTrue(mgr.needs_review({"source": "user", "brain": {"engine": "ha_action"}}),
                        "能动设备的引擎必须判需审（DCD ④-B）")
        self.assertFalse(mgr.needs_review({"source": "user", "brain": {"engine": "static_text"}}))


# ── DB29：触发器 actions.skill 存在性 ─────────────────────────────────

_BASE_TRIGGER = {
    "id": "trig_demo",
    "event": "heartbeat",
    "actions": [{"skill": "greet", "params": {}}],
}


class TriggerSkillExistence(unittest.TestCase):
    def test_missing_skill_is_rejected_when_checker_given(self):
        out, err = validate_trigger(_BASE_TRIGGER, skill_exists=lambda sid: False)
        self.assertIsNone(out)
        self.assertIn("技能不存在", err)

    def test_existing_skill_passes(self):
        out, err = validate_trigger(_BASE_TRIGGER, skill_exists=lambda sid: True)
        self.assertEqual(err, "")
        self.assertEqual(out["actions"][0]["skill"], "greet")

    def test_without_checker_keeps_legacy_behaviour(self):
        """负控/兼容：`triggers/store` 重载历史件时不回溯拒收。"""
        out, err = validate_trigger(_BASE_TRIGGER)
        self.assertEqual(err, "")
        self.assertIsNotNone(out)

    def test_trigger_routes_and_mcp_pass_the_checker(self):
        for rel in ("butler/api/trigger_routes.py", "butler/mcp/server.py"):
            src = (REPO / rel).read_text(encoding="utf-8")
            self.assertIn("skill_exists=skill_exists", src, f"{rel} 没把存在性校验接上（DB29 回退）")


# ── DB23：desk_system_run 程序白名单 ────────────────────────────────

class DeskProgramWhitelist(unittest.TestCase):
    def _allowed(self, path: str, programs: str):
        with _RestoreSettings(deskpilot_allowed_programs=programs):
            return desk_pilot._desk_program_allowed(path)

    def test_empty_whitelist_is_fail_closed(self):
        allowed, why = self._allowed(r"C:\Windows\System32\calc.exe", "")
        self.assertFalse(allowed)
        self.assertIn("DESKPILOT_ALLOWED_PROGRAMS", why)

    def test_listed_program_passes_case_insensitive(self):
        allowed, why = self._allowed(r"C:\Tools\PotPlayer\PotPlayerMini64.EXE",
                                     "notepad.exe,PotPlayerMini64.exe")
        self.assertTrue(allowed, why)

    def test_unlisted_program_is_refused(self):
        allowed, why = self._allowed("C:/Windows/System32/cmd.exe", "notepad.exe")
        self.assertFalse(allowed)
        self.assertIn("cmd.exe", why)

    def test_path_without_program_name_is_refused(self):
        allowed, _ = self._allowed("", "notepad.exe")
        self.assertFalse(allowed)

    def test_old_shape_ruler_negative_control(self):
        """负控：旧实现（不判白名单、原样放行）会把同一 path 判成允许。"""
        old_allowed = True
        allowed, _ = self._allowed("C:/Windows/System32/cmd.exe", "notepad.exe")
        self.assertNotEqual(allowed, old_allowed)


class DeskDispatchRefusesBeforeCallingPilot(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_returns_error_and_never_calls_system_run(self):
        called = []

        class _DPK:
            async def system_run(self, path, args=None):
                called.append(path)
                return {"ok": True}

        class _Agent:
            deskpilot = _DPK()

        with _RestoreSettings(deskpilot_allowed_programs=""):
            raw = await desk_pilot._dispatch_deskpilot(_Agent(), "desk_system_run",
                                                       {"path": "C:/Windows/System32/cmd.exe"})
        payload = json.loads(raw)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "program_not_allowed")
        self.assertEqual(called, [], "白名单拒绝前就调了 system_run＝闸装错了位置")


# ── 播报口径 B：早间简报 7:30 ──────────────────────────────────────

class BriefingSchedule(unittest.TestCase):
    def test_morning_briefing_moved_to_730(self):
        src = APP_PY.read_text(encoding="utf-8")
        self.assertIn('minute=30, id="morning_briefing"', src,
                      "早间简报还钉在 7:10＝撞起床场景（口径 B 回退）")
        self.assertIn("7:30 morning", src)


if __name__ == "__main__":
    unittest.main()
