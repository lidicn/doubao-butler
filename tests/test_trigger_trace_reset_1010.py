"""批151（10-10）：trigger 事件入口的 trace 必须「生完即还」，⛔ 留在同任务的下一条事件里。

现场证据（1010 那批冻结日志的 `scripts/audit_1010/b160_trace_chain_1010.py` 读数）：
  face_detected 出现 4 次，distinct trace = 1
  同一样本里 heartbeat 525 次 525 个号、scheduled 8 次 8 个号 ⇒ 只有 trigger 链会串。

机理：`TriggerEngine.handle_event` 在没有继承到 trace 时
`_current_trace_id.set(uuid4().hex[:16])`，但从不调 `reset(token)`。
事件流（MQTT 回调 / APScheduler）跑在长驻 task 上，而 contextvar 是**按 task 复制**的：
第一条事件把号留在该 task 的上下文里，之后每条事件 `_trace_id()` 都读到它 ⇒ 整条链共用一个号，
「这句话是谁触发的」在日志里重新变成问不出来的事（roadmap §五#2 想解决的问题没解决）。

六格形状，各自点名：
  1) 机理守卫＝set 那条腿在本环境真的跑到了（批151 起 engine 读叶子 `trace_ctx`，
     宿主/容器都吃得到；原先它绕 `butler.core.agent`，宿主无 homesdk 时被 `except: pass`
     静静吃掉＝泄漏与号都无从发生，下面几格会变成空转的绿色）。
  2) 同一 task 连办两条事件 ⇒ 两个号必须不同。（修复前红）
  3) 事件办完，contextvar 必须回到空。（修复前红）
  4) 事件体中途抛异常 ⇒ 照样还号。（修复前红）
  5) 上游已带号时：沿用上游的号，且办完还在（⛔ 把别人的号清成空）。（修复前后都绿＝反扩容守卫）
  6) `butler.core.agent._current_trace_id` 与 `trace_ctx.current_trace_id` 是同一枚对象
     （agent 在宿主不可 import ⇒ 该格显式 skip，⛔ 记成绿）。

隔离：全程不碰任何真库——执行流水与评估台账两腿都换成记录器；store 给空表，
`_match` 自然 0 命中，`_fire`/冷却/在场一律不进。
"""
import asyncio
import unittest

from butler.core.trace_ctx import current_trace_id as _current_trace_id
from butler.store import repo, write_failures
from butler.triggers.engine import TriggerEngine


class EmptyStore:
    def list(self):
        return []


