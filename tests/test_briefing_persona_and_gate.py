"""P0 播报修复（2026-11-06）：人格进容器、无数据不提示、闸门收紧。

病灶（生产日志实证）：
1. `persona file not found: /app/doc/briefing_persona.md` —— 容器只挂 `butler/ data/ tts/`，
   `doc/` 不在里面 ⇒ 人格退化成一句「你是这个家的管家。」；
2. 新闻源 `60s.viki.moe` 返回 HTML ⇒ `news: 0`，而 prompt 仍写「新闻里挑一条」⇒ 没数据也要挑，只能编；
3. `_briefing_is_bad` 只挡「空/短/含失败」⇒ 模型把 prompt 脚手架/占位词吐回来也照推。

每条都配「旧形状负控」：合成一份旧实现，确认它在本批判据下会**放过**、新实现会**咬住**，
免得写出一把恒绿尺。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from butler.core import briefing

REPO = Path(__file__).resolve().parents[1]
BUTLER_PERSONA = REPO / "butler" / "core" / "briefing_persona.md"
DOC_PERSONA = REPO / "doc" / "06-场景设计" / "briefing_persona.md"


class PersonaSource(unittest.TestCase):
    """人格必须随代码进容器；doc/ 那份只是开发机的第二来源。"""

    def setUp(self):
        self._cache = briefing._PERSONA_CACHE
        briefing._PERSONA_CACHE = ""
        self.addCleanup(self._restore)

    def _restore(self):
        briefing._PERSONA_CACHE = self._cache

    def test_butler_copy_exists(self):
        self.assertTrue(
            BUTLER_PERSONA.exists(),
            "butler/core/briefing_persona.md 缺失＝生产容器里没有人格（doc/ 不进容器）",
        )

    def test_two_copies_identical(self):
        """两份必须逐字节一致，否则开发机看到的和线上跑的不是同一个人格。"""
        self.assertEqual(
            BUTLER_PERSONA.read_text(encoding="utf-8"),
            DOC_PERSONA.read_text(encoding="utf-8"),
            "butler/core/ 与 doc/06-场景设计/ 的人格副本已漂移",
        )

    def test_prefers_butler_copy(self):
        text = briefing._load_persona()
        self.assertIn("lidicn", text)
        self.assertNotEqual(text, "你是这个家的管家。", "仍退化成兜底＝容器里读不到人格")

    def test_butler_copy_is_first_candidate(self):
        self.assertEqual(briefing._PERSONA_PATHS[0], BUTLER_PERSONA,
                         "加载顺序错了：doc/ 在前＝生产依旧读不到")

    def test_falls_back_to_doc_when_butler_copy_missing(self):
        import pathlib
        old = briefing._PERSONA_PATHS
        briefing._PERSONA_PATHS = (pathlib.Path("Z:/nope/briefing_persona.md"), DOC_PERSONA)
        try:
            text = briefing._load_persona()
        finally:
            briefing._PERSONA_PATHS = old
        self.assertIn("lidicn", text)

    def test_one_liner_only_when_both_missing(self):
        import pathlib
        old = briefing._PERSONA_PATHS
        briefing._PERSONA_PATHS = (pathlib.Path("Z:/nope/a.md"), pathlib.Path("Z:/nope/b.md"))
        try:
            text = briefing._load_persona()
        finally:
            briefing._PERSONA_PATHS = old
        self.assertEqual(text, "你是这个家的管家。")


class NoDataPrompt(unittest.TestCase):
    """没数据的那一格必须改写成「不许编」的硬规则，而不是留个空括号让模型自己权衡。"""

    def _prompt(self, **kw) -> str:
        base = dict(persona="人格", report={"total_events": 0}, weather="",
                    news=[], topic="", briefing_type="morning")
        base.update(kw)
        return briefing._build_prompt(**base)

    def test_empty_news_forbids_news(self):
        p = self._prompt()
        self.assertIn("不要提新闻", p)
        self.assertNotIn("新闻里挑一条", p, "新闻为空仍要求挑一条＝这轮 P0 没修")

    def test_news_present_allows_pick_and_lists_candidates(self):
        p = self._prompt(news=["某 AI 公司发布新模型", "游戏主机降价"])
        self.assertIn("新闻里挑一条", p)
        self.assertIn("某 AI 公司发布新模型", p)

    def test_empty_weather_forbids_guessing(self):
        p = self._prompt()
        self.assertIn("不要报天气", p)
        self.assertNotIn("天气：", p.split("---")[-1])

    def test_weather_present_is_included(self):
        p = self._prompt(weather="晴 25°C")
        self.assertIn("晴 25°C", p)
        self.assertNotIn("不要报天气", p)

    def test_empty_events_forbids_fabricating_activity(self):
        p = self._prompt()
        self.assertIn("不要编造谁在家", p)
        self.assertNotIn('"total_events": 0', p, "没活动仍把空报告塞给模型＝给它编的素材")

    def test_events_present_is_included(self):
        p = self._prompt(report={"total_events": 7, "top_rooms": ["客厅"]})
        self.assertIn('"total_events": 7', p)
        self.assertNotIn("不要编造谁在家", p)

    def test_empty_topic_forbids_fabricating_reminder(self):
        p = self._prompt()
        self.assertIn("不许编造提醒", p)

    def test_old_shape_prompt_still_asks_for_news(self):
        """负控：旧形状（固定一条「新闻里挑一条」＋空候选「暂无」）确实会提新闻。"""
        old = ("规则：\n3. 新闻里挑一条大佬可能感兴趣的\n---\n"
               "新闻候选：{'；'.join(news[:5]) if news else '暂无'}")
        self.assertIn("新闻里挑一条", old)
        self.assertNotIn("新闻里挑一条", self._prompt())


class Gate(unittest.TestCase):
    """闸门：脚手架回声与占位词一律判坏；正常一段话必须放行。"""

    @staticmethod
    def _old_is_bad(text: str) -> bool:
        """修前的形状（只挡空/短/含失败），用作负控。"""
        return briefing.is_degenerate_content(text) or "失败" in (text or "")

    def test_rejects_scaffold_echo(self):
        for t in ("新闻候选：某科技新闻", "规则：1. 自己判断", "天气：晴", "生活提醒：周一检查书包",
                  '家里今天的情况：{"total_events": 3}'):
            self.assertTrue(briefing._briefing_is_bad(t), f"脚手架回声没拦住：{t!r}")

    def test_rejects_placeholders(self):
        for t in ("今天的情况是 undefined", "结果是 null", "值 None", "花括号 {} 没渲染"):
            self.assertTrue(briefing._briefing_is_bad(t), f"占位词没拦住：{t!r}")

    def test_old_gate_would_pass_these(self):
        """负控：同一批文本在旧闸门里是绿的 ⇒ 这条尺子真的加了咬合。"""
        for t in ("新闻候选：某科技新闻", "今天的情况是 undefined", "占位词 null 在这里"):
            self.assertFalse(self._old_is_bad(t))
            self.assertTrue(briefing._briefing_is_bad(t))

    def test_accepts_normal_briefing(self):
        t = ("大佬早，今天晴到多云，白天 25 度左右。小凯和小艾的书包昨晚已经理好，红领巾在玄关架子上。"
             "晚上我提醒他们早点睡，周末再放开玩。")
        self.assertFalse(briefing._briefing_is_bad(t))

    def test_accepts_short_but_meaningful(self):
        self.assertFalse(briefing._briefing_is_bad("今天没有特别的安排，放心上班。"))


class GenerateBriefingNoData(unittest.IsolatedAsyncioTestCase):
    """端到端：数据全空时，喂给 LLM 的 prompt 必须带三条「不许编」的硬规则。"""

    async def asyncSetUp(self):
        self._orig = (briefing._build_report, briefing._fetch_weather, briefing._fetch_news,
                      briefing._get_topic, briefing._PERSONA_CACHE)
        briefing._PERSONA_CACHE = ""

    async def asyncTearDown(self):
        (briefing._build_report, briefing._fetch_weather, briefing._fetch_news,
         briefing._get_topic, briefing._PERSONA_CACHE) = self._orig

    async def _run(self, out="大佬早，今天没什么特别的事，放心上班。"):
        seen = {}

        class _LLM:
            async def chat(self, system, history, max_tokens=0, temperature=0):
                seen["system"] = system
                seen["temperature"] = temperature
                return out, {}

        briefing._build_report = lambda: {"total_events": 0}
        briefing._fetch_weather = self._async_ret("")
        briefing._fetch_news = self._async_ret([])
        briefing._get_topic = lambda t: ""

        class _RT:
            llm = _LLM()

        text = await briefing.generate_briefing(_RT(), "morning")
        return text, seen

    @staticmethod
    def _async_ret(value):
        async def _inner(*a, **k):
            return value
        return _inner

    async def test_prompt_carries_no_data_rules(self):
        text, seen = await self._run()
        self.assertEqual(text, "大佬早，今天没什么特别的事，放心上班。")
        sys = seen["system"]
        for rule in ("不要提新闻", "不要报天气", "不要编造谁在家", "不许编造提醒"):
            self.assertIn(rule, sys, f"prompt 缺硬规则：{rule}")
        self.assertIn("lidicn", sys, "prompt 里没有人格＝容器会退化成兜底")
        self.assertEqual(seen["temperature"], 0.6, "温度应降到 0.6 压自由发挥")

    async def test_scaffold_output_is_flag_bad(self):
        text, _ = await self._run(out="新闻候选：暂无；天气：暂无")
        self.assertTrue(briefing._briefing_is_bad(text))


if __name__ == "__main__":
    unittest.main()
