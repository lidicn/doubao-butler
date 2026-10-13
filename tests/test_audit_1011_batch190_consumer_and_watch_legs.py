"""批190（P1-11 合并行 #11 最后两条腿）：`consumer.cancel()` 没人 await＋两张 watch 表没关停腿。

现读基线（`workorders/readings/1011/b190_baseline_1011.txt`，23:57:18Z，模式原文一并登记）：

1. `grep -rnE 'consumer\\.cancel\\(\\)' butler/app.py` ⇒ `834: consumer.cancel()`；
   `grep -rnE 'wait_for\\(consumer|await consumer' butler/ tests/ --include=*.py` ⇒ **rc=1，0 命中**。
   取消标志打下去就没人收：`_consume`（`app.py:101`）的 `except asyncio.CancelledError` 只兜
   `queue.get()` 那一眼（`:106`），分发腿兜的是 `except Exception`（`:178`）——**CancelledError 在
   3.8+ 是 BaseException，会从分发腿逃出去**，所以那一轮若正在 `await rt.inbox.handle(...)`／
   `await rt.dialog.on_event(...)`，任务不会立刻停，`mqtt.stop()`（`:880`）与 `close()`（`:891`）
   先走，分发腿回头就往已关的通道里写。批177 收的是三条**外部**收尾腿，这一枚是自家 consumer。
2. `grep -rnE 'def cancel_watch_tasks|cancel_watch_tasks' butler/ tests/ scripts/` ⇒ **rc=1，0 命中**。
   两张 watch 表都没有关停腿：`ha.py:51 self._xiaomi_watch_tasks`（实例级）与
   `ha.py:601 _media_stop_tasks`（**类属性**，跨实例共享）里的任务是「播完该停」的轮询循环
   （`ha.py:594`／`:675` 挂上去），每 0.2s 读一次 HA 状态、到点连发 5 次 `media_pause`。
   进程收摊时它们还在跑＝**关停窗口里往音箱下发停播指令**，而停播路径上的 `httpx` 客户端与
   HA token 那一刻正被拆。

⛔ 本批顺手收的，登记给下一批（§二十五 档头同一条）：两张表**平时**只 `.get`＋赋值、无 `pop`／
done 回调（内存回收一族），本批的 `cancel_watch_tasks()` 只在关停那一眼清表，⛔ 治那条。
⇒ **下一批已到＝批211**（`tests/test_audit_1013_batch211_watch_task_reclaim.py`）：那条登记腿已接上
（`_forget_task_when_done` 按身份回收），同批把 `_media_stop_tasks` 从类体挪进 `__init__`。本档的
测试夹具因此从 `HAClient._media_stop_tasks` 改走 `h._media_stop_tasks`——**判据不变**（两张表的活
任务都要被取消、表要清空、连停两次⛔ 抛），只改取表的路径。

判据钉**行为**（`cancel_watch_tasks()` 必须真把两张表里的活任务取消并把表清空）也钉**形状**
（await 走 `asyncio.wait_for(consumer, timeout=…)`，⛔ 裸 `await consumer`＝分发腿不收就永远卡在这，
超时上限钉 2.0s 与批177 三条腿同值；两条腿都排在 `mqtt.stop()`／`close()` 之前、各自 try/except
＋`logger.warning`）。形状尺配四份合成件两侧校准（⛔ 只跑真文件）。`butler/app.py` 只读源码，
⛔ import（二次 import＝在停摆库上开连接）。
"""
from __future__ import annotations

import ast
import importlib.util
import os
import pathlib
import sys
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 现网⛔ 四把键（config 有启动硬门）：这里只放**明显是假的**占位值，⛔ 抄任何真凭据。
for _k, _v in (("DOUBAO_API_KEY", "dummy1"), ("DESKPILOT_API_TOKEN", "dummy2"),
               ("TASK_REPORT_TOKEN", "dummy3"), ("BUTLER_WEB_PASSWORD", "dummy4")):
    os.environ.setdefault(_k, _v)

APP_PY = REPO_ROOT / "butler" / "app.py"
HA_PY = REPO_ROOT / "butler" / "integrations" / "ha.py"
B6_SCRIPT = REPO_ROOT / "scripts" / "audit_1002" / "classify_unawaited_1002.py"

