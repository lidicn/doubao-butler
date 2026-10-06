"""审计 2026-10-06 批44 组5 验收（P0-G，DCD 裁 **A′**）：简单规则设备控制⛔ 认不出实体也发全域命令。

裁定原文 `E:\\NAS\\关键决策部\\decisions\\20261006-AF诊断型与DB简单规则与DPP三件-裁定.md` §二，四条约束逐条对腿：
  ① 认不出设备实体 ⇒ 回一句"要开哪个房间的灯？"，**⛔ 发命令**（现状：`call_service(domain, service, {})`＝给领域下命令不点名设备，HA 语义是**该领域全部设备**，说一句"开灯"全屋灯真动）；
  ② 否定词与疑问句排除**同批做**（"我是不是忘关客厅的灯了"不是命令，是被读成命令的问话）；
  ③ 捷径继续服务"现在几点"这类非设备话，摘除范围**只限设备六条**（`PreserveLeg` 两条钉这条）；
  ④ 解析**复用正路的模糊匹配器** `butler/tools/devices.py:11 _resolve_entity`，但它认不出时**原样退回 hint** ⇒
     "认没认出"必须另判（判据同 `butler/tools/registry.py:1017`：`resolved != hint`；hint 本身是合法 entity_id 形状时原样返回也算认出）。

基底读数（写这份验收**之前**在权威树现读，⛔ 事后补）：
  `butler/core/simple_rules.py` md5 `09a5114dad410cf3b0f77d9ca7681115`／130 行／CR 0；
  `butler/core/dialog.py` 调用点 `:375 simple_result = await check_simple_command(message)`（单参，⛔ 传 runtime ⇒ 实体无从解析）；
  设备规则表 `:50-57` 六条，元组形状 `(pattern, domain, service)`（⛔ 抽取件 ⇒ hint 拿不出来）。

它测不到什么（先在这里认，别等收窗再补）：
- 假件喂的 `get_states()` 是我手写的三条 HA 状态，⛔ 证明真 HA 的实体命名；`_resolve_entity` 本体⛔ 改动，本档只钉"调用它＋另判认没认出"这一层。
- 生效要那次授权 `docker restart`：`/app/butler` 是 rw bind mount，落盘≠加载。
- `source == "xiaoai"` 那条静默路（小爱原生处理，本侧⛔ 再播报）不在本枚摘除范围，本档按"⛔ 把好路改坏"钉住。
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break


def _find_root():
    """源码腿的扫描根：本机＝仓库根，容器内＝/app（/app/butler 是 rw bind mount）。"""
    for cand in (_HERE, os.path.dirname(_HERE), "/app", os.getcwd()):
        if os.path.isfile(os.path.join(cand, "butler", "core", "simple_rules.py")):
            return cand
    raise unittest.SkipTest("找不到含 butler/core/simple_rules.py 的根⇒ 源码腿无物可读")


ROOT = _find_root()
SIMPLE_SOURCE = os.path.join(ROOT, "butler", "core", "simple_rules.py")
DIALOG_SOURCE = os.path.join(ROOT, "butler", "core", "dialog.py")

ASK_MARK = "哪个房间"
SUCCESS_MARK = "好的"
FAIL_MARK = "出了点问题"


def _sr():
    try:
        import butler.core.simple_rules as sr
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest("simple_rules 导入失败（%s）" % type(exc).__name__)
    return sr


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeHa:
    """只暴露 `get_states` / `call_service` 两条腿，调用全部记账。"""

    def __init__(self, states):
        self.states = list(states)
        self.get_states_calls = 0
        self.calls = []

    async def get_states(self):
        self.get_states_calls += 1
        return self.states

    async def call_service(self, domain, service, data=None):
        self.calls.append((domain, service, dict(data or {})))
        return "ok (200)"

    @property
    def agent(self):
        return types.SimpleNamespace(ha=self)


def _state(eid, name):
    return {"entity_id": eid, "attributes": {"friendly_name": name}}


LIVING = _state("light.ke_ting", "客厅灯")
BED = _state("light.woshi", "卧室灯")
AC = _state("climate.ke_ting_ac", "客厅空调")


class DeviceEntityLeg(unittest.TestCase):
    """① 认不出就回问句、认得出才把 entity_id 放进 data。"""

    def test_bare_command_asks_instead_of_driving_the_whole_domain(self):
        sr = _sr()
        ha = FakeHa([LIVING, BED])
        out = _run(sr.check_simple_command("开灯", agent=ha.agent))
        self.assertIsNotNone(out, "「开灯」不再命中任何规则⇒ 判据要重看")
        reply, action = out
        self.assertNotEqual("call_service", action.get("action"),
                            "认不出实体仍发 call_service＝不点名设备＝该领域全部灯都动")
        self.assertIn(ASK_MARK, reply, "认不出实体没有回问句：%r" % reply)
        self.assertNotIn(SUCCESS_MARK, reply, "回问句里混了成功口径（%r）＝账实不符" % reply)
        self.assertEqual([], ha.calls, "认不出实体仍调了 HA")

    def test_recognized_entity_lands_in_data(self):
        sr = _sr()
        ha = FakeHa([LIVING, BED])
        out = _run(sr.check_simple_command("打开客厅的灯", agent=ha.agent))
        self.assertIsNotNone(out, "「打开客厅的灯」不再命中设备规则⇒ 判据要重看")
        reply, action = out
        self.assertEqual("call_service", action.get("action"), "认出了实体却不发令：%r" % (action,))
        self.assertEqual({"entity_id": "light.ke_ting"}, action.get("data"),
                         "call_service 的 data 没带解析出的 entity_id")
        self.assertNotIn(ASK_MARK, reply, "认出了实体仍在问房间：%r" % reply)

    def test_room_that_matches_nothing_sends_nothing(self):
        sr = _sr()
        ha = FakeHa([LIVING, BED])
        out = _run(sr.check_simple_command("打开卫生间的灯", agent=ha.agent))
        reply, action = out
        self.assertNotEqual("call_service", action.get("action"),
                            "卫生间没有灯仍发全域 light/turn_on")
        self.assertIn(ASK_MARK, reply, "认不出仍报成功口径：%r" % reply)
        self.assertEqual([], ha.calls)
        self.assertGreaterEqual(ha.get_states_calls, 1, "本条没真走到解析腿⇒ 读数为空")

    def test_missing_agent_is_fail_closed(self):
        sr = _sr()
        out = _run(sr.check_simple_command("开客厅的灯", agent=None))
        reply, action = out
        self.assertNotEqual("call_service", action.get("action"),
                            "没有 HA 句柄也照发 call_service＝落给上层一个无法解析的全域命令")
        self.assertIn(ASK_MARK, reply)

    def test_every_device_rule_can_extract_a_hint(self):
        sr = _sr()
        probes = ("开客厅的灯", "关卧室的灯", "开客厅的空调", "关书房的空调",
                  "打开客厅的电视", "关闭卧室的电视")
        for text in probes:
            hit = sr._check_device_control(text)
            self.assertIsNotNone(hit, "%r 不再命中设备规则⇒ 名册要重看" % text)
            action = hit[1]
            self.assertTrue(action.get("device_hint"),
                            "%r 命中了设备规则但抽不出设备线索（六条规则⛔ 各带抽取件）：%s" % (text, action))

    def test_literal_entity_id_hint_counts_as_recognized(self):
        """`_resolve_entity` 第一步是 entity_id 精确匹配并**原样返回** ⇒ 那条也得算"认出来了"，⛔ 把正路能做的事判成认不出。"""
        sr = _sr()
        ha = FakeHa([LIVING, BED])
        out = _run(sr.check_simple_command("打开 light.ke_ting 灯", agent=ha.agent))
        _reply, action = out
        self.assertEqual("call_service", action.get("action"), "字面 entity_id 被误判成认不出")
        self.assertEqual("light.ke_ting", (action.get("data") or {}).get("entity_id"))

    def test_resolution_reuses_the_live_fuzzy_matcher(self):
        """④ 复用正路匹配器：⛔ 在本模块另写一套打分（"同名两套"判例）。"""
        src = open(SIMPLE_SOURCE, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "butler.tools.devices":
                imported |= {a.name for a in node.names}
        self.assertIn("_resolve_entity", imported,
                      "simple_rules ⛔ 自造匹配器，必须 from butler.tools.devices import _resolve_entity")


class QuestionNegationLeg(unittest.TestCase):
    """② 否定词与疑问句：同批做，命中即⛔ 当命令执行（回 None＝交正路/LLM）。"""

    def _assert_not_a_command(self, text):
        sr = _sr()
        ha = FakeHa([LIVING, BED])
        out = _run(sr.check_simple_command(text, agent=ha.agent))
        if out is not None:
            _reply, action = out
            self.assertNotEqual("call_service", action.get("action"),
                                "%r 是问话/否定，却仍被当命令发出：%s" % (text, action))
        self.assertEqual([], ha.calls, "%r 触发了 HA 调用" % text)

    def test_negation_is_not_a_command(self):
        self._assert_not_a_command("不要开灯")

    def test_rhetorical_question_is_not_a_command(self):
        self._assert_not_a_command("我是不是忘关客厅的灯了")

    def test_question_particle_is_not_a_command(self):
        self._assert_not_a_command("开灯好吗？")

    def test_doubt_about_ac_is_not_a_command(self):
        self._assert_not_a_command("要不要开空调呢")


class PreserveLeg(unittest.TestCase):
    """③ 非设备话照旧走捷径（这两条改前改后都必须绿＝钉"摘除范围只限设备六条"）。"""

    def test_time_query_still_served_without_llm(self):
        sr = _sr()
        out = _run(sr.check_simple_command("现在几点"))
        self.assertIsNotNone(out, "时间查询被设备那刀的排除规则误伤⇒ 捷径范围划大了")
        reply, action = out
        self.assertEqual("none", action.get("action"))
        self.assertIn("现在", reply)

    def test_skill_create_intent_still_served(self):
        sr = _sr()
        out = _run(sr.check_simple_command("创建技能"))
        self.assertIsNotNone(out, "技能创建意图被误伤")
        self.assertEqual("skill_create_intent", out[1].get("action"))

    def test_positive_device_command_is_not_blocked_by_the_question_gate(self):
        """排除闸⛔ 一杆子打死好话：命令句里带「了」也照发。"""
        sr = _sr()
        ha = FakeHa([LIVING, BED])
        out = _run(sr.check_simple_command("开一下客厅的灯", agent=ha.agent))
        self.assertEqual("call_service", out[1].get("action"), "肯定命令被疑问排除闸吃掉：%r" % (out,))


class WiringLeg(unittest.TestCase):
    """生产路径必须真的把 runtime 递进来，否则解析腿永远拿不到 HA 句柄（盘上有码≠接上了）。"""

    def _call_nodes(self):
        src = open(DIALOG_SOURCE, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "check_simple_command"]

    def test_dialog_passes_the_runtime_to_the_simple_rule_layer(self):
        nodes = self._call_nodes()
        self.assertGreaterEqual(len(nodes), 1, "dialog 里找不到 check_simple_command 调用点")
        armed = [n for n in nodes
                 if len(n.args) >= 2
                 or any(kw.arg in ("agent", "ha", "runtime") for kw in n.keywords)]
        self.assertEqual(len(nodes), len(armed),
                         "调用点仍只传文本⇒ 设备实体永远解析不了：%s" % [n.lineno for n in nodes])

    def test_ask_device_reply_is_spoken_and_still_skips_the_llm(self):
        """追问也是"不走大模型"那条快路的产物：dialog 里 `ask_device` 必须有分支接住。"""
        src = open(DIALOG_SOURCE, encoding="utf-8", errors="replace").read()
        # assertTrue 而非 assertIn：后者会把整份 dialog.py 打进失败消息（46KB 读数件，判据行反而被淹）
        self.assertTrue("ask_device" in src,
                        "simple_rules 回的是 ask_device，dialog 里没有对应分支＝回问句可能被当命令或落进 LLM")


if __name__ == "__main__":
    unittest.main(verbosity=2)
