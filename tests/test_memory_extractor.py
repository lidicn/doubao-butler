"""v1.1 记忆提取引擎单元测试（不部署、不碰真实 DB / 真实 LLM）。

覆盖：
1. _parse_llm_json 各种 LLM 输出形态
2. _build_prompt 组装与截断
3. _dedupe_and_save 去重 + 落库
4. MemoryExtractor.run 端到端（FakeLLM 返回固定 JSON）
5. repo 的 memory_facts CRUD

批182 收编说明（⛔ 再让这一格空转）：本文件原先是 13 个**模块级** `test_*` 函数，
不 import pytest ⇒ 本仓回归门 `python -m unittest` 一条也不收（现读：`Ran 0 tests`，
AST 名册 `scripts/audit_1002/ast_test_census_1002.py` 记 `MODULE_LEVEL_FILE|test_memory_extractor.py|13`）。
改成 `unittest.TestCase` 后第一次真跑，18 绿 1 红——红的是 `test_dedupe_filters_bad_content`
（它以为"合法那条会入库"，实际被 `conf<0.6` 那条腿拦下，而这一枚被算进 `skipped_dup`）。
低置信度要不要进待审队列是**口径问题**（文件头设计原则写"不丢弃"、M5 注释写"全部 pending"、
代码却丢），本批只把**计数撒谎**这件事修掉：重复与低置信分开数。
"""
import asyncio
import json
import shutil
import tempfile
import time
import types
import unittest
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="butler_mem_test_")

from butler.config import Settings
import butler.store.db as db_mod
import butler.store.repo as repo_mod

_s = Settings(data_dir=_tmp)
db_mod.get_settings = lambda: _s
repo_mod.get_settings = lambda: _s

from butler.store import repo
from butler.memory.extractor import MemoryExtractor


def tearDownModule():
    shutil.rmtree(_tmp, ignore_errors=True)


def _wipe_tables():
    """清空所有记忆表，保证测试隔离。"""
    from butler.store.db import get_conn
    c = get_conn()
    c.execute("DELETE FROM memory_facts")
    c.execute("DELETE FROM chat_logs")
    c.commit()


# ---------- Fake RT ----------

class FakeMemory:
    async def get_members(self):
        return [{"name": "大佬"}, {"name": "凯文"}, {"name": "爱美丽"}]

    async def member_schedule(self, name, days=14):
        return {"median_first_seen": "07:20", "median_last_seen": "23:30"}


class FakeLLM:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    async def chat(self, system, messages, **kw):
        self.calls.append({"system": system, "messages": messages, **kw})
        return self._reply, 0


def _make_rt(llm_reply):
    rt = types.SimpleNamespace()
    rt.llm = FakeLLM(llm_reply)
    rt.memory = FakeMemory()
    return rt


class ExtractorCase(unittest.TestCase):
    def setUp(self):
        _wipe_tables()


class ParseLlmJsonTests(ExtractorCase):
    """1. _parse_llm_json。"""

    def test_parse_plain_json(self):
        obj = MemoryExtractor._parse_llm_json('{"preferences": [], "family": [], "events": [], "habits": []}')
        self.assertEqual(obj, {"preferences": [], "family": [], "events": [], "habits": []})

    def test_parse_markdown_fenced(self):
        text = '```json\n{"preferences": [{"content": "喜欢简洁", "confidence": 0.9}]}\n```'
        obj = MemoryExtractor._parse_llm_json(text)
        self.assertEqual(len(obj["preferences"]), 1)
        self.assertEqual(obj["preferences"][0]["content"], "喜欢简洁")

    def test_parse_with_explanation(self):
        text = '好的，分析结果如下：\n{"habits": [{"content": "早上7点看CCTV1", "confidence": 0.8}]}\n希望对你有帮助。'
        obj = MemoryExtractor._parse_llm_json(text)
        self.assertEqual(obj["habits"][0]["content"], "早上7点看CCTV1")

    def test_parse_garbage(self):
        self.assertEqual(MemoryExtractor._parse_llm_json(""), {})
        self.assertEqual(MemoryExtractor._parse_llm_json("没有json"), {})
        self.assertEqual(MemoryExtractor._parse_llm_json("{ broken"), {})