CELLS = 13
CONSUMER = "consumer"
WATCH_LEG = ("ha", "cancel_watch_tasks")
MQTT_STOP = ["mqtt", "stop"]
DB_CLOSE_LEAF = "close"
MAX_WAIT_SECONDS = 2.0


def _chain(node):
    """`rt.ha.cancel_watch_tasks` → ["rt","ha","cancel_watch_tasks"]；不是纯名字链 ⇒ None。"""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return list(reversed(parts))
    return None


def _any_yield(stmts) -> bool:
    for st in stmts:
        if any(isinstance(y, ast.Yield) for y in ast.walk(st)):
            return True
    return False


def _shutdown_block(src: str):
    """`async def lifespan` 里那条**包着 yield** 的 try 的 finally 语句表。"""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            for item in ast.walk(node):
                if isinstance(item, ast.Try) and _any_yield(item.body):
                    return item.finalbody
    return None


def _walk_stmts(stmts):
    for st in stmts:
        for node in ast.walk(st):
            yield node


def _calls_in(block) -> list:
    out = []
    for node in _walk_stmts(block):
        if isinstance(node, ast.Call):
            parts = _chain(node.func)
            if parts:
                out.append((node.lineno, parts, node))
    return out


def _leg_line(calls, attr, leaf):
    for lineno, parts, _ in calls:
        if parts[-1] == leaf and attr in parts[:-1]:
            return lineno
    return None


def _chain_line(calls, chain_parts):
    for lineno, parts, _ in calls:
        if parts == chain_parts:
            return lineno
    return None


def _warns_in(block, lineno) -> bool:
    """那枚调用是否裹在 try/except 里、且 except 体调了 `logger.warning`（⛔ 静默 pass）。"""
    for node in _walk_stmts(block):
        if not isinstance(node, ast.Try) or not node.handlers:
            continue
        hit = any(isinstance(c, ast.Call) and c.lineno == lineno
                  for c in _walk_stmts(node.body))
        if not hit:
            continue
        for h in node.handlers:
            for c in _walk_stmts(h.body):
                if isinstance(c, ast.Call) and _chain(c.func) == ["logger", "warning"]:
                    return True
    return False


def _consumer_await(block):
    """找 finally 里对 `consumer` 的 await：返回 {"line","kind","timeout"}，取不到 ⇒ None。

    `kind` 两形：`wait_for`（`await asyncio.wait_for(consumer, timeout=T)`）与
    `bare`（`await consumer`）。有 `wait_for` 时优先报它——⛔ 让「先写了裸 await、
    又补了一层 wait_for」读成缺腿。
    """
    bare = None
    for node in _walk_stmts(block):
        if not isinstance(node, ast.Await):
            continue
        v = node.value
        if isinstance(v, ast.Call):
            parts = _chain(v.func)
            if parts == ["asyncio", "wait_for"] and v.args and \
                    isinstance(v.args[0], ast.Name) and v.args[0].id == CONSUMER:
                timeout = None
                for kw in v.keywords:
                    if kw.arg == "timeout" and isinstance(kw.value, ast.Constant):
                        timeout = kw.value.value
                return {"line": node.lineno, "kind": "wait_for", "timeout": timeout}
        elif isinstance(v, ast.Name) and v.id == CONSUMER:
            bare = {"line": node.lineno, "kind": "bare", "timeout": None}
    return bare


# 合成校准件：四条腿各自配「应放行」与「应咬住」两侧，⛔ 只在真文件上跑一遍绿就自称尺子能用。
_SYNTH_CLEAN = '''
async def lifespan():
    try:
        yield
    finally:
        consumer.cancel()
        try:
            await asyncio.wait_for(consumer, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
            consumer.cancel()
            logger.warning("consumer shutdown wait failed: %s", e)
        try:
            if getattr(rt, "ha", None) is not None:
                rt.ha.cancel_watch_tasks()
        except Exception as e:
            logger.warning("watch tasks cancel failed: %s", e)
        mqtt.stop()
        close()
'''

_SYNTH_LEAK = '''
async def lifespan():
    try:
        yield
    finally:
        consumer.cancel()
        mqtt.stop()
        close()
'''

