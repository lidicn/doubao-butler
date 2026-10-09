"""批35（10-02《doubao-butler-稳定性审计报告》分流）落下的两枚新缺陷。

覆盖（全部对权威树现读，⛔ 抄报告行号）：
- V35：`butler/tts/playback_queue.py` 的 `_ensure_worker` 只看 `device_id not in self._queues`，
  ⛔ 看那台设备的消费 task 是否已经 `done()` ⇒ worker 一旦退出，`_queues[dev]` 还在，此后
  `enqueue` 照样返回 True（账面上「已排队」），但没有任何消费者 ⇒ **该设备永久哑火直到进程重启**。
  死因也从不上报（报告 P1-11）。
- V36：`butler/tts/nowvoice_tts.py` 的 `_gen_and_download` 调 `generate_speech(text, voice_id,
  language_code)` ⛔ 带 `rate`（`nowvoice_client.py:263` 那个形参是有的，`:287-288` 只在
  `rate != 0` 时才塞进请求）⇒ 语速对 nowvoice 引擎完全无效；而批15 已经把 `speed` 放进缓存
  key（`base.py:cache_filename`），所以现在 1.0 与 1.2 会各存一份**内容相同**的音频。
  台账 §16-8／§17 当时明写「上游 speed 是否真传到位（尤其 nowvoice 那条腿）⛔ 再验」⇒ 本批收这笔。

本文件的已知盲区（先答「它测不到什么」）：
1. V36 的三条腿是**源码形状尺**（AST），⛔ 证明 nowvoice 远端服务真按 rate 变了语速——那是
   合成服务的事，本仓只保证「rate 透传到客户端调用」这一层。
2. `rate_percent` 的换算式（`(speed-1)*100` 取整）是抄 `edge_tts._rate_str` 既有口径，⛔ 另立新数。
3. V35 的行为腿直接调 `_ensure_worker`，⛔ 覆盖「worker 因 BaseException（非 Exception）退出」
   那条最窄的路——它靠 `done()` 判据本身兜住，测试里用一个正常返回的 task 当替身。
"""
import ast
import asyncio
import os
import unittest

from butler.tts.base import TTSResult, cache_filename

try:
    from butler.tts.base import rate_percent
    HAVE_RATE_HELPER = True
except ImportError:
    rate_percent = None
    HAVE_RATE_HELPER = False

from butler.tts.playback_queue import PlaybackQueue

BUTLER_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "butler")
NOWVOICE = os.path.join(BUTLER_ROOT, "tts", "nowvoice_tts.py")
EDGE = os.path.join(BUTLER_ROOT, "tts", "edge_tts.py")
BASE = os.path.join(BUTLER_ROOT, "tts", "base.py")
PLAYBACK = os.path.join(BUTLER_ROOT, "tts", "playback_queue.py")


