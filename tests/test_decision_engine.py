"""v0.9 决策引擎回归测试（不部署、不碰真实 DB）。

WO-DB-113 修复：原文件是手写脚本（0 个 test_* 用例），且模块顶层 mkdtemp
不回收导致 /tmp 泄漏。现改为 unittest.TestCase，用 TemporaryDirectory 上下文管理。
全流程在一个 test 方法内顺序执行（与原脚本 main() 同构），避免模块缓存导致
monkeypatch 失效。
"""
import asyncio
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeLLM:
    def __init__(self):
        self.responses = []

    async def chat(self, system, messages, **kw):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, 0


class FakeHA:
    s = types.SimpleNamespace(ha_token="x")

    async def notify_message(self, message, entity_id=None):
        return "ok"


class FakeDevices:
    def all(self):
        return [types.SimpleNamespace(type="xiaomi", enabled=True, ha_entity="notify.xx",
                                      room="主卧室", id="xiao_touch8")]

    def get(self, did):
        return types.SimpleNamespace(type="xiaomi", enabled=True, ha_entity="notify.xx",
                                     room="主卧室", id=did)


class FakeRoles:
    def get(self, rid):
        return types.SimpleNamespace(output_devices=["xiao_touch8"], id=rid)

    def all(self):
        return [
            types.SimpleNamespace(id="butler", enabled=True, scope="family", bound_rooms=[], member=""),
            types.SimpleNamespace(id="agent_lidicn", enabled=True, scope="private", bound_rooms=["书房"], member="lidicn"),
        ]


class FakeNotifier:
    def __init__(self):
        self.count = 0

    async def push(self, role_id, scene, direct_text=None):
        self.count += 1
        return direct_text or scene


class FakeMemory:
    async def member_schedule(self, name, days=14):
        return {}


class FakeLocator:
    def __init__(self):
        self.memory = FakeMemory()

    def candidate_rooms(self):
        return ["客厅", "主卧室"]

    async def find_member(self, name, rooms=None, minutes=30, timeout_per=8.0):
        return {"found": False, "room": "", "last_seen": "", "via": "none",
                "confidence": 0.0, "scanned": 0, "detail": ""}


class TestDecisionEngine(unittest.TestCase):
    """决策引擎全流程回归：9 个断言顺序执行（与原脚本 main() 同构）。"""

    def test_decision_engine_full_flow(self):
        with tempfile.TemporaryDirectory(prefix="butler_decision_test_") as tmp:
            from butler.config import Settings
            import butler.store.db as db_mod
            import butler.store.repo as repo_mod

            s = Settings(data_dir=tmp)
            db_mod.get_settings = lambda: s
            repo_mod.get_settings = lambda: s

            from butler.store import repo
            from butler.decision.config import DecisionConfig
            from butler.decision.aggregator import DecisionAggregator
            from butler.decision.action_router import ActionRouter
            from butler.decision.engine import DecisionEngine

            notifier = FakeNotifier()
            rt = types.SimpleNamespace(
                llm=FakeLLM(),
                ha=FakeHA(),
                devices=FakeDevices(),
                roles=FakeRoles(),
                notifier=notifier,
                locator=FakeLocator(),
                scheduler=types.SimpleNamespace(),
            )
            cfg = DecisionConfig(tmp)
            agg = DecisionAggregator(rt, cfg)
            router = ActionRouter(rt, cfg)
            engine = DecisionEngine(rt, cfg, agg, router)
            mock = {
                "devices": {"客厅灯": "关", "主卧室空调": "开（24°C）"},
                "presence": {"主卧室": "lidicn"},
                "vision_events": [],
                "dialog": [],
                "activities": [],
            }

            def tick(responses, **kw):
                rt.llm.responses = list(responses)
                return asyncio.get_event_loop().run_until_complete(
                    engine.tick({"mock": mock, **kw})
                )

            # 1. no_action
            r = tick(['{"action":"no_action","reason":"一切正常","confidence":0.9}'])
            self.assertEqual(r["action"], "no_action")

            # 2. 白名单外（control_device）→ filtered
            r = tick(['{"action":"control_device","room":"客厅","text":"开灯","reason":"x","confidence":0.9}'])
            self.assertEqual(r["action"], "filtered")

            # 3. 低置信度 → filtered
            r = tick(['{"action":"speak","room":"主卧室","text":"该起床了","reason":"x","confidence":0.3}'])
            self.assertEqual(r["action"], "filtered")

            # 4. 有效 speak（置信度 0.9 → 执行）
            r = tick(['{"action":"speak","room":"主卧室","member":"lidicn","text":"该起床了","reason":"睡过头","confidence":0.9}'], now_hour=8)
            self.assertEqual(r["action"], "speak")

            # 5. 冷却：同样 speak 再触发 → filtered
            r = tick(['{"action":"speak","room":"主卧室","member":"lidicn","text":"该起床了","reason":"睡过头","confidence":0.9}'], now_hour=8)
            self.assertEqual(r["action"], "filtered")
            self.assertIn("冷却", r["reason"])

            # 6. notify
            r = tick(['{"action":"notify","text":"今日新闻摘要","reason":"x","confidence":0.8}'], now_hour=8)
            self.assertEqual(r["action"], "notify")
            self.assertGreaterEqual(notifier.count, 1)

            # 7. 异常 JSON → error
            r = tick(["这不是 JSON"])
            self.assertEqual(r["action"], "error")

            # 8. suggest_automation
            r = tick(['{"action":"suggest_automation","text":"发现你打开电脑时总开挂灯","reason":"习惯","confidence":0.8}'], now_hour=8)
            self.assertEqual(r["action"], "suggest_automation")

            # 9. 决策历史落库
            runs = repo.recent_decision_runs(limit=50)
            self.assertGreaterEqual(len(runs), 8)
            actions = {x["action"] for x in runs}
            self.assertIn("no_action", actions)
            self.assertIn("speak", actions)
            self.assertIn("notify", actions)
            self.assertIn("error", actions)

        # J6: with 块退出后 TemporaryDirectory 自动清理
        self.assertFalse(os.path.exists(tmp),
                         f"temp dir leaked after context exit: {tmp}")


if __name__ == "__main__":
    unittest.main()
