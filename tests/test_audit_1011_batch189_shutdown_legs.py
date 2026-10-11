"""批189（P1-11 关停收尾两条腿）：起了后台循环却没人停——`rt.event_stream` 与小米 token 刷新。

现读基线（`workorders/readings/1011/b189_baseline_1011.txt`，23:28:40Z，模式原文一并登记）：

1. `grep -rnE "event_stream\\.stop\\(" butler/ tests/ scripts/` ⇒ **rc=1，0 命中**。
   释放腿本身是现成的（`butler/core/event_stream.py:104 def stop()`：置 `_running=False` 并
   `self._task.cancel()`），缺的是**接线**——`app.py:817 rt.event_stream.start()` 起了它，
   `finally`（`:833` 起）里没有那一枚。这条 WS 订阅循环在 `db.close()`（接线后现读 `:892`）与
   批186／188 的十枚 `close_conn()`（`:905`／`:919`）之后还会继续跑：它的持久化腿
   `event_stream.py:259-270` 拿 `store.db.get_conn()` 写 `home_events`，失败就落
   `write_failures.record()`——**而台账连接正是关停时最后被释放的那一枚**，
   于是「往已关的通道里写」这条形状在批186／188 之后又开了一个新入口。
2. `grep -rnE "def (stop|close|shutdown)" butler/integrations/ha.py` ⇒ **rc=1，0 命中**：
   `HAClient` 没有任何停止 API。`ha.py:64` 起的 `_xiaomi_token_refresh_loop`（`:73 while True`，
   每 2h 一次换票、失败每 60s 重试）由 `app.py:333` 武装，全仓只有起腿没有停腿。

⛔ 本批顺手收的两条，登记给下一批：
- `rt.audiobook` 的播放任务（`audiobook/manager.py:186 create_task(_play_loop())`，
  `async def stop():206` 只有 `/api/audiobook/stop` 一个调用者）——它的收尾会向音箱下发
  stop／清队列（`:405-417`），属**口播路径**，动它要先过决策支援，不在本批。
- `ha.py:51 _xiaomi_watch_tasks`／`:601 _media_stop_tasks` 两张任务表只 `.get`＋赋值、
  无 pop／done 回调（且后者是**类属性**跨实例共享）——那是内存回收一族，另一批走。

判据钉**行为**（`stop_xiaomi_token_refresh()` 必须真的把活任务取消掉）也钉**形状**
（两条腿落在 `finally` 里、排在 `close()` 之前、各自 try/except＋`logger.warning`），
形状尺配两侧合成校准（⛔ 只跑真文件）。`butler/app.py` 只读源码，⛔ import（二次 import＝
在停摆库上开连接）。

**读数时效**：本档正文里的 `app.py:NNN` 是批189 那遍的现读值，此后 app.py 上方有净插行
（批195 之后批197 又插 2 行）⇒ 逐枚漂移。当前权威落点以 `scripts/audit_1002/classify_unawaited_1002.py`
的 `SITES`（按符号现读重钉）为准；本档判据本身是**从源码现读行号**（`_leg_line`），⛔ 抄正文数字。
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

CELLS = 12
# 本批在册的两条腿：finally 块里必须出现的（接收者属性名, 方法叶名）
LEGS = (("event_stream", "stop"), ("ha", "stop_xiaomi_token_refresh"))
DB_CLOSE_LEAF = "close"


def _chain(node):
    """`rt.event_stream.stop` → ["rt","event_stream","stop"]；不是纯名字链 ⇒ None。"""
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


def _has_close(calls):
    for lineno, parts, _ in calls:
        if parts == [DB_CLOSE_LEAF] or parts[-1] == DB_CLOSE_LEAF and len(parts) == 1:
            return lineno
    return None


# 合成校准件：同一条尺，放行侧必须两条都在、咬住侧必须两条都不在。
_SYNTH_CLEAN = '''
async def lifespan():
    try:
        yield
    finally:
        consumer.cancel()
        try:
            if getattr(rt, "event_stream", None) is not None:
                rt.event_stream.stop()
        except Exception as e:
            logger.warning("event_stream stop failed: %s", e)
        try:
            if getattr(rt, "ha", None) is not None:
                rt.ha.stop_xiaomi_token_refresh()
        except Exception as e:
            logger.warning("token refresh stop failed: %s", e)
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

# 第三条合成件：两条腿**接了**却用 `except: pass` 吞掉失败——专门校准 `_warns_in` 那一面
# （放行／咬住之外第三个判据⛔ 只靠真文件的一遍绿来自证）。
_SYNTH_SILENT = '''
async def lifespan():
    try:
        yield
    finally:
        try:
            rt.event_stream.stop()
        except Exception:
            pass
        rt.ha.stop_xiaomi_token_refresh()
        close()
'''


def load_b6():
    spec = importlib.util.spec_from_file_location("classify_unawaited_b189", B6_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBatch189ShutdownLegs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app_src = APP_PY.read_text(encoding="utf-8")
        cls.ha_src = HA_PY.read_text(encoding="utf-8")
        cls.block = _shutdown_block(cls.app_src)
        assert cls.block is not None, "app.py 里找不到包着 yield 的 finally"
        cls.calls = _calls_in(cls.block)

    def test_00_canary_paths_exist(self):
        self.assertTrue(APP_PY.is_file() and HA_PY.is_file() and B6_SCRIPT.is_file(),
                        "判定所依赖的文件有一枚不在位")

    def test_01_cell_count_is_pinned(self):
        got = len([m for m in dir(self) if m.startswith("test_")])
        self.assertEqual(got, CELLS,
                         f"本格数与档头 CELLS 不符（实得 {got}）：增删格必须同步改判据与台账")

    def test_02_shape_ruler_is_calibrated_both_ways(self):
        clean = _calls_in(_shutdown_block(_SYNTH_CLEAN))
        leak = _calls_in(_shutdown_block(_SYNTH_LEAK))
        silent_block = _shutdown_block(_SYNTH_SILENT)
        silent = _calls_in(silent_block)
        for attr, leaf in LEGS:
            self.assertIsNotNone(_leg_line(clean, attr, leaf),
                                 f"放行腿被咬（{attr}.{leaf}）＝这把尺会把好码读成坏码")
            self.assertIsNone(_leg_line(leak, attr, leaf),
                              f"咬住腿没咬（{attr}.{leaf}）＝这把尺永远绿，配不当闸")
            # 告警判据的两侧：合成件里接了但 `except: pass`／裸调 ⇒ 必须判假；
            # ⛔ 让 test_06 只在"真文件恰好有 logger.warning"这一侧绿着。
            s_line = _leg_line(silent, attr, leaf)
            self.assertIsNotNone(s_line, f"合成静默件里 {attr}.{leaf} 取不到落点＝这条校准空转")
            self.assertFalse(_warns_in(silent_block, s_line),
                             f"{attr}.{leaf} 静默失败被放行＝`except: pass` 那面判据没咬")
        self.assertTrue(_warns_in(_shutdown_block(_SYNTH_CLEAN),
                                  _leg_line(clean, "event_stream", "stop")),
                        "合成放行件里的告警腿没被判据认出来")
        self.assertIsNotNone(_has_close(clean), "校准件的 close() 锚取不到＝排序格是空转")

    def test_03_event_stream_stop_leg_is_wired(self):
        line = _leg_line(self.calls, "event_stream", "stop")
        self.assertIsNotNone(line,
                             "app.py 关停块里没有 `rt.event_stream.stop()`："
                             "WS 订阅循环在 db.close() 之后还往 store.db／台账里写")

    def test_04_token_refresh_stop_leg_is_wired(self):
        line = _leg_line(self.calls, "ha", "stop_xiaomi_token_refresh")
        self.assertIsNotNone(line,
                             "app.py 关停块里没有 `rt.ha.stop_xiaomi_token_refresh()`："
                             "每 2h 换票的 `while True` 循环没人收")

    def test_05_both_legs_precede_db_close(self):
        close_line = _has_close(self.calls)
        self.assertIsNotNone(close_line, "取不到 `close()` 的落点＝排序判据无从判起")
        for attr, leaf in LEGS:
            line = _leg_line(self.calls, attr, leaf)
            self.assertIsNotNone(line, f"{attr}.{leaf} 尚未接线")
            self.assertLess(line, close_line,
                            f"{attr}.{leaf} 排在 close():{close_line} 之后＝往已关的连接里写")

    def test_06_new_legs_warn_instead_of_passing(self):
        for attr, leaf in LEGS:
            line = _leg_line(self.calls, attr, leaf)
            self.assertIsNotNone(line, f"{attr}.{leaf} 尚未接线")
            self.assertTrue(_warns_in(self.block, line),
                            f"{attr}.{leaf}（:{line}）⛔ try/except＋logger.warning："
                            "关停腿静默 pass＝失败没人知道")

    def test_07_ha_client_has_a_stop_api(self):
        nodes = [n for n in ast.walk(ast.parse(self.ha_src))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == "stop_xiaomi_token_refresh"]
        self.assertEqual(len(nodes), 1, "HAClient 的停止 API 应恰好一枚")
        self.assertIsInstance(nodes[0], ast.FunctionDef,
                              "停止 API 写成协程＝关停块里裸调等于没调（批6 的 ASYNC_UNAWAITED 那族）")

    def test_08_stop_api_cancels_a_live_task(self):
        """行为腿：挂上去的活任务必须真被取消，且引用复位（⛔ 只钉形状）。"""
        import asyncio

        from butler.integrations.ha import HAClient
        h = HAClient(types.SimpleNamespace(xiaomi_pass_token=""))
        state = {}

        async def _never_ends():
            state["started"] = True
            await asyncio.sleep(3600)

        async def _scenario():
            task = asyncio.create_task(_never_ends())
            await asyncio.sleep(0)                  # 让协程真的跑进去，⛔ 测一个没启动的任务
            h._xiaomi_token_refresh_task = task
            h.stop_xiaomi_token_refresh()
            try:
                await task
            except asyncio.CancelledError:
                state["cancelled"] = True
            state["ref"] = h._xiaomi_token_refresh_task
            return state

        out = asyncio.run(_scenario())
        self.assertTrue(out.get("started"), "夹具没跑起来＝这一格什么都没测")
        self.assertTrue(out.get("cancelled"), "取消请求没落到任务上")
        self.assertIsNone(out.get("ref"), "取消后没复位引用：重连时会拿到 done 任务而跳过新循环")

    def test_09_stop_api_is_idempotent(self):
        """没起过／起完就停／停两次都⛔ 抛——关停块里那枚 try 不该被当成常开保险。"""
        from butler.integrations.ha import HAClient
        h = HAClient(types.SimpleNamespace(xiaomi_pass_token=""))
        self.assertIsNone(h._xiaomi_token_refresh_task)
        h.stop_xiaomi_token_refresh()
        h.stop_xiaomi_token_refresh()

    def test_10_event_stream_stop_clears_run_flag_and_task(self):
        """已存在的释放腿被接线，行为仍要对：`_running` 置假＋任务取消。"""
        import asyncio

        from butler.core.event_stream import EventStream
        es = EventStream(types.SimpleNamespace())
        es._running = True

        async def _scenario():
            es._task = asyncio.create_task(asyncio.sleep(3600))
            await asyncio.sleep(0)
            es.stop()
            cancelled = es._task.cancelled() or es._task.cancelling() > 0
            return es._running, cancelled

        running, cancelled = asyncio.run(_scenario())
        self.assertFalse(running, "stop() 后 _running 仍为真＝重连循环不会退出")
        self.assertTrue(cancelled, "stop() 没取消任务")

    def test_11_batch6_roster_registers_both_sites(self):
        """两条新腿进批6 的未 await 名册，且逐枚判为同步体（⛔ 让普查漏掉新调用点）。"""
        b6 = load_b6()
        stops = [(rel, line, name) for rel, line, name in b6.SITES if rel == "app.py"]
        line_map = {f"{rel}:{line}": name for rel, line, name in stops}
        tree = b6.Tree(REPO_ROOT / "butler")
        rows = []
        for attr, leaf in LEGS:
            src = APP_PY.read_text(encoding="utf-8")
            calls = _calls_in(_shutdown_block(src))
            line = _leg_line(calls, attr, leaf)
            self.assertIsNotNone(line, f"{attr}.{leaf} 尚未接线，无从入册")
            self.assertIn(f"app.py:{line}", line_map,
                          f"app.py:{line}（{attr}.{leaf}）没进批6 名册＝普查在册名单漏了新腿")
            rows.append(b6.classify(tree, "app.py", line, line_map[f"app.py:{line}"]))
        for r in rows:
            self.assertEqual(r["verdict"], "SYNC_OK",
                             f"{r['site']} 判定 {r['verdict']}（{r['why']}）：期望同步体")


if __name__ == "__main__":
    unittest.main()
