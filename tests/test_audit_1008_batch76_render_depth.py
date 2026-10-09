"""批76 验收＝D12（P2·medium，`butler/skills/engines/tool_sequence/engine.py:31` `_render`
无深度预算、无环检测，且两枚调用点在 `run()` 的 try 之外）。

报告坐标（E 面 `doc/审计报告/doubao-butler_第五轮审计报告.md:18`）三条腿，本批逐条现读复核：
  · 深度阶梯：本批尺②在生产解释器（容器 Python 3.11.16）上实测＝depth≤400 ok／500 RecursionError，
    读数在 `workorders/readings/1008_b76/b76_render_depth_probe_container_1008.txt`；
  · `json.loads` 同解释器上 depth=500 仍能加载（1000 才炸）⇒「能装进技能档、却能把 `_render` 炸掉」
    的带是存在的，报告这条成立；
  · 自引用 dict：尺②读数 `CYCLE|RecursionError`（两面同号），环检测确实缺。
现网可达性（本批尺①普查，⛔ 抄报告的『最大深度 2』）：加载面 `data/skills/{builtin,user,agent}/*.json`
共 24 枚、其中 tool_sequence 3 枚，`_render` 真吃的那两处（`steps[*].args`＋`success_text`）
可达深度 `MAX_SEQ_DEPTH_LOADED|2`；整份文档深度最大 6。⇒ 预算取 64：现测真值的 32 倍、崩溃带起点 500 的 1/8。

异常位置（现读 engine.py:60 与 :84）：两枚调用点都在 `try`（:62 起）之外，
⇒ RecursionError 穿出 `run()`，被 `core/agent.py` 的兜网 `except Exception` 当『技能匹配失败』吞掉，
整条技能降级成 ReAct 重规划（用户侧＝同样的话换条不确定的路，且白烧 token）。

腿的分工（⛔ 把『新能力腿』当『bug 先红』的证据）：
  · bug 先红（HEAD 必红，红因＝RecursionError 逃逸／异常穿出 run）：legs 1-4、7；
  · 新能力腿（HEAD 上 `_MAX_RENDER_DEPTH` 不存在⇒红因＝符号缺席）：leg 5；
  · 护栏腿（改前改后都应绿，守的是『上闸⛔ 把现网技能一起挡在门外』、『⛔ 把共享子树当环』）：legs 6、8、9。
环判定⛔ 用全局 seen 集：同一个对象作为两个兄弟字段出现（DAG）必须照常渲染，
所以沿「当前路径上的祖先对象」比身份（父件持有引用⇒地址不会被复用，⛔ 拿 id() 当键）。
运行时腿⛔ 碰现网库、⛔ import app/routes；`dispatch_tool` 是协作者、不是被测件，替身只为数调用次数。
命名纪律：全部 `unittest.TestCase` 方法（⛔ 模块级 `test_*`，discover 不 import pytest 时静默收下）。
"""
from __future__ import annotations

import asyncio
import types
import unittest

import butler.skills.engines.tool_sequence.engine as eng_mod
from butler.skills.runner_types import SkillContext

# 现测两把尺给的边界（⛔ 手抄报告号）：加载面可达真值 2／崩溃带起点 500
LIVE_REACHABLE_MAX = 2
CRASH_BAND_START = 500
# 改前改后都必须放行的一档：尺②里 depth=50 在 HEAD 上就是 ok
SAFE_DEPTH = 50


def nest(levels):
    """levels 层容器，叶子放占位符；levels=1 ⇒ 单枚 dict。"""
    v = {"k": "{{keyword}}"}
    for _ in range(levels - 1):
        v = [v]
    return v


def make_ctx(steps, success_text="", payload=None):
    skill = {"id": "b76_fixture", "brain": {"engine": "tool_sequence", "steps": steps}}
    if success_text:
        skill["brain"]["success_text"] = success_text
    return SkillContext(skill=skill, payload=payload or {"keyword": "流浪地球"},
                        rt=types.SimpleNamespace(agent=object()))


class _Calls(list):
    def __init__(self):
        super().__init__()
        self.real = None

    def install(self):
        self.real = eng_mod.dispatch_tool

        async def fake(tool, args, agent):
            self.append(tool)
            return "ok"

        eng_mod.dispatch_tool = fake

    def uninstall(self):
        eng_mod.dispatch_tool = self.real


def run_engine(ctx):
    return asyncio.run(eng_mod.ToolSequenceEngine().run(ctx))