class BuildPromptTests(ExtractorCase):
    """2. _build_prompt。"""

    def test_build_prompt_includes_dialogs_and_schedules(self):
        ex = MemoryExtractor(_make_rt("{}"))
        dialogs = [
            {"ts": time.time() - 3600, "user_msg": "我喜欢简洁回复", "assistant_reply": "好的",
             "room": "书房", "role": "butler", "source": "mobile_app"},
        ]
        prompt = ex._build_prompt(dialogs, {"大佬": {"median_first_seen": "07:20"}})
        self.assertIn("我喜欢简洁回复", prompt)
        self.assertIn("大佬", prompt)
        self.assertIn("07:20", prompt)

    def test_build_prompt_truncates_long(self):
        ex = MemoryExtractor(_make_rt("{}"))
        long_msg = "长文本" * 5000  # 15000 字
        dialogs = [{"ts": time.time(), "user_msg": long_msg, "assistant_reply": "",
                    "room": "", "role": "", "source": ""}]
        prompt = ex._build_prompt(dialogs, {})
        self.assertLessEqual(len(prompt), 8500)


class DedupeSaveTests(ExtractorCase):
    """3. _dedupe_and_save。"""

    def test_dedupe_and_save_inserts_and_dedupes(self):
        ex = MemoryExtractor(_make_rt("{}"))
        facts = {
            "preferences": [
                {"content": "喜欢简洁回复", "confidence": 0.9},
                {"content": "不喜欢被打扰", "confidence": 0.8},
            ],
            "family": [{"content": "凯文12岁喜欢Xbox", "confidence": 0.95}],
            "events": [{"content": "9月12日去医院复诊", "date_hint": "09-12", "confidence": 0.7}],
            "habits": [{"content": "早上7:20看CCTV1", "confidence": 0.85}],
        }
        inserted, dup, low = ex._dedupe_and_save(facts, days=7)
        self.assertEqual(inserted, 5, f"expected 5 inserted, got {inserted}")
        self.assertEqual(dup, 0)
        self.assertEqual(low, 0)

        # 再跑一次，全部应被去重
        inserted2, dup2, low2 = ex._dedupe_and_save(facts, days=7)
        self.assertEqual(inserted2, 0)
        self.assertEqual(dup2, 5)
        self.assertEqual(low2, 0)

    def test_dedupe_contains_match(self):
        ex = MemoryExtractor(_make_rt("{}"))
        ex._dedupe_and_save({"preferences": [{"content": "喜欢简洁的回复", "confidence": 0.9}]}, days=7)
        # 新内容是已有内容的子串/超串 → 应被去重
        inserted, dup, low = ex._dedupe_and_save(
            {"preferences": [{"content": "喜欢简洁", "confidence": 0.9}]}, days=7)
        self.assertEqual(inserted, 0)
        self.assertEqual(dup, 1)
        self.assertEqual(low, 0)

    def test_dedupe_filters_bad_content(self):
        """空/太短/None 三条被形状筛掉；合法那条因 conf=0.5 走「低置信」那一腿，
        ⛔ 再被算成"重复"——原写法在这里返回 (0, 1)，读起来像"数据库里已有一条同款"，
        而真相是"置信度不够"。分腿之后两条腿各自可数。"""
        ex = MemoryExtractor(_make_rt("{}"))
        facts = {
            "preferences": [
                {"content": "", "confidence": 0.9},            # 空
                {"content": "ab", "confidence": 0.9},           # 太短
                {"content": None, "confidence": 0.5},           # None
                {"content": "合法的偏好内容", "confidence": 0.5},  # 合法但低于 0.6
            ],
        }
        inserted, dup, low = ex._dedupe_and_save(facts, days=7)
        self.assertEqual(inserted, 0)
        self.assertEqual(dup, 0, "低置信度那条被算进了重复数")
        self.assertEqual(low, 1)

    def test_low_confidence_floor_only(self):
        """同一枚内容把置信度抬到 0.6 ⇒ 应当入库（钉「0 那条是门槛造成的，⛔ 形状筛掉的」）。"""
        ex = MemoryExtractor(_make_rt("{}"))
        inserted, dup, low = ex._dedupe_and_save(
            {"preferences": [{"content": "合法的偏好内容", "confidence": 0.6}]}, days=7)
        self.assertEqual(inserted, 1)
        self.assertEqual((dup, low), (0, 0))

    def test_confidence_clamped(self):
        ex = MemoryExtractor(_make_rt("{}"))
        facts = {"preferences": [{"content": "测试越界置信度", "confidence": 5}]}
        ex._dedupe_and_save(facts, days=7)
        rows = repo.list_facts(status="pending")
        self.assertEqual(rows[-1]["confidence"], 1.0)  # 被 clamp 到 1.0


