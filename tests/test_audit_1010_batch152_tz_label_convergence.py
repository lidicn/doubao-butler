"""批152 验收（先红）：PR-B 时区收敛收尾——家庭时区标识单点来自 `homesdk.time.house_tz_name()`。

旧机制：`Asia/Shanghai` 字面量在管家侧还剩 6 处落点
  - `app.py` 调度器 `AsyncIOScheduler(timezone="Asia/Shanghai")`（时间轴，最关键）
  - `app.py` 简报排程日志标签
  - `core/agent.py` 系统提示词标签 + 注释
  - `skills/engines/llm_decide/engine.py` 系统提示词标签
  - `skills/engines/llm_decide/mock.py` 系统提示词标签（并留着只为它而 import 的 `zoneinfo`）
后果：`HOMESDK_TZ` 一改，调度器与提示词仍按北京报时 ⇒ 与 `house_now()` 取到的钟面错 8 小时级别。

本批口径：字面量归零，时区标识一律现取 `house_tz_name()`（`TZ`/`AF_TZ`/`TZ_OFFSET_HOURS`
三级解析 + `HOMESDK_` 前缀优先，默认仍是 `Asia/Shanghai`，所以默认行为不变）。

判据分两腿：
  - 行为腿：改 `HOMESDK_TZ=Asia/Tokyo` 后，两个引擎真的把 `（Asia/Tokyo）` 写进 system 提示词；
  - 形状腿：`butler/**/*.py` 源码内 `Asia/Shanghai` 0 命中（形状尺带正/负校准，见
    `test_shape_ruler_bites_synthetic_text`／`test_shape_ruler_bites_known_file`）。
  `core/agent.py` 那一腿只有形状证据（`Agent.run` 全程要 DB/记忆/ReAct，行为腿代价不划算）。
"""
import os
import tempfile
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="butler_test_batch152_")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("TTS_DIR", _TMP)

from butler.core import agent as agent_mod
from butler.skills.engines.llm_decide import engine as decide_engine_mod
from butler.skills.engines.llm_decide import mock as decide_mock_mod
from butler.skills.runner_types import SkillContext

BUTLER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(agent_mod.__file__)))
LITERAL = "Asia/Shanghai"


def py_sources(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def literal_lines(text: str) -> list[int]:
    return [i for i, line in enumerate(text.splitlines(), 1) if LITERAL in line]


class ShapeRuler(unittest.TestCase):
    def test_shape_ruler_bites_synthetic_text(self):
        # 负控：尺子必须咬得住含字面量的源码，否则下面的 0 命中毫无意义
        self.assertEqual(literal_lines(f'x = "{LITERAL}"\ny = 1'), [1])

    def test_shape_ruler_bites_known_file(self):
        # 正控：真实文件里现读 ≥1 命中，证明扫描走的是盘上内容不是空转。
        # 取材 homesdk/time.py 的 DEFAULT_TZ_NAME——它在 butler/ 之外，合法保留字面量。
        from homesdk import time as homesdk_time
        with open(homesdk_time.__file__, encoding="utf-8") as f:
            self.assertGreaterEqual(len(literal_lines(f.read())), 1)

    def test_no_literal_tz_in_butler_sources(self):
        hits = []
        for path in py_sources(BUTLER_ROOT):
            with open(path, encoding="utf-8") as f:
                lines = literal_lines(f.read())
            if lines:
                hits.append(f"{os.path.relpath(path, BUTLER_ROOT)}:{lines}")
        self.assertEqual(hits, [], f"butler/ 仍有 {LITERAL} 字面量：{hits}")

    def test_scheduler_tz_comes_from_homesdk(self):
        with open(os.path.join(BUTLER_ROOT, "app.py"), encoding="utf-8") as f:
            src = f.read()
        needles = ("AsyncIOScheduler(timezone=house_tz_name())",
                   "from homesdk.time import house_tz_name")
        self.assertEqual([n for n in needles if n not in src], [])

    def test_mock_engine_drops_zoneinfo_import(self):
        # zoneinfo 在 mock.py 里只为这处字面量存在，收敛后即死码
        with open(decide_mock_mod.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertEqual([i for i, line in enumerate(src.splitlines(), 1)
                          if "from zoneinfo import ZoneInfo" in line], [])


class FakeLLM:
    def __init__(self, reply="主人早，今天有什么安排？"):
        self.reply = reply
        self.systems = []

    async def chat(self, system, messages, **kwargs):
        self.systems.append(system)
        return self.reply, {}

    async def chat_with_tools(self, system, messages, tools, executor, **kwargs):
        self.systems.append(system)
        return self.reply


class FakeRuntime:
    """引擎要求 rt.llm 与 rt.agent 都非空；白名单里没有预查工具，agent 不会被真正调用。"""

    def __init__(self, llm):
        self.llm = llm
        self.agent = object()


class BehaviorRuler(unittest.IsolatedAsyncioTestCase):
    UNSET_KEYS = ("HOMESDK_TZ", "TZ", "HOMESDK_AF_TZ", "AF_TZ",
                  "HOMESDK_TZ_OFFSET_HOURS", "TZ_OFFSET_HOURS")

    async def _run_engine_with_tz(self, tz_name: str | None) -> str:
        skill = {
            "brain": {
                "tools_whitelist": ["tv_screenshot"],
                "system": "你是家庭管家。",
                "prompt": "主人刚出现在客厅。",
                "ask_wait_seconds": 0,
            }
        }
        llm = FakeLLM()
        ctx = SkillContext(skill=skill, rt=FakeRuntime(llm), payload={"room": "客厅"})
        if tz_name is None:
            kept = {k: v for k, v in os.environ.items() if k not in self.UNSET_KEYS}
            patcher = mock.patch.dict(os.environ, kept, clear=True)
        else:
            patcher = mock.patch.dict(os.environ, {"HOMESDK_TZ": tz_name})
        with patcher:
            await decide_engine_mod.LLMDecideEngine().run(ctx)
        self.assertEqual(len(llm.systems), 1)
        return llm.systems[0]

    async def test_engine_prompt_label_follows_declared_tz(self):
        system = await self._run_engine_with_tz("Asia/Tokyo")
        self.assertIn(f"（Asia/Tokyo）", system)
        self.assertNotIn(LITERAL, system)

    async def test_mock_engine_prompt_label_follows_declared_tz(self):
        skill = {
            "brain": {
                "tools_whitelist": ["tv_screenshot"],
                "system": "你是家庭管家。",
                "prompt": "测试场景。",
            }
        }
        llm = FakeLLM(reply="收到")
        rt = FakeRuntime(llm)
        with mock.patch.dict(os.environ, {"HOMESDK_TZ": "Asia/Tokyo"}):
            await decide_mock_mod.mock_test(skill, rt, [{"name": "s1", "mock": {}}])
        self.assertEqual(len(llm.systems), 1)
        self.assertIn("（Asia/Tokyo）", llm.systems[0])
        self.assertNotIn(LITERAL, llm.systems[0])

    async def test_engine_label_defaults_to_house_tz(self):
        # 未配置时默认仍是北京：本批只改来源，不改默认行为
        system = await self._run_engine_with_tz(None)
        self.assertIn(f"（{LITERAL}）", system)


if __name__ == "__main__":
    unittest.main()
