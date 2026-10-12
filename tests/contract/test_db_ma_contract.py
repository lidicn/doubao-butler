"""DB→MA 主通道契约闸（ADM 联动执行计划-DB 第 6 步 ① ＋ 契约表 §三/§五）。

上位：`E:\\NAS\\homesdk\\doc\\ADM联动主题注册表与消息契约.md` v1.0 §三（MCP 调用面）
      ＋ 同目录 `homesdk-0.3.1-py3-none-any.whl` 里的 `homesdk/presence.py`（机器可读常量）。
一条闸管四件事，每件都指到一次现读：
  1) `retrieve_agent_memories` 的 member_id **必填、fail-closed**（§三原文），DB 缺主体时⛔ 外发；
  2) DB 往 MA 发的工具名只许落在「已登记 ∪ 待 DCD 登记」名单里，新增即红（名单在码里、有出处）；
  3) Bearer 头单点构造、缺凭据显式失败（⛔ 发空凭据）；
  4) MCP 信封是 `{name, arguments}`。
⛔ 真网络：`call_tool` 在 setUp 里换成记录器，一条请求都不许出门。
"""
import ast
import asyncio
import os
import unittest
from pathlib import Path

import butler as _butler_pkg

# 权威源＝活包所在树（容器里 /app/butler 就是部署树的绑定挂载），⛔ 相对 __file__ 猜仓库根
REPO = Path(_butler_pkg.__file__).resolve().parent.parent
MA_SRC = REPO / "butler" / "integrations" / "memory_agent.py"

# 契约表 §三 点名的 DB→MA 工具（原文照抄，出处＝该表「三、MCP 调用面」表体）
REGISTERED_MA_TOOLS = {
    "ask_memory",
    "get_member_persona",
    "analyze_behavior_change",
    "run_analysis_template",
    "retrieve_agent_memories",
}
# DB 在用、但契约表 §三 尚未登记的工具。出处＝`E:\\NAS\\关键决策部\\inbox\\20261001-DB-重启窗判读与第二窗申请-决策申请.md` §十一-4。
# 这张名单是「待登记」不是「已豁免」：表里补登记后应从此处删名；名单里的名字若 DB 已不再调用，
# test_05 会反过来判红（⛔ 例外名单悄悄变成常驻）。
PENDING_MA_TOOLS = {
    "list_agent_memories",
    "get_vision_status",
    "analyze_camera",
    "add_semantic_memory",
    "revoke_memory",
}


def ma_tool_names_called() -> set:
    """从源码 AST 枚举 `*.call_tool("字面量"`，⛔ 手挑清单（手挑＝假绿通道）。"""
    found = set()
    for py in sorted((REPO / "butler").rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=str(py))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "call_tool" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                found.add(node.args[0].value)
    return found


