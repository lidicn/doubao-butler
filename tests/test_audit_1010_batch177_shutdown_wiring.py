"""批177 判据门 · 关停收尾（归档《最终审计报告》P1-13＝《缺陷汇总》合并行 #11「关停不收尾」的三条腿）。

现读（2026-10-10T17:44Z，镜像面）：
  · `butler/af_bridge.py:95-100 async def stop()` **全仓 0 调用点**；`butler/app.py:539` 起了 `await af_bridge.start()`，
    关停腿（`app.py:833-859` 的 `finally`）里没有它 ⇒ 轮询任务随进程消亡，容器每次重启留一条 pending 任务尾巴。
  · `butler/tts/playback_queue.py:145 async def shutdown()` **全仓 0 调用点**；`app.py:321/360` 挂着 `tts.queue`
    ⇒ 每设备的播放 worker 任务无人取消。
  · `butler/integrations/ilink/bridge.py:67 def stop()` 有定义、`app.py:504` 有 `rt.ilink_bridge.start()`，
    关停腿同样没写 ⇒ 微信桥的轮询线程 ⛔ 收尾（这一条是 `getattr(rt, "ilink_bridge", None)` 式的可选件，
    所以本门也断言它**缺件时不许抛**）。
  · `stop()` 自己还有个二阶缺陷：`_loop_task.cancel()` 之后**从不 await** ⇒ 返回那一刻任务仍是 pending。

门有两类，各管一条腿：
1. **单元腿**（`AfbStopAwaitTests`）：直接驱动 `af_bridge.stop()`，断言返回时轮询任务已 `done()`。
   用真 `asyncio` 任务＋真 `cancel()`，⛔ 桩件替被测函数。
   ⚠ 这格第一版是**假绿**：断言写在 `asyncio.run(...)` **返回之后**——`asyncio.run` 收尾会 `_cancel_all_tasks`
   把所有挂起任务跑完，于是 `task.done()` 恒真，尺子量的是 `asyncio.run` 不是 `stop()`。
   断言搬进协程内部之后才看到它真的红。
2. **形状腿**（`AppLifespanFinallyShapeTests`）：AST 读 `butler/app.py` 的 `lifespan` 函数，
   只看它 `try/finally` 的 **finally 体**里有没有那三枚调用。
   形状尺自带两向校准（合成源码"应咬 1"＋"应放行 0"），并有一格正向对照钉**已存在的** `close()` 腿
   ⇒ 尺子瞎了（恒 0）与尺子全咬（恒真）都过不了这扇门。
"""
from __future__ import annotations

import ast
import asyncio
import unittest

from butler import af_bridge


def collect_finally_calls(source: str, func_name: str) -> set[str]:
    """返回 `func_name` 函数里 `try/finally` 的 finally 体中所有调用的点号全名。"""
    tree = ast.parse(source)
    names: set[str] = set()

    def dotted(node: ast.expr) -> str:
        parts: list[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))

    found = False
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) or isinstance(fn, ast.AsyncFunctionDef):
            if fn.name != func_name:
                continue
            found = True
            for stmt in ast.walk(fn):
                if isinstance(stmt, ast.Try) and stmt.finalbody:
                    for inner in ast.walk(stmt):
                        if inner is stmt:
                            continue
                        if isinstance(inner, ast.Call):
                            names.add(dotted(inner.func))
    assert found, f"形状尺失效：源里没有 {func_name} 函数（切法错了，⛔ 把'读不到'当'没有'）"
    return names


class AfbStopAwaitTests(unittest.TestCase):
    """`af_bridge.stop()` 必须把轮询任务真的收回，⛔ 只钉一个取消标志就返回。"""

    def setUp(self):
        self._saved_task = af_bridge._loop_task
        self._saved_running = af_bridge._running

    def tearDown(self):
        af_bridge._loop_task = self._saved_task
        af_bridge._running = self._saved_running

    def test_stop_awaits_the_poll_task_it_cancels(self):
        async def _idle():
            while True:
                await asyncio.sleep(3600)

        async def scenario():
            task = asyncio.create_task(_idle())
            await asyncio.sleep(0)          # 让它真的进入循环
            af_bridge._loop_task = task
            af_bridge._running = True
            await af_bridge.stop()
            # 断言⛔ 挪出协程：`asyncio.run` 收尾时会自己把所有挂起任务取消（`_cancel_all_tasks`），
            # 出了协程再测 `done()` 是**恒真**的——那把尺子量的是 asyncio.run，不是 stop()。
            self.assertTrue(task.done(),
                            "stop() 返回时轮询任务应当已收尾（cancel 后不 await＝留一条 pending 尾巴）")
            self.assertFalse(af_bridge._running)

        asyncio.run(scenario())


_SYNTH_WITH = '''
async def lifespan(app):
    try:
        yield
    finally:
        await af_bridge.stop()
'''

_SYNTH_WITHOUT = '''
async def lifespan(app):
    try:
        yield
    finally:
        consumer.cancel()
        close()
'''


class AppLifespanFinallyShapeTests(unittest.TestCase):
    """app.py 关停腿的形状门（尺子自己先过两向校准）。"""

    @classmethod
    def setUpClass(cls):
        with open("butler/app.py", encoding="utf-8") as fh:
            cls.source = fh.read()
        cls.calls = collect_finally_calls(cls.source, "lifespan")

    def test_ruler_bites_on_synthetic_and_passes_on_clean(self):
        # 合成校准：应咬的那份恰好含目标名，应放行的那份一个都不含。
        with_calls = collect_finally_calls(_SYNTH_WITH, "lifespan")
        self.assertIn("af_bridge.stop", with_calls, "形状尺失效：合成件里明明有，尺子读不到")
        clean_calls = collect_finally_calls(_SYNTH_WITHOUT, "lifespan")
        self.assertNotIn("af_bridge.stop", clean_calls,
                         "形状尺失效：合成件里没有却读成了有（恒真尺＝没有闸）")

    def test_ruler_sees_the_legs_already_wired(self):
        # 正向对照：关停腿**早已存在**的几条必须读得到，否则"读不到新三条"没有意义。
        self.assertTrue(any(n.endswith("consumer.cancel") for n in self.calls),
                        f"尺子连 consumer.cancel 都读不到 ⇒ 切法错了。实读={sorted(self.calls)!r}")
        self.assertIn("close", self.calls, "尺子连 db.close() 都读不到 ⇒ 切法错了")

    def test_lifespan_finally_calls_af_bridge_stop(self):
        self.assertIn("af_bridge.stop", self.calls,
                      f"af_bridge.stop() 必须在关停腿被调用。实读 finally 调用={sorted(self.calls)!r}")

    def test_lifespan_finally_calls_playback_queue_shutdown(self):
        names = {n for n in self.calls if n.endswith("queue.shutdown")}
        self.assertTrue(names,
                        f"播放队列 shutdown() 必须在关停腿被调用。实读={sorted(self.calls)!r}")

    def test_lifespan_finally_calls_ilink_bridge_stop(self):
        names = {n for n in self.calls if n.endswith("ilink_bridge.stop")}
        self.assertTrue(names,
                        f"iLink 桥 stop() 必须在关停腿被调用。实读={sorted(self.calls)!r}")


if __name__ == "__main__":
    unittest.main()