class TriggerTraceResetTest(unittest.TestCase):
    def setUp(self):
        # ⛔ 让尺子碰现网 butler.db：两条写腿都换成记录器
        self.saved_eval = repo.add_trigger_evaluation
        self.saved_run = repo.add_trigger_run
        self.saved_record = write_failures.record
        self.evaluated = []
        self.ledger = []
        repo.add_trigger_evaluation = lambda *a, **k: self.evaluated.append(
            {"event": a[0], "matched": a[1], "dry_run": a[3], "trace": a[4]})
        repo.add_trigger_run = lambda *a, **k: self.persisted_run(a, k)
        write_failures.record = lambda site, error, **k: self.ledger.append(site)
        self.addCleanup(setattr, repo, "add_trigger_evaluation", self.saved_eval)
        self.addCleanup(setattr, repo, "add_trigger_run", self.saved_run)
        self.addCleanup(setattr, write_failures, "record", self.saved_record)
        # 上下文卫生：本格的起始态必须是「无号」
        self.saved_ctx = _current_trace_id.get()
        _current_trace_id.set("")
        self.addCleanup(_current_trace_id.set, self.saved_ctx)

    def persisted_run(self, a, k):
        return 0

    def build(self):
        eng = TriggerEngine(EmptyStore())
        # 记下每次进入匹配器那一刻的 contextvar 读数（＝事件体内部的现读）
        seen = []
        real_match = eng._match

        def spy_match(event_type, payload):
            seen.append(_current_trace_id.get() or "")
            return real_match(event_type, payload)

        eng._match = spy_match
        return eng, seen

    def test_born_trace_is_visible_inside_body(self):
        """格1 机理守卫：0 命中也必须能证明 set 那条腿真的执行了。"""
        eng, seen = self.build()
        asyncio.run(eng.handle_event("person_enter", {"member": "a", "room": "客厅"}))
        self.assertEqual(len(seen), 1, seen)
        self.assertEqual(len(self.evaluated), 1, self.evaluated)
        self.assertEqual(seen[0], self.evaluated[0]["trace"])
        self.assertTrue(seen[0], "事件体内读到的 trace 为空＝set 那条腿没跑到，下面几格全是空转")
        self.assertEqual(self.ledger, [], self.ledger)

    def test_two_events_in_one_task_get_distinct_traces(self):
        """格2：长驻 task 上的相邻两条事件⛔ 共用一个号。"""
        eng, seen = self.build()

        async def scenario():
            await eng.handle_event("person_enter", {"member": "a", "room": "客厅"})
            await eng.handle_event("person_enter", {"member": "b", "room": "书房"})

        asyncio.run(scenario())
        self.assertEqual(len(self.evaluated), 2, self.evaluated)
        first, second = (e["trace"] for e in self.evaluated)
        self.assertTrue(first and second, (first, second))
        self.assertNotEqual(first, second,
                            "第二条事件继承了第一条的号（防抖键不同仍被串＝泄漏在 contextvar 上）")

    def test_trace_does_not_leak_between_events(self):
        """格3：事件办完，号必须从 task 上下文收走。"""
        eng, seen = self.build()

        async def scenario():
            await eng.handle_event("person_enter", {"member": "a", "room": "客厅"})
            return _current_trace_id.get() or ""

        leaked = asyncio.run(scenario())
        self.assertEqual(leaked, "", "上一条事件的号还挂在 task 上下文里：%s" % leaked)
        self.assertEqual(len(self.evaluated), 1, self.evaluated)

    def test_reset_even_when_the_body_raises(self):
        """格4：事件体抛异常时 finally 仍要还号，否则一次崩溃污染整条 task。"""
        eng, seen = self.build()
        boom_raised = []

        async def scenario():
            real = eng._match

            def boom(event_type, payload):
                boom_raised.append(_current_trace_id.get() or "")
                raise RuntimeError("match leg blew up")

            eng._match = boom
            try:
                await eng.handle_event("person_enter", {"member": "a", "room": "客厅"})
            except RuntimeError:
                pass
            eng._match = real
            await eng.handle_event("person_enter", {"member": "b", "room": "书房"})
            return _current_trace_id.get() or ""

        leaked = asyncio.run(scenario())
        self.assertEqual(len(boom_raised), 1, boom_raised)
        self.assertEqual(leaked, "", "抛异常后号没还：%s" % leaked)
        self.assertEqual(len(self.evaluated), 1, self.evaluated)
        self.assertNotEqual(boom_raised[0], self.evaluated[0]["trace"],
                            "崩溃后的下一条事件仍继承了上一个号")

    def test_inherited_trace_is_reused_and_restored(self):
        """格5 反扩容守卫：上游给了号就沿用，办完原样还回去，⛔ 清成空。"""
        eng, seen = self.build()
        upstream = "deadbeefcafe1234"

        async def scenario():
            await eng.handle_event("person_enter", {"member": "a", "room": "客厅"})

        _current_trace_id.set(upstream)
        asyncio.run(scenario())
        self.assertEqual(seen, [upstream], seen)
        self.assertEqual(self.evaluated[0]["trace"], upstream, self.evaluated)
        self.assertEqual(_current_trace_id.get(), upstream,
                         "handle_event 把调用方的号清掉了＝上游链断在这里")


    def test_ctxvar_alias_in_agent_is_the_same_object(self):
        """格6：`agent._current_trace_id` 就是 `trace_ctx.current_trace_id`，不是第二份变量。

        宿主无 homesdk ⇒ `butler.core.agent` 整块不可 import，这格在宿主显式 skip
        （skip≠绿：读数件里它记成 skipped，谁也别把它当验收）。
        """
        try:
            from butler.core.agent import _current_trace_id as agent_var
        except Exception as e:
            self.skipTest("butler.core.agent 在本环境不可 import（%s）" % type(e).__name__)
            return
        self.assertIs(agent_var, _current_trace_id,
                      "agent 里那枚和叶子那枚不是同一个 ContextVar＝两处 set 各管各的上下文")


if __name__ == "__main__":
    unittest.main()
