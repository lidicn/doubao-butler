"""批20 验收：异步结果「投出去就以为成了」的四枚落点。

来源＝第 12 份报告 doc/审计报告/doubao-butler-稳定性功能审计报告.md 的 P1-3／P1-4／P1-5，
加台账表行 21 后半（SSE 广播丢件）。四枚共同形状：**跨线程/跨循环投递的失败，调用方看不见**。

  A_*  butler/bus/mqtt_client.py:97-99 —— QueueFull 抛在事件循环回调里，
         paho 网络线程那句 `except asyncio.QueueFull` 永不到货＝死闸，且丢件无计数。
  B_*  butler/triggers/registry.py:222-241 —— func() 返回 run_coroutine_threadsafe 的
         Future，mark_success 立刻 +1，Future 的 .result() 从没调用＝协程里的异常被吞，
         台账仍写 success（假绿）。
  C_*  butler/app.py:967-1026 _presence_poll_loop 无退出条件；:779-793 停机只 cancel
         consumer，不 cancel/await 定位轮询任务。
  D_*  butler/app.py:104-110 SSE 广播 `except asyncio.QueueFull: pass`，丢件无声。

命名纪律＝全部 unittest.TestCase 方法，模块级 test_* 为 0（回归门按 AST 分母对账）。
排除项：butler/tools/schedule.py:69-79 已经 fut.result(timeout=30) 取回异常＝不是缺陷。
"""
from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import importlib
import os
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
from types import SimpleNamespace

from butler.runtime import Runtime
from butler.bus.topics import PUB_DIALOG
from butler.triggers.registry import TriggerRegistry

# 结构腿（C3/C4）读**盘上的 butler/app.py**，⛔ 依赖 import——宿主 python 没有 starlette
# （test_audit_1002_fakegreen.py:229 为同一件事已经跳过一整组腿）。
# 「读的那份」由跑尺的人**显式指路**（B20_APP_PY），⛔ 由 import 结果反推：
# 反推出来的相等断言是同义反复，测不到「runner 指的是另一枚旧副本」。
APP_ENV = os.environ.get("B20_APP_PY", "")
APP_PATH = pathlib.Path(APP_ENV or pathlib.Path(__file__).resolve().parents[1] / "butler" / "app.py").resolve()
try:
    APP_SRC = APP_PATH.read_text(encoding="utf-8")
    APP_TREE = ast.parse(APP_SRC)
    APP_SRC_ERROR = None
except Exception as _e:
    APP_SRC, APP_TREE = "", None
    APP_SRC_ERROR = repr(_e)

try:
    import butler.app as app_mod
    APP_IMPORT_ERROR = None
except Exception as _e:
    app_mod = None
    APP_IMPORT_ERROR = repr(_e)


def _need_app(case):
    if app_mod is None:
        case.skipTest("butler.app 导不进来（%s）⇒ 本组执行腿只在容器内计账" % APP_IMPORT_ERROR)
    return app_mod


def _need_app_src(case):
    """结构腿必须读得到 app.py：指错路＝FAIL。skip 会让「没读到」冒充「没问题」。"""
    if APP_SRC_ERROR is not None:
        case.fail("读不到 B20_APP_PY=%s（%s）⇒ 跑尺件指错了路径，尺子先失效"
                  % (APP_PATH, APP_SRC_ERROR))


# ---------------------------------------------------------------- 结构尺（只读源码形状）

def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _finally_body_src(func_name: str) -> str:
    """取 func_name 里第一个带 finally 的 try 的收尾体源码；找不到返回空串。"""
    fn = _find_func(APP_TREE, func_name)
    if fn is None:
        return ""
    for node in ast.walk(fn):
        if isinstance(node, ast.Try) and node.finalbody:
            return "\n".join(ast.get_source_segment(APP_SRC, st) or "" for st in node.finalbody)
    return ""


# ---------------------------------------------------------------- A：MQTT 队列满

_FAKE_PAHO_KEYS = ("paho", "paho.mqtt", "paho.mqtt.client", "butler.bus.mqtt_client")


