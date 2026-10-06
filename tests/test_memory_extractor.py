"""v1.1 记忆提取引擎单元测试（不部署、不碰真实 DB / 真实 LLM）。

覆盖：
1. _parse_llm_json 各种 LLM 输出形态
2. _build_prompt 组装与截断
3. _dedupe_and_save 去重 + 落库
4. MemoryExtractor.run 端到端（FakeLLM 返回固定 JSON）
5. repo 的 memory_facts CRUD
"""
import asyncio
import json
import sys
import tempfile
import time
import types

_tmp = tempfile.mkdtemp(prefix="butler_mem_test_")

from butler.config import Settings
import butler.store.db as db_mod
import butler.store.repo as repo_mod

_s = Settings(data_dir=_tmp)
db_mod.get_settings = lambda: _s
repo_mod.get_settings = lambda: _s

from butler.store import repo
from butler.memory.extractor import MemoryExtractor


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


# ---------- 1. _parse_llm_json ----------

def test_parse_plain_json():
    _wipe_tables()
    obj = MemoryExtractor._parse_llm_json('{"preferences": [], "family": [], "events": [], "habits": []}')
    assert obj == {"preferences": [], "family": [], "events": [], "habits": []}


def test_parse_markdown_fenced():
    _wipe_tables()
    text = '```json\n{"preferences": [{"content": "喜欢简洁", "confidence": 0.9}]}\n```'
    obj = MemoryExtractor._parse_llm_json(text)
    assert len(obj["preferences"]) == 1
    assert obj["preferences"][0]["content"] == "喜欢简洁"


def test_parse_with_explanation():
    _wipe_tables()
    text = '好的，分析结果如下：\n{"habits": [{"content": "早上7点看CCTV1", "confidence": 0.8}]}\n希望对你有帮助。'
    obj = MemoryExtractor._parse_llm_json(text)
    assert obj["habits"][0]["content"] == "早上7点看CCTV1"


def test_parse_garbage():
    _wipe_tables()
    assert MemoryExtractor._parse_llm_json("") == {}
    assert MemoryExtractor._parse_llm_json("没有json") == {}
    assert MemoryExtractor._parse_llm_json("{ broken") == {}


# ---------- 2. _build_prompt ----------

def test_build_prompt_includes_dialogs_and_schedules():
    _wipe_tables()
    rt = _make_rt("{}")
    ex = MemoryExtractor(rt)
    dialogs = [
        {"ts": time.time() - 3600, "user_msg": "我喜欢简洁回复", "assistant_reply": "好的", "room": "书房", "role": "butler", "source": "mobile_app"},
    ]
    prompt = ex._build_prompt(dialogs, {"大佬": {"median_first_seen": "07:20"}})
    assert "我喜欢简洁回复" in prompt
    assert "大佬" in prompt
    assert "07:20" in prompt


def test_build_prompt_truncates_long():
    _wipe_tables()
    rt = _make_rt("{}")
    ex = MemoryExtractor(rt)
    long_msg = "长文本" * 5000  # 20000 字
    dialogs = [{"ts": time.time(), "user_msg": long_msg, "assistant_reply": "", "room": "", "role": "", "source": ""}]
    prompt = ex._build_prompt(dialogs, {})
    assert len(prompt) <= 8500  # 截断后不会超长


# ---------- 3. _dedupe_and_save ----------

def test_dedupe_and_save_inserts_and_dedupes():
    _wipe_tables()
    # 清空表，避免其他测试的数据干扰计数
    from butler.store.db import get_conn
    get_conn().execute("DELETE FROM memory_facts")
    get_conn().commit()

    rt = _make_rt("{}")
    ex = MemoryExtractor(rt)
    facts = {
        "preferences": [
            {"content": "喜欢简洁回复", "confidence": 0.9},
            {"content": "不喜欢被打扰", "confidence": 0.8},
        ],
        "family": [{"content": "凯文12岁喜欢Xbox", "confidence": 0.95}],
        "events": [{"content": "9月12日去医院复诊", "date_hint": "09-12", "confidence": 0.7}],
        "habits": [{"content": "早上7:20看CCTV1", "confidence": 0.85}],
    }
    inserted, skipped = ex._dedupe_and_save(facts, days=7)
    assert inserted == 5, f"expected 5 inserted, got {inserted}"
    assert skipped == 0

    # 再跑一次，全部应被去重
    inserted2, skipped2 = ex._dedupe_and_save(facts, days=7)
    assert inserted2 == 0
    assert skipped2 == 5


