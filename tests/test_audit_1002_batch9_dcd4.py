"""批9 验收＝DCD 裁定④（输出白名单）里**现网零受害者**的那几条腿。

裁定文书＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md` 议题④（裁定 B）：
  1. `_OUTPUTS` 保持三种（能力面不变）；
  2. 未知 output 类型＝校验报错，不静默剥；
  3. 删 `templates.py` 那条会造哑技能的模板项；
  4. `conflict.py` / `sandbox.py` 那两处按 `brain.engine` 判定或直接删。

⛔ 本文件不覆盖裁定 B 的第 2 条里「output 整体形状不合法 ⇒ 剥光后回落 tv_notify」那一半：
现读（`scripts/audit_1002/census_output_types_1002.py` ＋ 一次性半径普查）读出
`data/skills/{builtin,user,agent}` 24 份在册里**恰好 2 份**是旧形状（`output` 是 dict 而非 list、
字段是 `triggers` 复数），且两份都 `status=enabled` ⇒ 把那条回落改成报错＝这两只在役技能
下次重启直接不进索引。那是要人裁的后果，⛔ 我单方面落。见台账裁④那行。

命名纪律：全部写成 `unittest.TestCase` 方法。⛔ 模块级 `test_*` 函数——本宿主 discover
不 import pytest 时会把它们静默收下、一条不跑（台账 §9-3 那三个数就是这么对账出来的）。
"""
from __future__ import annotations

import pathlib
import tempfile
import unittest


WHITELIST = {"tv_notify", "xiaomi_speak", "bark"}


def _skill_raw(output):
    return {
        "id": "t_batch9_leg",
        "name": "批9 夹具",
        "trigger": {"entry": "webhook"},
        "brain": {"type": "static", "engine": "static_text"},
        "output": output,
    }


class SchemaWhitelistTest(unittest.TestCase):
    """裁定④-1／-2：白名单不扩，名单内的照常归一，名单外的当场报错。"""

    def test_output_whitelist_is_still_exactly_three(self):
        from butler.skills.schema import _OUTPUTS
        self.assertEqual(set(_OUTPUTS), WHITELIST,
                         "裁定 B 第 1 条＝能力面不变（⛔ 把 ha_service 补进白名单＝新能力上线，那是被驳回的 A）")

    def test_whitelisted_outputs_still_normalize(self):
        from butler.skills.schema import validate_skill
        normalized, err = validate_skill(_skill_raw([
            {"type": "tv_notify", "tts": False, "room": "客厅"},
            {"type": "xiaomi_speak", "role": "小爱"},
            {"type": "bark"},
        ]))
        self.assertEqual(err, "", f"三条合法 output 不该被拒：err={err!r}")
        self.assertIsNotNone(normalized)
        types = [o["type"] for o in normalized["output"]]
        self.assertEqual(types, ["tv_notify", "xiaomi_speak", "bark"])
        self.assertIs(normalized["output"][0]["tts"], False, "tts=False 必须原样保留（⛔ 沉默技能被改成开口）")
        self.assertEqual(normalized["output"][0].get("room"), "客厅")
        self.assertEqual(normalized["output"][1].get("role"), "小爱")

    def test_unknown_output_type_is_an_error_not_silently_stripped(self):
        from butler.skills.schema import validate_skill
        normalized, err = validate_skill(_skill_raw([
            {"type": "ha_service", "domain": "homeassistant", "service": "turn_on", "entity_id": "light.desk"}
        ]))
        self.assertIsNone(normalized,
                          "裁定 B 第 2 条核心＝「沉默地丢掉」变「当场拒绝」：改前这里返回一条被静默剥成 "
                          "tv_notify(tts=True) 的合法技能，面板以为建好了『开灯』，实际只会说不会做")
        self.assertIn("ha_service", err, f"报错必须点名是哪个类型：err={err!r}")

    def test_unknown_type_beside_valid_one_is_still_an_error(self):
        from butler.skills.schema import validate_skill
        normalized, err = validate_skill(_skill_raw([
            {"type": "tv_notify"},
            {"type": "ha_service", "service": "turn_on"},
        ]))
        self.assertIsNone(normalized,
                          "部分合法也不行：只剥掉坏的那条＝技能照常运行但动作蒸发，仍是假绿通道")
        self.assertIn("ha_service", err)


class TemplateGeneratorTest(unittest.TestCase):
    """裁定④-3：生成端⛔ 再产出进不了白名单的 output。"""

    def test_no_builtin_template_emits_unadmissible_output(self):
        from butler.skills.templates import BUILTIN_TEMPLATES
        offenders = []
        for tpl in BUILTIN_TEMPLATES:
            for o in tpl.get("skill_template", {}).get("output") or []:
                otype = o.get("type") if isinstance(o, dict) else f"<非 dict:{type(o).__name__}>"
                if otype not in WHITELIST:
                    offenders.append(f"{tpl.get('id')}#{otype}")
        self.assertEqual(offenders, [],
                         "模板「设备控制」的 output 第一项是 ha_service ⇒ 从面板建出来的『开灯』技能"
                         "得到一个只说『灯已打开』而不动灯的哑技能（裁定 B 第 3 条：删该项）")