class _FakeClient:
    """够用的 paho 替身：构造与一切方法空转（on_* 靠属性赋值，走 __getattr__ 之外的路）。"""

    def __init__(self, *a, **kw):
        pass

    def __getattr__(self, name):
        return lambda *a, **kw: None


def _fake_paho_modules():
    paho = types.ModuleType("paho")
    mqtt = types.ModuleType("paho.mqtt")
    client_mod = types.ModuleType("paho.mqtt.client")

    class CallbackAPIVersion:
        VERSION2 = 2

    client_mod.Client = _FakeClient
    client_mod.CallbackAPIVersion = CallbackAPIVersion
    mqtt.client = client_mod
    paho.mqtt = mqtt
    return {"paho": paho, "paho.mqtt": mqtt, "paho.mqtt.client": client_mod}


class MqttQueueFullLegs(unittest.TestCase):
    """P1-3：QueueFull 必须被**回调内**的 try 接住，并落成可查的丢件计数。"""

    def setUp(self):
        self._saved = {k: sys.modules.get(k) for k in _FAKE_PAHO_KEYS}
        self.tmp = tempfile.mkdtemp(prefix="b20_mqtt_")
        for k, v in _fake_paho_modules().items():
            sys.modules[k] = v
        sys.modules.pop("butler.bus.mqtt_client", None)
        self.mc = importlib.import_module("butler.bus.mqtt_client")
        from butler.config import Settings
        self.loop = asyncio.new_event_loop()
        self.c = self.mc.MQTTClient(Settings(data_dir=self.tmp), self.loop)
        self.cb_errors = []
        self.loop.set_exception_handler(self._capture_cb_error)

    def tearDown(self):
        for step in (self._close_loop, self._put_back_modules, self._rmtree):
            try:
                step()
            except Exception as e:
                print("[b20-restore] %s failed: %r" % (step.__name__, e))

    def _capture_cb_error(self, loop, context):
        self.cb_errors.append(context)

    def _close_loop(self):
        if getattr(self, "loop", None) is not None:
            self.loop.close()

    def _put_back_modules(self):
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def _rmtree(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fill_queue(self):
        for i in range(self.c.queue.maxsize):
            self.c.queue.put_nowait(("fill", {"i": i}))

    def _drive_loop(self):
        self.loop.run_until_complete(asyncio.sleep(0))

    def _deliver(self, topic="butler/test", payload=b'{"a":1}'):
        self.c._on_message(None, None, SimpleNamespace(topic=topic, payload=payload))

    def test_queue_full_is_counted_by_the_loop_callback(self):
        self._fill_queue()
        self._deliver()
        self._drive_loop()
        self.assertTrue(hasattr(self.c, "dropped"),
                        "MQTTClient 没有 dropped 计数位＝队列满丢件不可查（P1-3）")
        self.assertEqual(self.c.dropped, 1, "队列满那一条必须计入 dropped")

    def test_queue_full_does_not_surface_as_unhandled_callback_error(self):
        """旧形制的死闸：except 在网络线程，异常实际炸在循环回调里。"""
        self._fill_queue()
        self._deliver()
        self._drive_loop()
        self.assertEqual(self.cb_errors, [],
                         "QueueFull 逃进了事件循环的异常处理器＝网络线程那句 except 是死闸")

    def test_event_still_lands_when_the_queue_has_room(self):
        """护栏（改前改后都该绿）：计数不能把正常事件也吞掉。"""
        self._deliver()
        self._drive_loop()
        self.assertEqual(self.c.queue.qsize(), 1, "有位置时事件必须入队")
        self.assertEqual(getattr(self.c, "dropped", 0), 0, "没丢就别计数")

    def test_submit_failure_with_closed_loop_is_counted_too(self):
        """循环已关时 call_soon_threadsafe 当场抛 RuntimeError——旧形制会把它甩给 paho 线程。"""
        dead = asyncio.new_event_loop()
        dead.close()
        self.c.loop = dead
        err = None
        try:
            self._deliver()
        except BaseException as e:
            err = e
        self.assertIsNone(err, "投递失败⛔ 冒泡回 paho 网络线程：%r" % (err,))
        self.assertTrue(hasattr(self.c, "dropped"), "MQTTClient 没有 dropped 计数位（P1-3）")
        self.assertEqual(self.c.dropped, 1, "循环已关导致的丢件同样要计数")


# ---------------------------------------------------------------- B：定时任务 Future 异常

class _FakeAuditor:
    def __init__(self):
        self.rows = []

    def record(self, **kw):
        self.rows.append(kw)


class SchedulerFutureOutcomeLegs(unittest.TestCase):
    """P1-4：wrap_scheduler_job 必须把 run_coroutine_threadsafe 的 Future 结果取回来。"""

    def _reg(self, source_id="sched:probe"):
        aud = _FakeAuditor()
        reg = TriggerRegistry(auditor=aud)
        src = reg.register(source_id, "scheduler", "探针定时源")
        return reg, aud, src

    def _pending_future_job(self, reg, source_id):
        holder = {}

        def func():
            fut = concurrent.futures.Future()
            holder["fut"] = fut
            return fut

        return reg.wrap_scheduler_job(source_id, func), holder

    def test_failed_future_marks_error(self):
        reg, aud, src = self._reg()
        wrapped, holder = self._pending_future_job(reg, "sched:probe")
        wrapped()
        holder["fut"].set_exception(RuntimeError("loop-boom"))
        self.assertEqual(src.status, "error",
                         "协程抛了异常但台账仍是 %s＝Future 结果从没取回（P1-4）" % src.status)
        self.assertEqual(src.fail_count, 1, "fail_count 没跟上")
        self.assertIn("RuntimeError", src.last_result,
                      "last_result 要能看出是哪类异常：%r" % src.last_result)

    def test_failed_future_is_logged_at_error(self):
        reg, aud, src = self._reg()
        wrapped, holder = self._pending_future_job(reg, "sched:probe")
        wrapped()
        with self.assertLogs("butler.triggers.registry", level="ERROR") as cm:
            holder["fut"].set_exception(RuntimeError("loop-boom"))
        joined = "\n".join(cm.output)
        self.assertIn("SCHED_JOB_FAILED", joined, "失败没有可 grep 的错号")
        self.assertIn("sched:probe", joined, "错号里要点名是哪一路定时源")

    def test_dispatch_row_is_kept_and_error_row_is_added(self):
        """口径＝投递成功那一行照写（心跳每轮一行的既有语义⛔ 动），
        Future 失败再补一行 error；于是失败轮次留下两行，账面可读但⛔ 静默。"""
        reg, aud, src = self._reg()
        wrapped, holder = self._pending_future_job(reg, "sched:probe")
        wrapped()
        holder["fut"].set_exception(RuntimeError("loop-boom"))
        results = [r.get("result") for r in aud.rows]
        self.assertEqual(results, ["success", "error"],
                         "审计行形制变了：%r（每轮一行的心跳判据依赖 success 那行）" % (results,))
        self.assertEqual(src.success_count, 1)
        self.assertEqual(src.fail_count, 1)

    def test_successful_future_does_not_double_count(self):
        """护栏（⛔ 指望它先红）：成功轮次仍然只有一行 success、⛔ 二次 +1。"""
        reg, aud, src = self._reg()
        wrapped, holder = self._pending_future_job(reg, "sched:probe")
        wrapped()
        holder["fut"].set_result("ok")
        self.assertEqual(src.success_count, 1, "成功回调里别把 success 再加一次")
        self.assertEqual(src.fail_count, 0)
        self.assertEqual([r.get("result") for r in aud.rows], ["success"])

    def test_sync_job_still_marks_success_immediately(self):
        """护栏：sched:vibe_decision_timeout 那种同步 callable 行为不变。"""
        reg, aud, src = self._reg("sched:sync")
        wrapped = reg.wrap_scheduler_job("sched:sync", lambda: 42)
        self.assertEqual(wrapped(), 42, "wrapper⛔ 吃掉返回值")
        self.assertEqual(src.status, "running")
        self.assertEqual(src.success_count, 1)
        self.assertEqual(src.fail_count, 0)

    def test_real_cross_thread_dispatch_failure_is_recorded(self):
        """现网形状：循环活在另一条线程，回调在循环线程里跑。"""
        reg, aud, src = self._reg("sched:real")
        loop = asyncio.new_event_loop()

        def runner():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        th = threading.Thread(target=runner, daemon=True)
        th.start()
        try:
            async def boom():
                raise RuntimeError("cross-thread-boom")

            wrapped = reg.wrap_scheduler_job(
                "sched:real", lambda: asyncio.run_coroutine_threadsafe(boom(), loop))
            wrapped()
            deadline = time.time() + 3.0
            while time.time() < deadline and src.status != "error":
                time.sleep(0.02)
            self.assertEqual(src.status, "error",
                             "跨线程投递的协程异常没回到台账（P1-4 现网形状）")
            self.assertEqual(src.fail_count, 1)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            th.join(timeout=3.0)
            loop.close()


# ---------------------------------------------------------------- C：presence 轮询循环

class _EngineCfg:
    def __init__(self, interval):
        self.config = {"poll_interval_seconds": interval, "confidence_threshold": 0.6}
        self._users = {}
        self._rooms = {}
        self._last_poll = 0.0


class _HaSource:
    def __init__(self):
        self.calls = 0
        self.on_fetch = None

    async def fetch(self):
        self.calls += 1
        if self.on_fetch is not None:
            self.on_fetch.set()
        return {}


class _Fusion:
    def fuse(self, data):
        return {}

    def determine_locations(self, fused):
        return {}


class _Inference:
    def infer_room_occupants(self, room_id, ha_data, locations):
        return {"users": [], "unknown_person": None}


class _Store:
    def record_location(self, *a, **kw):
        pass


class PresenceLoopLegs(unittest.TestCase):
    """P1-5：循环要有退出条件，停机要把后台任务收口在 close() 之前。"""

    def _rt(self, interval, on_fetch=None):
        ha = _HaSource()
        ha.on_fetch = on_fetch
        return SimpleNamespace(
            presence_engine=_EngineCfg(interval),
            _presence_ha_source=ha,
            _presence_fusion=_Fusion(),
            _presence_inference=_Inference(),
            presence_store=_Store(),
            _stop_event=asyncio.Event(),
            ha=ha,
        )

    def _run(self, coro_factory, timeout):
        """返回 True＝coro_factory 造出来的任务在 timeout 内自己结束了。"""

        async def body():
            task = asyncio.ensure_future(coro_factory())
            try:
                await asyncio.wait_for(task, timeout=timeout)
                return True
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                return False
            except BaseException:
                task.cancel()
                raise

        return asyncio.run(body())

    def test_loop_returns_when_stop_event_is_already_set(self):
        am = _need_app(self)

        async def factory():
            rt = self._rt(interval=30)
            rt._stop_event.set()
            await am._presence_poll_loop(rt)

        self.assertTrue(self._run(factory, 2.0),
                        "stop 事件已置位，_presence_poll_loop 仍不返回（旧形制 while True）")

    def test_loop_wakes_promptly_when_stop_is_set_during_sleep(self):
        """间隔 30 秒也要在 stop 那一下醒来——判据是「等的是事件」⛔「等满周期」。"""
        am = _need_app(self)

        async def run_once():
            first = asyncio.Event()
            rt = self._rt(interval=30, on_fetch=first)
            task = asyncio.ensure_future(am._presence_poll_loop(rt))
            try:
                await asyncio.wait_for(first.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
                self.fail("轮询循环连第一轮都没跑起来")
            rt._stop_event.set()
            try:
                await asyncio.wait_for(task, timeout=2.0)
                return True
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                return False

        exited = asyncio.run(run_once())
        self.assertTrue(exited, "stop 事件置位后 30 秒间隔的循环没提前退出＝sleep(interval) 没换成等事件")

    def test_structural_legs_read_the_same_file_the_runner_executes(self):
        """护栏：C3/C4 读的那枚 app.py 必须就是被执行的那枚（⛔ 拿旧副本判停机收尾）。"""
        am = _need_app(self)
        self.assertEqual(str(APP_PATH), str(pathlib.Path(am.__file__).resolve()),
                         "结构尺读的源与执行的模块不是同一枚文件：%s vs %s"
                         % (APP_PATH, am.__file__))

    def test_shutdown_cancels_presence_task_before_closing_db(self):
        _need_app_src(self)
        fin = _finally_body_src("lifespan")
        self.assertNotEqual(fin, "", "找不到 lifespan 的 finally 体（尺子先失效，⛔ 判绿）")
        self.assertIn("_presence_poll_task", fin,
                      "停机收尾里没有定位轮询任务＝它带着 pending 跨过停机窗口（P1-5）")
        self.assertIn(".cancel()", fin, "轮询任务要 cancel")
        self.assertIn("wait_for", fin, "cancel 之后要 await 收回，否则只标了取消标志")
        self.assertIn("_stop_event", fin, "停机要先置 stop 事件，让循环自己走干净路径")
        self.assertLess(fin.index("_presence_poll_task"), fin.index("close()"),
                        "收口必须排在 close() 之前：晚一步就是往已关闭的连接里写")

    def test_stop_event_is_created_before_the_poll_task_starts(self):
        _need_app_src(self)
        fn = _find_func(APP_TREE, "lifespan")
        self.assertIsNotNone(fn, "找不到 lifespan（尺子先失效）")
        src = ast.get_source_segment(APP_SRC, fn) or ""
        self.assertIn("_stop_event", src,
                      "装配期没造 rt._stop_event＝循环读不到（P1-5 前半）")
        start = src.find("_stop_event")
        task_at = src.find("create_task(_presence_poll_loop")
        self.assertNotEqual(task_at, -1, "找不到 _presence_poll_loop 的 create_task 锚点")
        self.assertLess(start, task_at, "stop 事件必须早于轮询任务出生")


# ---------------------------------------------------------------- D：SSE 广播丢件

class SseFanoutLegs(unittest.TestCase):
    """表行 21 后半：慢订阅者的 QueueFull 要计数、要有声。"""

    def _run_consume(self, payloads, sub_prefill, sub_maxsize=1):
        am = _need_app(self)

        async def body():
            rt = Runtime()
            q = asyncio.Queue()
            for p in payloads:
                q.put_nowait(p)
            rt.mqtt = SimpleNamespace(queue=q)
            sub = asyncio.Queue(maxsize=sub_maxsize)
            for _ in range(sub_prefill):
                sub.put_nowait({"stale": True})
            rt.sse_subscribers.add(sub)
            task = asyncio.ensure_future(am._consume(rt))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return rt, sub

        return asyncio.run(body())

    def test_full_subscriber_drop_is_counted(self):
        rt, sub = self._run_consume([(PUB_DIALOG, {"a": 1})], sub_prefill=1)
        self.assertTrue(hasattr(rt, "sse_dropped"),
                        "Runtime 没有 sse_dropped 计数位＝SSE 丢件不可查（表行 21 后半）")
        self.assertEqual(rt.sse_dropped, 1, "订阅者队列满那一条必须计数")
        self.assertEqual(sub.qsize(), 1, "⛔ 靠挤掉旧消息来收新消息")

    def test_full_subscriber_drop_is_logged(self):
        _need_app(self)          # 先判可跑，⛔ 让 skipTest 落在 assertLogs 上下文里
        with self.assertLogs("butler.app", level="WARNING") as cm:
            self._run_consume([(PUB_DIALOG, {"a": 1})], sub_prefill=1)
        joined = "\n".join(cm.output)
        self.assertIn("SSE_SUBSCRIBER_QUEUE_FULL", joined, "丢件没有可 grep 的错号")

    def test_subscriber_with_room_receives_payload(self):
        """护栏（改前改后都绿）：正常投递不受计数逻辑影响。"""
        rt, sub = self._run_consume([(PUB_DIALOG, {"a": 2})], sub_prefill=0)
        self.assertEqual(sub.qsize(), 1, "有位置的订阅者必须收到")
        self.assertEqual(getattr(rt, "sse_dropped", 0), 0, "没丢就别计数")


if __name__ == "__main__":
    unittest.main(verbosity=2)