class RepoCrudTests(ExtractorCase):
    """4. repo 的 memory_facts CRUD。"""

    def test_repo_facts_crud(self):
        from butler.store.db import get_conn
        conn = get_conn()
        conn.execute(
            "INSERT INTO memory_facts (ts, fact_type, content, confidence, status, source) VALUES (?, ?, ?, ?, 'pending', 'manual')",
            (time.time(), "preference", "测试CRUD", 0.9),
        )
        conn.commit()
        facts = repo.list_facts(status="pending")
        self.assertGreaterEqual(len(facts), 1)
        fid = facts[0]["id"]

        self.assertTrue(repo.update_fact(fid, content="编辑后的内容"))
        self.assertEqual(repo.get_fact(fid)["content"], "编辑后的内容")

        self.assertTrue(repo.set_fact_status(fid, "approved"))
        got = repo.get_fact(fid)
        self.assertEqual(got["status"], "approved")
        self.assertIsNotNone(got["reviewed_ts"])

        counts = repo.count_facts_by_status()
        self.assertGreaterEqual(counts["approved"], 1)

        self.assertTrue(repo.set_fact_status(fid, "rejected"))
        self.assertTrue(repo.delete_fact(fid))
        self.assertIsNone(repo.get_fact(fid))


class RunEndToEndTests(ExtractorCase):
    """5. run() 端到端（FakeLLM 返回固定 JSON）。"""

    def test_run_end_to_end(self):
        llm_reply = json.dumps({
            "preferences": [{"content": "喜欢晚上开空调睡觉", "confidence": 0.88}],
            "family": [], "events": [], "habits": [],
        })
        ex = MemoryExtractor(_make_rt(llm_reply))
        from butler.store.db import get_conn
        conn = get_conn()
        conn.execute(
            "INSERT INTO chat_logs (ts, user_msg, assistant_reply, role, source) VALUES (?, ?, ?, ?, ?)",
            (time.time(), "晚上睡觉要开空调", "好的", "butler", "mobile_app"),
        )
        conn.commit()

        result = asyncio.run(ex.run(days=7))
        self.assertEqual(result["extracted"], 1, f"expected 1 fact, got {result}")
        facts = repo.list_facts(status="pending", fact_type="preference")
        self.assertIn("喜欢晚上开空调睡觉", [f["content"] for f in facts])

    def test_run_no_dialogs(self):
        ex = MemoryExtractor(_make_rt("{}"))
        result = asyncio.run(ex.run(days=7))
        self.assertEqual(result["extracted"], 0)

    def test_run_reports_dup_and_low_conf_separately(self):
        """计数真值必须一路活到 WebUI 吃的那个 dict（谎言就住在这一层）。

        一枚重复（已入库）＋一枚低置信（从未入库）混在一批里：
        `skipped_dup` 只准报 1，低置信那枚走 `ignored_low_conf`。
        """
        from butler.store.db import get_conn
        conn = get_conn()
        conn.execute(
            "INSERT INTO chat_logs (ts, user_msg, assistant_reply, role, source) VALUES (?, ?, ?, ?, ?)",
            (time.time(), "喜欢喝茶", "好的", "butler", "mobile_app"),
        )
        conn.commit()
        # 先入库一条，制造"重复"那一腿
        ex = MemoryExtractor(_make_rt("{}"))
        ex._dedupe_and_save({"preferences": [{"content": "喜欢喝茶", "confidence": 0.9}]}, days=7)

        llm_reply = json.dumps({
            "preferences": [
                {"content": "喜欢喝茶", "confidence": 0.9},      # 重复
                {"content": "周末想去爬山", "confidence": 0.4},  # 低置信，未入库
            ],
            "family": [], "events": [], "habits": [],
        })
        ex2 = MemoryExtractor(_make_rt(llm_reply))
        result = asyncio.run(ex2.run(days=7))
        self.assertEqual(result["extracted"], 0)
        self.assertEqual(result["skipped_dup"], 1, "低置信那枚又被算进重复数了")
        self.assertEqual(result["ignored_low_conf"], 1)


if __name__ == "__main__":
    unittest.main()