def test_dedupe_contains_match():
    _wipe_tables()
    rt = _make_rt("{}")
    ex = MemoryExtractor(rt)
    # 先写入一条
    ex._dedupe_and_save(
        {"preferences": [{"content": "喜欢简洁的回复", "confidence": 0.9}]},
        days=7,
    )
    # 新内容是已有内容的子串/超串 → 应被去重
    inserted, skipped = ex._dedupe_and_save(
        {"preferences": [{"content": "喜欢简洁", "confidence": 0.9}]},
        days=7,
    )
    assert inserted == 0
    assert skipped == 1


def test_dedupe_filters_bad_content():
    _wipe_tables()
    rt = _make_rt("{}")
    ex = MemoryExtractor(rt)
    facts = {
        "preferences": [
            {"content": "", "confidence": 0.9},          # 空
            {"content": "ab", "confidence": 0.9},         # 太短
            {"content": None, "confidence": 0.5},         # None
            {"content": "合法的偏好内容", "confidence": 0.5},
        ],
    }
    inserted, skipped = ex._dedupe_and_save(facts, days=7)
    assert inserted == 1  # 只有合法那条


def test_confidence_clamped():
    _wipe_tables()
    rt = _make_rt("{}")
    ex = MemoryExtractor(rt)
    facts = {"preferences": [{"content": "测试越界置信度", "confidence": 5}]}
    ex._dedupe_and_save(facts, days=7)
    rows = repo.list_facts(status="pending")
    assert rows[-1]["confidence"] == 1.0  # 被 clamp 到 1.0


# ---------- 4. repo CRUD ----------

def test_repo_facts_crud():
    _wipe_tables()
    # 手动插一条 pending
    from butler.store.db import get_conn
    conn = get_conn()
    conn.execute(
        "INSERT INTO memory_facts (ts, fact_type, content, confidence, status, source) VALUES (?, ?, ?, ?, 'pending', 'manual')",
        (time.time(), "preference", "测试CRUD", 0.9),
    )
    conn.commit()
    # list
    facts = repo.list_facts(status="pending")
    assert len(facts) >= 1
    fid = facts[0]["id"]

    # update
    assert repo.update_fact(fid, content="编辑后的内容") is True
    got = repo.get_fact(fid)
    assert got["content"] == "编辑后的内容"

    # approve
    assert repo.set_fact_status(fid, "approved") is True
    got = repo.get_fact(fid)
    assert got["status"] == "approved"
    assert got["reviewed_ts"] is not None

    # counts
    counts = repo.count_facts_by_status()
    assert counts["approved"] >= 1

    # reject
    assert repo.set_fact_status(fid, "rejected") is True
    assert repo.delete_fact(fid) is True
    assert repo.get_fact(fid) is None


# ---------- 5. run() 端到端 ----------

def test_run_end_to_end():
    _wipe_tables()
    llm_reply = json.dumps({
        "preferences": [{"content": "喜欢晚上开空调睡觉", "confidence": 0.88}],
        "family": [],
        "events": [],
        "habits": [],
    })
    rt = _make_rt(llm_reply)
    ex = MemoryExtractor(rt)

    # 先塞一条 chat_log
    from butler.store.db import get_conn
    conn = get_conn()
    conn.execute(
        "INSERT INTO chat_logs (ts, user_msg, assistant_reply, role, source) VALUES (?, ?, ?, ?, ?)",
        (time.time(), "晚上睡觉要开空调", "好的", "butler", "mobile_app"),
    )
    conn.commit()

    result = asyncio.run(ex.run(days=7))
    assert result["extracted"] == 1, f"expected 1 fact, got {result}"
    facts = repo.list_facts(status="pending", fact_type="preference")
    contents = [f["content"] for f in facts]
    assert "喜欢晚上开空调睡觉" in contents


def test_run_no_dialogs():
    _wipe_tables()
    # 清空 chat_logs
    from butler.store.db import get_conn
    get_conn().execute("DELETE FROM chat_logs")
    get_conn().commit()

    rt = _make_rt("{}")
    ex = MemoryExtractor(rt)
    result = asyncio.run(ex.run(days=7))
    assert result["extracted"] == 0