# 第三形：裸 `await consumer`（没 wait_for 兜时长）——分发腿不收就永远卡在这，⛔ 配「放行」。
_SYNTH_BARE = '''
async def lifespan():
    try:
        yield
    finally:
        consumer.cancel()
        try:
            await consumer
        except Exception as e:
            logger.warning("consumer shutdown wait failed: %s", e)
        rt.ha.cancel_watch_tasks()
        mqtt.stop()
        close()
'''

# 第四形：两条腿都接了，但 except 全用 `pass` 吞掉 ⇒ `_warns_in` 的咬住侧。
_SYNTH_SILENT = '''
async def lifespan():
    try:
        yield
    finally:
        try:
            await asyncio.wait_for(consumer, timeout=2.0)
        except Exception:
            pass
        try:
            rt.ha.cancel_watch_tasks()
        except Exception:
            pass
        mqtt.stop()
        close()
'''


def _has_close(calls):
    for lineno, parts, _ in calls:
        if parts[-1] == DB_CLOSE_LEAF and len(parts) == 1:
            return lineno
    return None


def load_b6():
    spec = importlib.util.spec_from_file_location("classify_unawaited_b190", B6_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBatch190ConsumerAndWatchLegs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app_src = APP_PY.read_text(encoding="utf-8")
        cls.ha_src = HA_PY.read_text(encoding="utf-8")
        cls.block = _shutdown_block(cls.app_src)
        assert cls.block is not None, "app.py 里找不到包着 yield 的 finally"
        cls.calls = _calls_in(cls.block)

    def test_00_canary_paths_exist(self):
        self.assertTrue(APP_PY.is_file() and HA_PY.is_file(), "判定所依赖的文件有一枚不在位")
        self.assertTrue((REPO_ROOT / "scripts" / "audit_1002" /
                         "classify_unawaited_1002.py").is_file(), "批6 普查脚本不在位")

    def test_01_cell_count_is_pinned(self):
        got = len([m for m in dir(self) if m.startswith("test_")])
        self.assertEqual(got, CELLS,
                         f"本格数与档头 CELLS 不符（实得 {got}）：增删格必须同步改判据与台账")

    def test_02_shape_ruler_is_calibrated_four_ways(self):
        clean_block = _shutdown_block(_SYNTH_CLEAN)
        leak_block = _shutdown_block(_SYNTH_LEAK)
        bare_block = _shutdown_block(_SYNTH_BARE)
        silent_block = _shutdown_block(_SYNTH_SILENT)
        clean, leak, bare, silent = (_calls_in(b) for b in (clean_block, leak_block,
                                                           bare_block, silent_block))
        # consumer await 腿：放行侧读得出 wait_for、咬住侧一枚都读不出、裸 await 侧读得出 bare
        self.assertEqual(_consumer_await(clean_block)["kind"], "wait_for", "放行件被咬＝这把尺会把好码读成坏码")
        self.assertIsNone(_consumer_await(leak_block), "裸 cancel 没被咬＝这把尺永远绿，配不当闸")
        self.assertEqual(_consumer_await(bare_block)["kind"], "bare", "裸 await 没被分出来＝超时格是空转")
        # watch 腿：放行侧在、咬住侧不在
        self.assertIsNotNone(_leg_line(clean, *WATCH_LEG), "放行件被咬（watch 腿）")
        self.assertIsNone(_leg_line(leak, *WATCH_LEG), "watch 腿没咬住＝这把尺配不当闸")
        # 告警判据两侧：静默件的两枚落点取不到就是空转，取到了必须判假
        aw = _consumer_await(silent_block)
        self.assertIsNotNone(aw, "静默件里取不到 consumer await 落点＝这条校准空转")
        self.assertFalse(_warns_in(silent_block, aw["line"]),
                         "静默 pass 的 consumer await 被放行＝`except: pass` 那面判据没咬")
        w_line = _leg_line(silent, *WATCH_LEG)
        self.assertIsNotNone(w_line, "静默件里取不到 watch 腿落点＝这条校准空转")
        self.assertFalse(_warns_in(silent_block, w_line), "watch 腿静默失败被放行")
        self.assertTrue(_warns_in(clean_block, _consumer_await(clean_block)["line"]),
                        "放行件里的告警腿没被判据认出来")
        self.assertTrue(_warns_in(clean_block, _leg_line(clean, *WATCH_LEG)),
                        "放行件里 watch 腿的告警判据空转")
        self.assertIsNotNone(_chain_line(clean, MQTT_STOP), "校准件的 mqtt.stop() 锚取不到＝排序格是空转")
        self.assertIsNotNone(_has_close(clean), "校准件的 close() 锚取不到＝排序格是空转")

    def test_03_consumer_cancel_is_awaited(self):
        aw = _consumer_await(self.block)
        self.assertIsNotNone(aw,
                             "app.py 关停块里 `consumer.cancel()` 之后没人 await 它："
                             "分发腿（`app.py:101` 的 `await rt.inbox.handle`／`dialog.on_event`）"
                             "会在 mqtt.stop()／close() 之后继续往已关的通道里写")

    def test_04_consumer_await_is_bounded(self):
        aw = _consumer_await(self.block)
        self.assertIsNotNone(aw, "consumer await 腿尚未接线")
        self.assertEqual(aw["kind"], "wait_for",
                         "裸 `await consumer`＝分发腿不响应取消时整条关停序列卡死在这")
        self.assertIsInstance(aw["timeout"], (int, float),
                              "wait_for 没写 timeout=（或写成变量）＝时长没钉，取不到判据")
        self.assertGreater(aw["timeout"], 0, "timeout=0 会把 consumer 直接掐成超时，等于没等")
        self.assertLessEqual(aw["timeout"], MAX_WAIT_SECONDS,
                             f"timeout={aw['timeout']} 超过 {MAX_WAIT_SECONDS}s：与批177 三条腿不同值，"
                             "关停窗口被这一枚拉长")

    def test_05_consumer_await_precedes_mqtt_stop_and_db_close(self):
        aw = _consumer_await(self.block)
        self.assertIsNotNone(aw, "consumer await 腿尚未接线")
        mqtt_line = _chain_line(self.calls, MQTT_STOP)
        close_line = _has_close(self.calls)
        self.assertIsNotNone(mqtt_line, "取不到 `mqtt.stop()` 的落点＝排序判据无从判起")
        self.assertIsNotNone(close_line, "取不到 `close()` 的落点＝排序判据无从判起")
        self.assertLess(aw["line"], mqtt_line,
                        f"consumer await（:{aw['line']}）排在 mqtt.stop():{mqtt_line} 之后")
        self.assertLess(aw["line"], close_line,
                        f"consumer await（:{aw['line']}）排在 close():{close_line} 之后＝往已关的连接里写")

    def test_06_consumer_await_warns_instead_of_passing(self):
        aw = _consumer_await(self.block)
        self.assertIsNotNone(aw, "consumer await 腿尚未接线")
        self.assertTrue(_warns_in(self.block, aw["line"]),
                        f"consumer await（:{aw['line']}）⛔ try/except＋logger.warning："
                        "关停腿静默 pass＝超时没人知道")

    def test_07_ha_client_has_a_watch_cancel_api(self):
        nodes = [n for n in ast.walk(ast.parse(self.ha_src))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == WATCH_LEG[1]]
        self.assertEqual(len(nodes), 1, "HAClient 的 watch 关停 API 应恰好一枚")
        self.assertIsInstance(nodes[0], ast.FunctionDef,
                              "写成协程＝关停块里裸调等于没调（批6 的 ASYNC_UNAWAITED 那族）")

    def test_08_watch_cancel_api_clears_both_tables(self):
        """行为腿：两张表里的活任务都要被取消，且表要清空（⛔ 只钉形状）。"""
        import asyncio

        from butler.integrations.ha import HAClient

        async def _never_ends():
            await asyncio.sleep(3600)

        async def _scenario():
            h = HAClient(types.SimpleNamespace(xiaomi_pass_token=""))
            t1 = asyncio.create_task(_never_ends())
            t2 = asyncio.create_task(_never_ends())
            await asyncio.sleep(0)                      # 让协程真的跑进去，⛔ 测两枚没启动的任务
            h._xiaomi_watch_tasks["dev-1"] = t1
            h._media_stop_tasks["media_player.speaker"] = t2
            armed = (len(h._xiaomi_watch_tasks), len(h._media_stop_tasks))
            try:
                h.cancel_watch_tasks()
            finally:
                h._media_stop_tasks.clear()              # 批211 起该表逐实例＝夹具⛔ 留在档间
            state = {"armed": armed,
                     "watch_left": len(h._xiaomi_watch_tasks),
                     "media_left": len(h._media_stop_tasks),
                     "cancelled_count": 0}
            for t in (t1, t2):
                try:
                    await t
                except asyncio.CancelledError:
                    state["cancelled_count"] += 1
            return state

        out = asyncio.run(_scenario())
        self.assertEqual(out["armed"], (1, 1), "夹具没把两张表都挂上＝这一格什么都没测")
        self.assertEqual(out["watch_left"], 0, "实例级 `_xiaomi_watch_tasks` 没清")
        self.assertEqual(out["media_left"], 0, "实例级 `_media_stop_tasks` 没清")
        self.assertEqual(out["cancelled_count"], 2,
                         "两张表里的任务有一枚没被取消＝关停窗口里它还会往音箱下发 pause")

    def test_09_watch_cancel_api_is_idempotent(self):
        """空表／连停两次都⛔ 抛——关停块里那枚 try 不该被当成常开保险。"""
        from butler.integrations.ha import HAClient
        h = HAClient(types.SimpleNamespace(xiaomi_pass_token=""))
        self.assertEqual(len(h._xiaomi_watch_tasks), 0)
        self.assertEqual(len(h._media_stop_tasks), 0)
        h.cancel_watch_tasks()
        h.cancel_watch_tasks()
        self.assertEqual(len(h._media_stop_tasks), 0)

    def test_10_watch_leg_is_wired_before_db_close(self):
        line = _leg_line(self.calls, *WATCH_LEG)
        self.assertIsNotNone(line,
                             "app.py 关停块里没有 `rt.ha.cancel_watch_tasks()`："
                             "「播完该停」的轮询任务在 close() 之后还发 media_pause")
        close_line = _has_close(self.calls)
        self.assertIsNotNone(close_line, "取不到 `close()` 的落点＝排序判据无从判起")
        self.assertLess(line, close_line,
                        f"watch 腿（:{line}）排在 close():{close_line} 之后")
        mqtt_line = _chain_line(self.calls, MQTT_STOP)
        self.assertIsNotNone(mqtt_line, "取不到 `mqtt.stop()` 的落点＝排序判据无从判起")
        self.assertLess(line, mqtt_line, f"watch 腿（:{line}）排在 mqtt.stop():{mqtt_line} 之后")

    def test_11_watch_leg_warns_instead_of_passing(self):
        line = _leg_line(self.calls, *WATCH_LEG)
        self.assertIsNotNone(line, "watch 腿尚未接线")
        self.assertTrue(_warns_in(self.block, line),
                        f"watch 腿（:{line}）⛔ try/except＋logger.warning：关停腿静默 pass＝失败没人知道")

    def test_12_batch6_roster_registers_the_watch_site(self):
        """新调用点是同步体 ⇒ 必须进批6 的未 await 名册（⛔ 让普查漏掉新腿）。"""
        b6 = load_b6()
        src = APP_PY.read_text(encoding="utf-8")
        line = _leg_line(_calls_in(_shutdown_block(src)), *WATCH_LEG)
        self.assertIsNotNone(line, "watch 腿尚未接线，无从入册")
        line_map = {f"app.py:{rel_line}": name for rel, rel_line, name in b6.SITES if rel == "app.py"}
        self.assertIn(f"app.py:{line}", line_map,
                      f"app.py:{line}（{WATCH_LEG[1]}）没进批6 名册＝普查在册名单漏了新腿")
        tree = b6.Tree(REPO_ROOT / "butler")
        row = b6.classify(tree, "app.py", line, line_map[f"app.py:{line}"])
        self.assertEqual(row["verdict"], "SYNC_OK",
                         f"{row['site']} 判定 {row['verdict']}（{row['why']}）：期望同步体")


if __name__ == "__main__":
    unittest.main()