class TestRenderDepthBudget(unittest.TestCase):
    # ---- bug 先红 ----
    def test_1_over_budget_args_raises_valueerror_not_recursion(self):
        with self.assertRaises(ValueError) as cm:
            eng_mod._render(nest(CRASH_BAND_START), {"keyword": "x"})
        self.assertNotIsInstance(cm.exception, RecursionError)

    def test_2_self_reference_raises_valueerror_not_recursion(self):
        cyc = {"a": "{{keyword}}"}
        cyc["self"] = cyc
        with self.assertRaises(ValueError) as cm:
            eng_mod._render(cyc, {"keyword": "x"})
        self.assertNotIsInstance(cm.exception, RecursionError)

    def test_3_mutual_reference_raises_valueerror(self):
        a = {}
        b = {"a": a}
        a["b"] = b
        with self.assertRaises(ValueError):
            eng_mod._render(a, {"keyword": "x"})

    def test_4_run_converts_deep_args_into_failed_result(self):
        calls = _Calls()
        calls.install()
        try:
            res = run_engine(make_ctx([{"tool": "tv_search_play", "args": nest(CRASH_BAND_START)}]))
        finally:
            calls.uninstall()
        self.assertFalse(res.ok)
        self.assertEqual("error", res.status)
        self.assertTrue(res.error)
        self.assertEqual(0, res.meta.get("failed_step"))
        # 参数没渲染出来⇒一条工具都不许发（⛔ 半截序列已经发出去再报错）
        self.assertEqual([], list(calls))

    def test_7_run_converts_cyclic_and_deep_success_text(self):
        for label, bad in (("cycle", _self_ref()), ("deep", nest(CRASH_BAND_START))):
            with self.subTest(bad=label):
                calls = _Calls()
                calls.install()
                try:
                    res = run_engine(make_ctx([{"tool": "tv_search_play",
                                                "args": {"keyword": "{{keyword}}"}}],
                                              success_text=bad))
                finally:
                    calls.uninstall()
                self.assertFalse(res.ok)
                self.assertEqual("error", res.status)
                self.assertEqual("success_text", res.meta.get("stage"))
                # success_text 是承诺的口播：炸了⛔ 算成功，但⛔ 已下发的步序要为零⇒先渲染再发令
                self.assertEqual([], list(calls))


class TestRenderBudgetCapability(unittest.TestCase):
    # ---- 新能力腿（HEAD 上符号缺席即红，⛔ 当 bug 成立的证据）----
    def test_5_budget_sits_between_live_max_and_crash_band(self):
        budget = getattr(eng_mod, "_MAX_RENDER_DEPTH", None)
        self.assertIsInstance(budget, int)
        self.assertGreater(budget, LIVE_REACHABLE_MAX)
        self.assertLess(budget, CRASH_BAND_START)
        with self.assertRaises(ValueError):
            eng_mod._render(nest(budget + 1), {"keyword": "x"})


class TestRenderGuardrails(unittest.TestCase):
    # ---- 护栏腿（改前改后都绿）----
    def test_6_within_budget_still_renders_every_leaf(self):
        out = eng_mod._render(nest(SAFE_DEPTH), {"keyword": "流浪地球"})
        node = out
        while isinstance(node, (list, dict)):
            node = node[0] if isinstance(node, list) else node["k"]
        self.assertEqual("流浪地球", node)

    def test_8_shared_subtree_is_not_a_cycle(self):
        shared = {"entity_id": "light.a"}
        args = {"left": shared, "right": shared}
        out = eng_mod._render(args, {"keyword": "x"})
        self.assertEqual({"left": {"entity_id": "light.a"}, "right": {"entity_id": "light.a"}}, out)

    def test_9_real_skill_shape_still_executes_and_announces(self):
        # 尺①现读：加载面 3 枚 tool_sequence 档的 args 就是这枚形状（可达深度 2）
        calls = _Calls()
        calls.install()
        try:
            res = run_engine(make_ctx([{"tool": "tv_search_play", "args": {"keyword": "{{keyword}}"}}],
                                      success_text="已在飞牛TV上播放《{{keyword}}》",
                                      payload={"keyword": "流浪地球"}))
        finally:
            calls.uninstall()
        self.assertTrue(res.ok)
        self.assertEqual("已在飞牛TV上播放《流浪地球》", res.text)
        self.assertEqual(["tv_search_play"], list(calls))


def _self_ref():
    cyc = {"a": "x"}
    cyc["self"] = cyc
    return cyc


if __name__ == "__main__":
    unittest.main(verbosity=2)