def _src(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def generate_speech_calls(source):
    """返回 [(调用名, 关键字名集合), ...]——只认属性链末端是 generate_speech 的调用。"""
    out = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "generate_speech":
                out.append((f.attr, {kw.arg for kw in node.keywords if kw.arg}))
    return out


def rate_kw_values(source):
    """所有 generate_speech 调用里 `rate=` 那个关键字的 AST 节点（没有就不进列表）。"""
    vals = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "generate_speech":
                for kw in node.keywords:
                    if kw.arg == "rate":
                        vals.append(kw.value)
    return vals


def names_imported_from(source, module):
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            out.update(a.name for a in node.names)
    return out


def function_segment(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        # ⛔ 只写 ast.FunctionDef：async def 是 AsyncFunctionDef，整段会取空，
        # 于是 assertTrue(有防护) 假红、assertFalse(旧形状) 假绿——两条腿同时白过。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def worker_done_guard_present(source):
    """`_ensure_worker` 里是否既取了 workers 表、又判了 `.done()`（两条都要，缺一＝没修）。"""
    seg = function_segment(source, "_ensure_worker")
    return ("_workers" in seg) and (".done()" in seg)


class RatePercentTest(unittest.TestCase):
    """换算式只有 base 里那一份，且数值口径沿用 edge 的 (speed-1)*100 取整。"""

    def test_baseline_speed_is_zero_percent(self):
        self.assertTrue(HAVE_RATE_HELPER, "butler/tts/base.py 里必须有 rate_percent（V36 的唯一真源）")
        self.assertEqual(rate_percent(1.0), 0)

    def test_signed_and_rounded(self):
        self.assertTrue(HAVE_RATE_HELPER)
        self.assertEqual(rate_percent(1.2), 20)
        self.assertEqual(rate_percent(0.75), -25)
        self.assertEqual(rate_percent(2.0), 100)


class NowVoiceRatePassthroughTest(unittest.TestCase):
    """V36 三条腿：调用带 rate／rate 由 rate_percent(speed) 算／rate_percent 从 base 导入。"""

    def setUp(self):
        self.src = _src(NOWVOICE)

    def test_generate_speech_call_carries_rate(self):
        calls = generate_speech_calls(self.src)
        self.assertEqual(len(calls), 1, "nowvoice_tts.py 应恰有 1 处 generate_speech 调用")
        self.assertIn("rate", calls[0][1], "generate_speech 必须收到 rate＝语速，否则该引擎语速无效")

    def test_rate_comes_from_the_single_helper(self):
        vals = rate_kw_values(self.src)
        self.assertEqual(len(vals), 1)
        v = vals[0]
        self.assertIsInstance(v, ast.Call, "rate 必须由 rate_percent(speed) 算出，⛔ 就地抄公式")
        self.assertIsInstance(v.func, ast.Name)
        self.assertEqual(v.func.id, "rate_percent")
        self.assertEqual(len(v.args), 1)
        self.assertIsInstance(v.args[0], ast.Name)
        self.assertEqual(v.args[0].id, "speed")

    def test_helper_is_imported_from_base(self):
        self.assertIn("rate_percent", names_imported_from(self.src, "butler.tts.base"))


class EdgeDelegationTest(unittest.TestCase):
    """edge 的 `--rate` 字符串改成调用同一个真源，⛔ 再各抄一份公式。"""

    def test_rate_str_delegates(self):
        seg = function_segment(_src(EDGE), "_rate_str")
        self.assertTrue(seg, "edge_tts.py 里应仍有 _rate_str")
        self.assertIn("rate_percent(", seg, "_rate_str 必须改成调用 base.rate_percent")
        self.assertNotIn("* 100", seg, "公式只许留在 base 一处（§15 那批的教训就是四份各抄一遍）")

    def test_edge_imports_helper_from_base(self):
        self.assertIn("rate_percent", names_imported_from(_src(EDGE), "butler.tts.base"))


class PlaybackWorkerRestartTest(unittest.TestCase):
    """V35 行为腿：死的 worker 要被换掉、换掉后要真能播、死因要上报、活的⛔ 动。"""

    def _pq(self):
        return PlaybackQueue(ha_client=None, play_gap_s=0.0)

    def _run(self, coro):
        return asyncio.run(coro)

    def test_dead_worker_is_replaced_on_same_queue(self):
        async def body():
            pq = self._pq()
            q = asyncio.Queue(maxsize=5)

            async def dies_with():
                raise RuntimeError("synthetic worker death")

            dead = asyncio.ensure_future(dies_with())
            with self.assertRaises(RuntimeError):
                await dead
            pq._queues["dev1"] = q
            pq._workers["dev1"] = dead
            q2 = await pq._ensure_worker("dev1")
            new = pq._workers["dev1"]
            self.assertIs(q2, q, "队列要复用原对象，⛔ 另起一条把已排队的项目丢在旧队里")
            self.assertIsNot(new, dead, "已结束的 worker 必须被换掉")
            self.assertFalse(new.done(), "换出来的 worker 得是在跑的")
            new.cancel()
            try:
                await new
            except asyncio.CancelledError:
                pass
            return new

        self._run(body())

    def test_restarted_worker_actually_consumes(self):
        async def body():
            pq = self._pq()
            q = asyncio.Queue(maxsize=5)
            played = []
            ev = asyncio.Event()

            async def fake_play(device_id, result, url):
                played.append((device_id, result.filename, url))
                ev.set()

            pq._play_one = fake_play

            async def dead_coro():
                return None

            dead = asyncio.ensure_future(dead_coro())
            await dead
            pq._queues["dev1"] = q
            pq._workers["dev1"] = dead
            item = TTSResult(filename="f.mp3", public_url="http://x/f.mp3",
                             provider="edge-tts", voice="v", duration_ms=0, cached=False)
            q.put_nowait((item, "http://x/f.mp3", "cafe1234"))
            await pq._ensure_worker("dev1")
            try:
                await asyncio.wait_for(ev.wait(), timeout=2.0)
            finally:
                task = pq._workers.get("dev1")
                if task is not None:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            self.assertEqual(played, [("dev1", "f.mp3", "http://x/f.mp3")],
                             "换出的 worker 必须把死前已排队的这一条播掉")

        self._run(body())

    def test_death_cause_is_logged(self):
        async def body():
            pq = self._pq()
            q = asyncio.Queue(maxsize=5)

            async def dies_with():
                raise RuntimeError("synthetic worker death")

            dead = asyncio.ensure_future(dies_with())
            with self.assertRaises(RuntimeError):
                await dead
            pq._queues["dev1"] = q
            pq._workers["dev1"] = dead
            with self.assertLogs("butler.tts.queue", level="ERROR") as cap:
                await pq._ensure_worker("dev1")
                new = pq._workers["dev1"]
                await asyncio.sleep(0)
                new.cancel()
                try:
                    await new
                except asyncio.CancelledError:
                    pass
            joined = " | ".join(cap.output)
            self.assertIn("playback worker", joined)
            self.assertIn("restart", joined.lower(), "死掉要写明是重启，⛔ 混在普通 error 里")
            self.assertIn("synthetic worker death", joined, "死因（原异常）必须打出来")

        self._run(body())

    def test_live_worker_is_not_replaced(self):
        async def body():
            pq = self._pq()
            q = asyncio.Queue(maxsize=5)
            alive = asyncio.ensure_future(asyncio.sleep(30))
            pq._queues["dev1"] = q
            pq._workers["dev1"] = alive
            q2 = await pq._ensure_worker("dev1")
            self.assertIs(q2, q)
            self.assertIs(pq._workers["dev1"], alive, "在跑的 worker⛔ 被换（否则一条播到一半的任务会被丢）")
            alive.cancel()
            try:
                await alive
            except asyncio.CancelledError:
                pass

        self._run(body())


class RulerBitesTest(unittest.TestCase):
    """负控：同一把尺喂「已知真值」的夹具，两头的计数都要钉死（⛔ 只要求 >0）。"""

    REAL_CALL = (
        "from butler.tts.base import rate_percent\n"
        "def _gen():\n"
        "    c.generate_speech(text=t, voice_id=v, language_code='zh-CN', rate=rate_percent(speed))\n"
    )
    NO_RATE_CALL = (
        "def _gen():\n"
        "    c.generate_speech(text=t, voice_id=v, language_code='zh-CN')\n"
    )
    INLINED_FORMULA = (
        "def _rate_str(speed):\n"
        "    pct = int(round((speed - 1.0) * 100))\n"
        "    return f\"{pct:+d}%\"\n"
    )
    DELEGATED_FORMULA = (
        "def _rate_str(speed):\n"
        "    return f\"{rate_percent(speed):+d}%\"\n"
    )

    def test_positive_fixture_reads_one_call_with_rate(self):
        calls = generate_speech_calls(self.REAL_CALL)
        self.assertEqual(calls, [("generate_speech", {"text", "voice_id", "language_code", "rate"})])
        self.assertEqual(len(rate_kw_values(self.REAL_CALL)), 1)

    def test_negative_fixture_without_rate_is_caught(self):
        calls = generate_speech_calls(self.NO_RATE_CALL)
        self.assertEqual(len(calls), 1, "夹具里调用点要恰 1 处（尺没解析＝这条负控零信息）")
        self.assertNotIn("rate", calls[0][1])
        self.assertEqual(len(rate_kw_values(self.NO_RATE_CALL)), 0)

    def test_negative_fixture_with_inlined_formula_is_caught(self):
        self.assertNotIn("rate_percent(", function_segment(self.INLINED_FORMULA, "_rate_str"))
        self.assertIn("* 100", function_segment(self.INLINED_FORMULA, "_rate_str"))
        self.assertIn("rate_percent(", function_segment(self.DELEGATED_FORMULA, "_rate_str"))
        self.assertNotIn("* 100", function_segment(self.DELEGATED_FORMULA, "_rate_str"))

    def test_cache_filename_still_carries_speed(self):
        """批15 那条在册口径不许被这次改动带走：key 里带 speed。"""
        self.assertEqual(cache_filename("a", "v", "e", 1.0), cache_filename("a", "v", "e", 1.0))
        self.assertNotEqual(cache_filename("a", "v", "e", 1.0), cache_filename("a", "v", "e", 1.2))

    def test_function_segment_reaches_async_def(self):
        """本批第一版尺子只匹配 ast.FunctionDef，`async def _ensure_worker` 取到空串 ⇒ 两条腿一起白过。这条腿钉住 AsyncFunctionDef。"""
        seg = function_segment(
            "class C:\n"
            "    async def _ensure_worker(self, d):\n"
            "        self._workers[d] = 1\n"
            "        if self._workers[d].done():\n"
            "            pass\n",
            "_ensure_worker",
        )
        self.assertNotEqual(seg, "", "async def 必须能取到段（取空＝这把尺看不见协程方法）")
        self.assertTrue(worker_done_guard_present(
            "async def _ensure_worker(self, d):\n"
            "    self._workers[d] = 1\n"
            "    if self._workers[d].done():\n"
            "        pass\n"
        ))

    def test_ensure_worker_guard_leg_bites_on_fixture(self):
        self.assertTrue(worker_done_guard_present(_src(PLAYBACK)))
        self.assertFalse(worker_done_guard_present(
            "async def _ensure_worker(self, d):\n"
            "    if d not in self._queues:\n"
            "        self._queues[d] = asyncio.Queue()\n"
            "    return self._queues[d]\n"
        ), "旧形状（只查 _queues）必须被判 False，否则这条腿永远白过")


if __name__ == "__main__":
    unittest.main()
