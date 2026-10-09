"""格14（R1 2026-09-30）·TTS 发声 trace 腿 + 两把锁改名的单测。

口径出自裁定 `E:\\NAS\\关键决策部\\decisions\\20260930-豆包管家19格-裁定.md:92`
＝第 14 格 R1「只做不碰行为的部分」，⛔ R3（一次改 24 处调用点）。

钉住四件事：
1. ``TTSManager.speak`` 是全家的嘴；新增的 ``trace_id`` / ``via`` 只进日志，
   一个都不许漏进 ``synthesize`` 或播放队列——这条就是「行为没变」的证明腿。
2. 队列那条腿（tts/adapter.py）必须把 ``TTSItem.trace_id`` 带到嘴上。
   改之前这个字段从入队到播报全仓 0 读取点；而日志尾上靠 contextvar 现取的 ``trace=``
   在 ``loop.create_task`` 起的后台 worker 里取不到请求那头的 id。
3. 两把锁改名后语义没塌：``TTSQueue._play_lock`` 仍然一条播完才播下一条。
4. 旁路落点数量钉死（纪律 3）：``rt.tts.speak(`` 直接叫嘴的业务点多一个就红。

风格说明：本文件用 ``unittest.TestCase``，因为运行镜像里没有 pytest（格17 未落地），
而 ``python -m unittest`` 只执行 TestCase 用例——pytest 风格的用例在这儿是 0 收集。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import unittest
from pathlib import Path

# 容器里 tests/ 既不在镜像也没挂载，本文件是塞进 /dev/shm 跑的 ⇒ 扫描根用环境变量指过去。
REPO = Path(os.environ.get("BUTLER_REPO", "") or
            str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(REPO))

from butler.tts.adapter import TTSManagerSpeaker          # noqa: E402
from butler.tts.manager import TTSManager                  # noqa: E402
from butler.tts.playback_queue import PlaybackQueue        # noqa: E402
from butler.tts.queue import TTSItem, TTSQueue             # noqa: E402

# 现量（2026-09-30T14:14Z，权威树 5c02040）：绕过队列直接叫嘴的业务点就这 5 个
# 审计 2026-10-02：af_bridge 的 _announce 已改走 dialog.speak_as_role（排队出口）
# ⇒旁路落点从 5 个变成 4 个；本名册只允许往"更少旁路"方向变。
DIRECT_BYPASS_SITES = {
    "butler/proactive/engine.py",
    "butler/timeseries/anomaly.py",
    "butler/morning/routine.py",
    "butler/notifier/router.py",
}
_BYPASS_RE = re.compile(r"\.tts\.speak\(")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Result:
    """TTSResult 的形状替身：speak 只把它原样交给队列。"""
    filename = "x.mp3"
    url = "http://127.0.0.1:8095/tts/x.mp3"
    provider = "nowvoice"
    cached = False


class _RecordingQueue:
    def __init__(self):
        self.plays: list[tuple[str, object]] = []

    async def enqueue(self, device_id, res):
        self.plays.append((device_id, res))


class _Mouth:
    """只借 TTSManager.speak 的真身，其余依赖换成记录器（⛔ 起整套 Settings/HA）。

    ``result`` 是类属性：两条腿（带 trace / 不带 trace）必须拿到同一个对象，
    否则「参数一字不差」会退化成比对象身份。
    """

    speak = TTSManager.speak
    result = _Result()

    def __init__(self):
        self.ha = object()               # 非 None ⇒ 让「有设备就播」那条腿真的走
        self.queue = _RecordingQueue()
        self.synth_kwargs: list[dict] = []

    async def synthesize(self, text, **kw):
        self.synth_kwargs.append(kw)
        return self.result


class MouthLegTest(unittest.TestCase):
    def setUp(self):
        self.m = _Mouth()

    def test_direct_call_logs_via_direct_and_placeholder_trace(self):
        with self.assertLogs("butler.tts.manager", level="INFO") as cap:
            res = _run(self.m.speak("灯已打开"))
        self.assertIs(res, self.m.result)
        joined = "\n".join(cap.output)
        self.assertIn("TTS_SPEAK via=direct trace=-", joined, cap.output)

    def test_new_params_never_reach_synthesize_or_playback(self):
        """行为不变的证明腿：带了 trace_id/via，synthesize 与播放拿到的参数一字不差。"""
        base = _Mouth()
        with self.assertLogs("butler.tts.manager", level="INFO"):
            _run(base.speak("要开灯吗", device_id="dev-1", member="小晨"))
        tagged = _Mouth()
        with self.assertLogs("butler.tts.manager", level="INFO"):
            _run(tagged.speak("要开灯吗", device_id="dev-1", member="小晨",
                              trace_id="deadbeef", via="queue"))
        self.assertEqual(base.synth_kwargs, tagged.synth_kwargs)
        self.assertEqual([(d, r) for d, r in base.queue.plays],
                         [(d, r) for d, r in tagged.queue.plays])
        self.assertEqual(tagged.synth_kwargs[0]["member"], "小晨")
        for key in ("trace_id", "via"):
            self.assertNotIn(key, tagged.synth_kwargs[0], f"{key} 漏进合成了")

    def test_text_is_never_put_into_the_speak_log(self):
        """脱敏边界：嘴上那条线只许记长度，⛔ 把原文写进日志。"""
        secret = "口令是勿外传的整句话"
        with self.assertLogs("butler.tts.manager", level="INFO") as cap:
            _run(self.m.speak(secret, trace_id="t1"))
        joined = "\n".join(cap.output)
        self.assertNotIn("勿外传", joined, cap.output)
        self.assertIn(f"len={len(secret)}", joined, cap.output)


class QueueLegTraceTest(unittest.TestCase):
    def setUp(self):
        self.m = _Mouth()
        self.sp = TTSManagerSpeaker(self.m)

    def test_adapter_carries_item_trace_id_to_the_mouth(self):
        item = TTSItem(text="水开了", device_id="dev-9", trace_id="a1b2c3d4")
        with self.assertLogs("butler.tts.manager", level="INFO") as cap:
            ok = _run(self.sp.speak(item))
        self.assertIs(ok, True)
        joined = "\n".join(cap.output)
        self.assertIn("TTS_SPEAK via=queue trace=a1b2c3d4", joined, cap.output)
        self.assertEqual(self.m.queue.plays[0][0], "dev-9")

    def test_item_without_trace_still_logs_a_placeholder(self):
        item = TTSItem(text="无 trace", device_id="dev-9")
        with self.assertLogs("butler.tts.manager", level="INFO") as cap:
            _run(self.sp.speak(item))
        self.assertIn("via=queue trace=-", "\n".join(cap.output), cap.output)

    def test_unresolved_device_returns_false_not_none(self):
        """TTSSpeaker 协议写明「必须返回 bool」；改前这里裸 return 给的是 None。
        唯一调用点 queue.play_one 走 bool(...)，None 与 False 同值 ⇒ 行为不变。"""
        with self.assertLogs("butler.tts.adapter", level="WARNING") as cap:
            out = _run(TTSManagerSpeaker(self.m).speak(TTSItem(text="x", room="不存在的房间")))
        self.assertIs(out, False, "必须严格是 False，不是 None")
        self.assertIn("no device resolved", "\n".join(cap.output))


class EngineLegTest(unittest.TestCase):
    """批149（2026-10-10）：现网 15/15 条 TTS_SPEAK 的 backend= 全是 "-" ⇒ 日志说不出用的哪个引擎。

    ``backend=`` 记的是"调用方要哪个"（多数调用方不点名，所以是 "-"），
    ``TTS_ENGINE`` 记的是"哪个引擎真的答了"（``TTSResult.provider``，兜底失败＝ "-"）。
    两条腿口径不同，⛔ 互相替换。
    """

    def test_engine_leg_names_the_provider_that_answered(self):
        m = _Mouth()
        with self.assertLogs("butler.tts.manager", level="INFO") as cap:
            _run(m.speak("灯已打开", device_id="dev-3", trace_id="cafe1234"))
        joined = "\n".join(cap.output)
        self.assertIn("TTS_ENGINE via=direct trace=cafe1234 engine=nowvoice device=dev-3",
                      joined, cap.output)

    def test_engine_leg_reports_placeholder_when_nothing_answered(self):
        m = _Mouth()
        m.result = None
        with self.assertLogs("butler.tts.manager", level="INFO") as cap:
            res = _run(m.speak("灯已打开", device_id="dev-3"))
        self.assertIsNone(res)
        joined = "\n".join(cap.output)
        self.assertIn("TTS_ENGINE via=direct trace=- engine=- device=dev-3", joined, cap.output)

    def test_engine_leg_carries_no_utterance_text(self):
        """脱敏边界同 TTS_SPEAK：引擎腿只记 id，⛔ 把原文带进日志。"""
        secret = "口令是勿外传的整句话"
        m = _Mouth()
        with self.assertLogs("butler.tts.manager", level="INFO") as cap:
            _run(m.speak(secret, trace_id="t9"))
        joined = "\n".join(cap.output)
        self.assertNotIn("勿外传", joined, cap.output)
        self.assertIn("engine=nowvoice", joined, cap.output)

    def test_engine_leg_does_not_reach_synthesize_kwargs(self):
        """行为不变的证明腿：新增日志不许漏进合成。"""
        m = _Mouth()
        with self.assertLogs("butler.tts.manager", level="INFO"):
            _run(m.speak("要开灯吗", device_id="dev-1", member="小晨", trace_id="e1", via="queue"))
        kw = m.synth_kwargs[0]
        self.assertNotIn("engine", kw, "engine 漏进合成了")
        self.assertEqual(sorted(kw), sorted(["member", "voice", "backend", "speed", "nowvoice_voice"]),
                         f"合成入参变了：{sorted(kw)}")


class LockNamingTest(unittest.TestCase):
    def test_queue_lock_field_is_named_for_what_it_guards(self):
        q = TTSQueue(speaker=None)
        self.assertFalse(hasattr(q, "_lock"), "旧名 _lock 还在：改名没改全")
        self.assertIsNone(q._play_lock, "play_lock 必须仍是懒创建")
        lk = q.play_lock
        self.assertIs(lk, q._play_lock)
        self.assertIsInstance(lk, asyncio.Lock)

    def test_playback_queue_lock_field_is_named_for_what_it_guards(self):
        pq = PlaybackQueue()
        self.assertFalse(hasattr(pq, "_lock"), "旧名 _lock 还在：改名没改全")
        self.assertIsInstance(pq._worker_lock, asyncio.Lock)

    def test_public_play_lock_name_is_unchanged(self):
        """公有面（tests/test_tts_queue.py:393 那条用例）不许被这次改名打断。"""
        q = TTSQueue(speaker=None)
        self.assertTrue(type(q).play_lock.fset is None)
        self.assertIs(q.play_lock, q.play_lock)

    def test_play_lock_still_serialises_playback(self):
        """改名后仍然「一条播完才播下一条」——这条是真语义，不是属性名。"""
        class SlowSpeaker:
            def __init__(self):
                self.active = 0
                self.max_active = 0
                self.order: list[str] = []

            async def speak(self, item):
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                await asyncio.sleep(0.01)
                self.order.append(item.text)
                self.active -= 1
                return True

        sp = SlowSpeaker()
        now = [1_700_000_000.0]
        q = TTSQueue(speaker=sp, clock=lambda: now[0],
                     localtime=lambda ts: time_struct_noon())
        for i in range(4):
            q.enqueue(f"m{i}", priority=3)

        async def main():
            return await asyncio.gather(*(q.play_one() for _ in range(4)))

        results = _run(main())
        self.assertEqual([True] * 4, results)
        self.assertEqual(1, sp.max_active, "顺序锁没咬住：并发播出过")
        self.assertEqual(["m0", "m1", "m2", "m3"], sp.order)
        self.assertEqual(4, q.status()["played"])


def time_struct_noon():
    import time as _t
    return _t.struct_time((2026, 9, 30, 12, 0, 0, 2, 273, -1))


class BypassRulerTest(unittest.TestCase):
    def test_direct_bypass_sites_are_exactly_the_recorded_five(self):
        """纪律 3（发声调用点零旁路）：多一个旁路就红，⛔ 悄悄长出来。"""
        root = REPO / "butler"
        self.assertTrue(root.is_dir(), f"扫不到被测目录 {root}")
        hits: set[str] = set()
        scanned = 0
        for p in sorted(root.rglob("*.py")):
            s = str(p).replace("\\", "/")
            if "/archive/" in s or ".bak" in s:
                continue
            scanned += 1
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if _BYPASS_RE.search(text):
                hits.add(str(p.relative_to(REPO)).replace("\\", "/"))
        self.assertEqual(sorted(DIRECT_BYPASS_SITES), sorted(hits),
                         f"旁路落点变了（扫了 {scanned} 个文件，根={root}）：{sorted(hits)}")
        self.assertGreater(scanned, 100, "扫描面异常小，这把尺没吃到位")


if __name__ == "__main__":
    unittest.main(verbosity=2)