class SandboxGateTest(unittest.TestCase):
    """裁定④-4：危险输出审批门——设备控制走 brain.engine，门就得按 engine 判。"""

    def _mgr(self):
        from butler.skills.sandbox import SandboxManager
        with tempfile.TemporaryDirectory() as td:
            return SandboxManager(runner=None, data_dir=td)

    def test_needs_review_bites_for_ha_action_engine(self):
        mgr = self._mgr()
        skill = {
            "id": "t_ha", "source": "user",
            "brain": {"engine": "ha_action", "domain": "light", "service": "turn_on", "entity": "light.desk"},
            "output": [{"type": "tv_notify", "tts": False}],
        }
        self.assertTrue(mgr.needs_review(skill),
                        "改前 needs_review 只看 output 类型（白名单恒不含 ha_service）与 source==agent ⇒ "
                        "真正会动设备的技能直接免审（恒空的门）")

    def test_needs_review_stays_quiet_for_safe_engine(self):
        mgr = self._mgr()
        skill = {"id": "t_safe", "source": "user",
                 "brain": {"engine": "static_text"},
                 "output": [{"type": "tv_notify"}]}
        self.assertFalse(mgr.needs_review(skill), "安全引擎且用户来源的技能不该被这道门拦住（⛔ 把门改成见谁咬谁）")

    def test_assess_risk_flags_device_engine(self):
        mgr = self._mgr()
        risk = mgr.assess_risk({"id": "t_ha", "source": "user",
                                "brain": {"engine": "ha_action"},
                                "output": [{"type": "tv_notify"}],
                                "limits": {"per_day": 5}})
        self.assertNotEqual(risk["level"], "low", f"设备引擎必须进风险账：{risk}")

    def test_dead_risky_outputs_constant_is_gone(self):
        import butler.skills.sandbox as sb
        self.assertFalse(hasattr(sb, "RISKY_OUTPUTS"),
                         "改完按 engine 判定后，`RISKY_OUTPUTS={ha_service, mqtt_publish}` 就是第二个"
                         "「定义在盘上、链路上没人调」的影子常量（本批六件的共同病因）——留它就是留病")


class ConflictGateTest(unittest.TestCase):
    """裁定④-4（conflict 那半）：动作冲突检测按 brain.engine 取实体。"""

    def test_action_conflict_bites_for_same_entity_via_brain_engine(self):
        from butler.skills.conflict import ConflictDetector
        with tempfile.TemporaryDirectory() as td:
            det = ConflictDetector(store=None, data_dir=td)
        skills = [
            {"id": "a_on", "brain": {"engine": "ha_action", "entity": "light.living_room"}},
            {"id": "a_off", "brain": {"engine": "ha_action", "entity": "light.living_room"}},
        ]
        triggers = [{"id": "tr_batch9_a", "enabled": True, "actions": [{"skill": "a_on"}, {"skill": "a_off"}]}]
        found = det._detect_action_conflict(triggers, skills)
        self.assertTrue(any(c.get("type") == "action_conflict" for c in found),
                        f"改前该函数只从 output 里找 ha_service.entity_id ⇒ 恒空，两个相反动作控制同一盏灯"
                        f"检不出冲突（返回={found}）")

    def test_action_conflict_stays_empty_without_shared_device(self):
        from butler.skills.conflict import ConflictDetector
        with tempfile.TemporaryDirectory() as td:
            det = ConflictDetector(store=None, data_dir=td)
        skills = [
            {"id": "a_on", "brain": {"engine": "ha_action", "entity": "light.living_room"}},
            {"id": "b_on", "brain": {"engine": "ha_action", "entity": "light.study"}},
        ]
        triggers = [{"id": "tr_batch9_b", "enabled": True, "actions": [{"skill": "a_on"}, {"skill": "b_on"}]}]
        self.assertEqual(det._detect_action_conflict(triggers, skills), [],
                         "不同设备不构成冲突（⛔ 为了咬而咬）")

    def test_action_conflict_ignores_non_device_engines(self):
        from butler.skills.conflict import ConflictDetector
        with tempfile.TemporaryDirectory() as td:
            det = ConflictDetector(store=None, data_dir=td)
        skills = [
            {"id": "s1", "brain": {"engine": "llm_text", "entity": "light.same"}},
            {"id": "s2", "brain": {"engine": "static_text", "entity": "light.same"}},
        ]
        triggers = [{"id": "tr_batch9_b", "enabled": True, "actions": [{"skill": "s1"}, {"skill": "s2"}]}]
        self.assertEqual(det._detect_action_conflict(triggers, skills), [],
                         "只有会动设备的 engine 才进这道检测（⛔ 把文本引擎也算成设备冲突）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