class _ClientHarness(unittest.TestCase):
    def setUp(self):
        from butler.config import Settings
        from butler.integrations.memory_agent import MemoryAgentClient
        self.calls = []
        c = MemoryAgentClient(Settings(data_dir="/tmp/dd_contract"))
        c.token = "SENTINEL-MA-MCP"          # 测试自造的假凭据，⛔ 不是现网值
        c.s.memory_agent_butler_token = "SENTINEL-MA-BUTLER"
        c.s.memory_agent_app_token = "SENTINEL-MA-APP"
        c.ensure_initialized = lambda: asyncio.sleep(0)

        async def recorder(name, arguments, timeout=8):
            self.calls.append((name, dict(arguments)))
            return {"result": {"content": []}}

        c.call_tool = recorder
        self.c = c

    def run_coro(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class DbMaContractTest(_ClientHarness):
    def test_00_recorder_captures_the_envelope(self):
        """尺先自证：记录器真吃到了调用，否则下面的 0 都是「没测」。"""
        self.run_coro(self.c.ask_memory("昨天书房空调开了多久"))
        self.assertEqual([n for n, a in self.calls], ["ask_memory"])

    def test_01_ask_memory_arguments_are_question_and_days(self):
        self.run_coro(self.c.ask_memory("客厅今天有人吗", days=3))
        name, args = self.calls[0]
        self.assertEqual(name, "ask_memory")
        self.assertEqual(set(args.keys()), {"question", "days"})
        self.assertEqual(args["days"], 3)

    def test_02_retrieve_refuses_locally_without_member_id(self):
        """契约表 §三：`retrieve_agent_memories`（member_id 必填，fail-closed）。
        缺主体时 DB 必须**连请求都不发**——发出去就是请 MA 把全家记忆捞进当前对话。"""
        out = self.run_coro(self.c.retrieve("谁喜欢喝美式", member=""))
        self.assertEqual(out, [])
        self.assertEqual([n for n, a in self.calls], [],
                         "没主体却外发了 retrieve_agent_memories＝§三 的 fail-closed 没咬合")

    def test_03_retrieve_sends_member_id_when_present(self):
        self.run_coro(self.c.retrieve("谁喜欢喝美式", member="lidicn", limit=2))
        name, args = self.calls[0]
        self.assertEqual(name, "retrieve_agent_memories")
        self.assertEqual(args["member_id"], "lidicn")
        self.assertEqual(args["top_k"], 2)

    def test_04_called_tools_are_in_registered_or_pending(self):
        called = ma_tool_names_called()
        allowed = REGISTERED_MA_TOOLS | PENDING_MA_TOOLS
        self.assertTrue(called, "AST 一个工具名都没枚举到＝这把尺的分母是 0，判据无效")
        self.assertLessEqual(called - allowed, set(),
                             "未登记的 MA 工具上了调用面：%s" % sorted(called - allowed))

    def test_05_pending_list_has_no_dead_entries(self):
        called = ma_tool_names_called()
        self.assertEqual(PENDING_MA_TOOLS - called, set(),
                         "这些名字 DB 已不调用，请从待登记名单删掉（别把例外养成长驻）：%s"
                         % sorted(PENDING_MA_TOOLS - called))

    def test_06_bearer_is_single_point_and_fail_closed(self):
        from butler.integrations.memory_agent import CredentialMissing
        h = self.c.bearer_headers("mcp")
        self.assertEqual(h, {"Authorization": "Bearer SENTINEL-MA-MCP"})
        self.c.token = ""
        with self.assertRaises(CredentialMissing):
            self.c.bearer_headers("mcp")
        with self.assertRaises(KeyError):
            self.c.bearer_headers("no_such_surface")

    def test_07_negative_control_ruler_bites(self):
        """负控：故意让 retrieve 在外发前不设闸，test_02 那条判据必须反过来命中。
        它若照样绿＝test_02 只是恒真断言。"""
        async def leaky_retrieve(query, member="", limit=5):
            await self.c.call_tool("retrieve_agent_memories", {"query": query, "top_k": limit})
            return []
        self.calls.clear()
        self.run_coro(leaky_retrieve("x", member=""))
        self.assertEqual([n for n, a in self.calls], ["retrieve_agent_memories"],
                         "夹具没生效＝这条负控什么都没测")

    def test_08_registered_names_still_match_the_contract_doc(self):
        """登记表名与契约表原文对账：文件在盘上时现读，读不到就判「未量」而不是判绿。"""
        doc = os.environ.get("ADM_CONTRACT_DOC")
        if not doc or not Path(doc).exists():
            self.skipTest("ADM_CONTRACT_DOC 未给出或对不上＝本腿未量（⛔ 当绿）")
            return
        text = Path(doc).read_text(encoding="utf-8", errors="replace")
        for tool in REGISTERED_MA_TOOLS:
            self.assertIn(tool, text, "契约表里点名过 %s，现在文本里找不到＝表被我改了或表换版了" % tool)


if __name__ == "__main__":
    unittest.main()
