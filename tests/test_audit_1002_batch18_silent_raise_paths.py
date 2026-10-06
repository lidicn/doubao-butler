r"""批18 验收 · 三枚「抛错就静默／就卡住」的缺陷（2026-10-02，来源＝第五轮／第六轮审计报告，⛔ 在合并表里）

行号＝2026-10-02 19:07–19:09Z 在权威树 `/vol1/1000/docker/doubao-butler` 现读（awk 打印行号那把尺），只作参考。

1) 第五轮 P0-7（原文严重度 P0）＝`butler/tts/singleton.py:63` 的 `loop.create_task(_queue.run())`
   （报告原文写 :37，那一行现读是 `record_ttl_drop` 段；`init_queue` 定义在 :43，全仓只在 `app.py:291` 调一次，报告写 :288）。
   全仓唯一的 TTS 队列 worker 协程被当成 fire-and-forget 起了，**返回值没保存**。CPython 官方口径：
   事件循环只对被调度任务持弱引用 ⇒ 无强引用时可能在跑完前被 GC 回收＝队列静默停止消费，日志一句没有。
   本批只治「长生命周期 worker」这一枚＋dialog 的收尾任务（第 2 枚），其余短任务登记不翻（改动面＞收益）。

2) 第六轮 P2-18（原文严重度 P2）＝`butler/core/dialog.py` 的「set_state(SPEAKING) → await 发声 → set_state(WAITING)」
   一族：发声抛错就永远停在 SPEAKING。现读 `set_state(DialogState.SPEAKING)` **8 处**（:226 :341 :353 :375 :410 :444 :502 :724），
   `create_task(self._return_idle())` **9 处**（:229 :344 :356 :378 :393 :413 :451 :507 :731；:393 只有收尾、无 SPEAKING 入口），
   全仓 `DialogState.SPEAKING` 也＝8 且全在 dialog.py ⇒ 分母闭合。
   发声确实会抛：`speak_as_role`（:627）只把 `repo.add_turn` 那段包了 try，`_emit_devices` 里
   `ha.play_xiaomi_url_once` / `ha.tts_play_url` / `tts.synthesize` 全是裸 await。
   修法＝8 处收敛成 `_speak_transition(action)` 一个出口（try/finally 保证收尾），
   收尾任务存进模块级 `_idle_tasks` 强引用注册表（:393 那一处裸 spawn 一起并进来）。

3) 第六轮 P1-22（原文严重度 P1）＝pending 技能描述**过期**后，本轮普通话语仍被喂进技能生成器。
   现读：`if _pending_exp:` 在 :330（8 空格）；过期分支 :331-334 只有 `del` + `logger.info("skill create pending expired…")`，
   注释写着「走正常流程」；而 :361 的 `if creator:` 缩进＝12 空格（与 :331 的 if / :335 的 else 同级，⛔ 在 else 里），
   `creator` 早在 :263 就取好 ⇒ **过期路径照样为真** ⇒ :363 立刻 `generate_skill_from_description(message)`，
   并在 :379 带着 `skill_create: True` 返回（连「走正常流程」都没走到）。
   ⇒ 最小修⛔ 是「过期分支置 creator = None」：那样 :373 的 `else:` 会把本轮普通话语回成「技能生成器未就绪，请稍后再试。」
   正确的修＝把 :361-379 整段（19 行）缩进进 :335 的 `else:`（只有未过期才走生成器）；行数不变，只动前导空格。

本文件 9 条腿＝先红 8（腿 1-8）＋笼头对照 1（腿 9：未过期必须仍喂生成器，修⛔ 把正路一起堵掉）。
它测不到什么（先在这里认）：
- 「无强引用的 task 真被 GC 掉」⛔ 能在单测里稳定复现（取决于时序与引用计数）；本批只证「现在存了引用、跑完会摘掉、
  worker 抛错会有 error 级日志」，GC 那一环按 CPython 文档口径陈述，⛔ 声称实测。
- dialog 那 8 处只把 `_quick_reply`（:221）拿来跑真行为，其余 7 处由结构性腿 7 钉「必须走同一个出口」，⛔ 逐处注入异常。
- 现网是否真发生过「卡在 SPEAKING」⛔ 取证（`/api/status` 的 state 读数要那次 restart 之后才有意义）。
- 腿 8/9 用的是替身 runtime（真件要碰 ask 存储与 DB），故本批⛔ 声称测到 ask 挂起与技能创建的相互影响。
- `check_simple_command` 在腿 8 里被打成定值（为的是让「过期后走正常流程」这一步确定性收口），⛔ 证明简单命令表本身正确。
  替身签名跟生产走：A′ 后 dialog 递两参（`message, rt`），本文件两个替身已改成 `(message, agent=None)`（1006 连带）。
"""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


def _ensure_paths_on_sys_path() -> None:
    """仓根 + vendored homesdk（`vendor/homesdk/src`，即 Dockerfile 的安装源）。

    宿主机没装 homesdk（它只在镜像的 site-packages 里），而 `butler/core/dialog.py:22`
    就 `from homesdk.consent import ...` ⇒ 这条补径只是让本文件的腿在宿主机能跑到真代码
    （与 tests/test_cross_repo_contracts.py 的 ROOT 补径同一手法），⛔ 生产修复。
    """
    for p in (REPO, REPO / "vendor" / "homesdk" / "src"):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


_ensure_paths_on_sys_path()


def _source(rel):
    return (REPO / rel).read_text(encoding="utf-8")


class TTSWorkerReferenceTest(unittest.IsolatedAsyncioTestCase):
    """腿 1-2：TTS 队列 worker 的 task 必须有强引用，跑完要摘掉，抛错要出声。"""

    def setUp(self):
        from butler.tts import singleton

        self.singleton = singleton
        # 单例状态逐条重置：上一轮的 _queue 会让 init_queue 走 :47「already initialized」早退，
        # 那样我的断言测的就⛔ 是被测函数（变异探针也咬不住）。
        self._old_queue = singleton._queue
        self._old_manager = singleton._manager
        singleton._queue = None
        singleton._manager = None

    def tearDown(self):
        self.singleton._queue = self._old_queue
        self.singleton._manager = self._old_manager

    def _registry(self):
        reg = getattr(self.singleton, "_worker_tasks", None)
        self.assertIsInstance(
            reg, set,
            "singleton._worker_tasks 不存在＝worker task 没被强引用持有（第五轮 P0-7 的原始形状）")
        return reg

    async def test_worker_task_is_kept_in_a_strong_reference(self):
        class IdleQueue:
            def __init__(self, *a, **k):
                pass

            async def run(self):
                await asyncio.Event().wait()

        with patch.object(self.singleton, "TTSQueue", IdleQueue):
            q = self.singleton.init_queue(object())
        self.assertIsNotNone(q)
        reg = self._registry()
        self.assertEqual(len(reg), 1, "起 worker 后必须恰好有 1 个强引用 task")
        task = next(iter(reg))
        self.assertFalse(task.done())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        self.assertEqual(len(self._registry()), 0, "task 结束后必须从注册表摘掉（⛔ 越跑越涨）")

    async def test_worker_failure_is_logged_as_error(self):
        """worker 一起来就抛 ⇒ 必须留 error 级日志（旧码：异常进了 task 对象，没人 await，一句都不响）。

        红跑阶段这条腿只认「没有 error 日志」这一个理由：注册表在这里用 getattr 读，
        ⛔ 早退成 _registry() 的「没这个属性」——那是腿 1 的理由，两条腿同因就少了一条腿。
        """
        boom = RuntimeError("worker died")

        class DeadQueue:
            def __init__(self, *a, **k):
                pass

            async def run(self):
                raise boom

        with patch.object(self.singleton, "TTSQueue", DeadQueue):
            with self.assertLogs("butler.tts.queue_singleton", level="ERROR") as captured:
                self.singleton.init_queue(object())
                for _ in range(50):
                    await asyncio.sleep(0)
                    reg = getattr(self.singleton, "_worker_tasks", None)
                    if isinstance(reg, set) and len(reg) == 0:
                        break
        joined = "\n".join(captured.output)
        self.assertIn("TTSQueue worker", joined)
        self.assertIn("RuntimeError", joined)
        self.assertEqual(len(self._registry()), 0, "失败的 task 也要从注册表摘掉")


class DialogSpeakTransitionTest(unittest.IsolatedAsyncioTestCase):
    """腿 3-7：SPEAKING 之后的收尾必须在 finally 里，发声抛错也不许卡在 SPEAKING。"""

    def _dialog(self, waiting_seconds: float = 0.0):
        from butler.core.dialog import DialogManager

        class FakeState:
            def __init__(self):
                self.seen = []

            def set_state(self, st):
                self.seen.append(st)

        # __init__ 形参序（dialog.py:41-56 现读）＝settings,state,mqtt,llm,tts,tv,ha,bark,memory,persona,dedup,wakeup,agent（13 个）
        return DialogManager(
            SimpleNamespace(waiting_seconds=waiting_seconds),  # settings
            FakeState(),                            # state
            None, None, None, None, None, None, None, None, None, None,
            SimpleNamespace(run=None),              # agent（本组腿不进 LLM）
        )

    async def test_transition_returns_value_and_restores_waiting(self):
        from butler.core.state import DialogState

        async def spoken_ok():
            await asyncio.sleep(0.01)
            return "spoken-ok"

        d = self._dialog()
        got = await d._speak_transition(spoken_ok)
        self.assertEqual(got, "spoken-ok")
        self.assertEqual(d.state.seen[0], DialogState.SPEAKING)
        self.assertEqual(d.state.seen[-1], DialogState.WAITING)

    async def test_transition_restores_waiting_when_action_raises(self):
        from butler.core.state import DialogState

        d = self._dialog()

        def boom():
            raise RuntimeError("HA 连接断了")

        with self.assertRaises(RuntimeError):
            await d._speak_transition(boom)
        self.assertIn(DialogState.WAITING, d.state.seen,
                      "第六轮 P2-18：发声抛错后状态机必须回 WAITING，⛔ 永久卡 SPEAKING")

    async def test_transition_idle_task_keeps_strong_reference(self):
        from butler.core import dialog as dialog_mod

        async def nothing():
            return None

        reg = getattr(dialog_mod, "_idle_tasks", None)
        self.assertIsInstance(
            reg, set, "_return_idle 的 task 也⛔ 丢引用（与腿 1 同形状，同一批对齐）")
        before = set(reg)
        # waiting_seconds 给非零：让收尾任务保持 pending，才看得见「在跑时有没有被注册表抓住」。
        # 上一版只在最后断「注册表为空」⇒ 去掉 _idle_tasks.add 也照样绿（变异探针 D3 咬不住）。
        d = self._dialog(waiting_seconds=30.0)
        await d._speak_transition(nothing)
        spawned = [t for t in reg if t not in before]
        self.assertEqual(len(spawned), 1,
                         "收尾任务在跑时必须在强引用注册表里（裸 create_task＝loop 只持弱引用）")
        task = spawned[0]
        self.assertFalse(task.done(), "30 秒的等待还没跑完，此刻它必须是活任务")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for _ in range(20):
            await asyncio.sleep(0)
        self.assertNotIn(task, reg, "收尾任务收尾即摘（⛔ 越跑越涨）")

    async def test_quick_reply_does_not_stick_at_speaking(self):
        """跑真的一个调用点：_quick_reply 里发声抛错 ⇒ 状态回 WAITING，异常照原样上抛。"""
        from butler.core.state import DialogState

        d = self._dialog()

        async def raise_speak(*a, **k):
            raise RuntimeError("speak failed")

        d.speak_as_role = raise_speak
        role = SimpleNamespace(id="butler", name="管家")
        with self.assertRaises(RuntimeError):
            await d._quick_reply(role, "客厅", "家人", "来了")
        self.assertIn(DialogState.WAITING, d.state.seen)

    def test_speaking_transition_has_a_single_exit_site(self):
        """结构性腿：dialog.py 里 SPEAKING 入口只许在 _speak_transition 内出现一次，
        _return_idle 的 spawn 只许在 _spawn_idle 内出现一次 ⇒ 8＋9 处旧形状不许留残。"""
        src = _source("butler/core/dialog.py")
        self.assertEqual(
            src.count("set_state(DialogState.SPEAKING)"), 1,
            "SPEAKING 入口必须收敛到一处（现读 8 处＝六轮 P2-18 的族）")
        self.assertEqual(
            src.count("create_task(self._return_idle())"), 1,
            "收尾任务 spawn 必须收敛到一处（现读 9 处，含 :393 那处裸 spawn）")
        self.assertEqual(
            src.count("self._speak_transition("), 8,
            "8 个发声出口必须都改走 _speak_transition")
        self.assertEqual(
            src.count("self._spawn_idle()"), 2,
            "_spawn_idle 的调用点＝finally 里 1 处 + :393 裸 spawn 1 处")
        self.assertIn("def _speak_transition", src)
        self.assertIn("def _spawn_idle", src)
        self.assertIn("_idle_tasks", src)


class PendingSkillExpiryTest(unittest.IsolatedAsyncioTestCase):
    """腿 8-9：pending 技能描述过期后，本轮普通话语⛔ 被喂进技能生成器；未过期必须仍喂。"""

    def _build(self):
        from butler.core import dialog as dialog_mod
        from butler.core.dialog import DialogManager

        calls = []

        class Creator:
            def get_pending(self, role_id):
                return None

            async def generate_skill_from_description(self, desc, llm):
                calls.append(desc)
                return {"ok": False, "error": "stub"}

        role = SimpleNamespace(id="butler", name="管家", system="", room="客厅",
                               voice=None, output_devices=[], enabled=True,
                               scope="family", bound_rooms=[], presence_rooms=["*"],
                               member="家人")
        rt = SimpleNamespace(roles=SimpleNamespace(get=lambda rid: role),
                             skill_creator=Creator(),
                             devices=SimpleNamespace(all=lambda: [],
                                                     resolve=lambda a, b=None: []),
                             push_guard=None, ha=None)
        state = SimpleNamespace(set_state=lambda st: None, note_speak=lambda m: None,
                                add_turn=lambda *a: None, present={})
        # 同一个 13 参数序；memory/agent 用替身，本组腿只要 on_wakeup 能在 :361 前后确定性收口
        d = DialogManager(
            SimpleNamespace(waiting_seconds=0.0), state,
            None, None, None, None, None, None,
            SimpleNamespace(retrieve=self._empty_retrieve),
            None, None, None,
            SimpleNamespace(run=self._agent_run),
        )
        return d, dialog_mod, rt, calls

    @staticmethod
    async def _empty_retrieve(*a, **k):
        return []

    @staticmethod
    async def _agent_run(*a, **k):
        return "今天多云"

    @staticmethod
    async def _stub_speak(*a, **k):
        return {"spoken": True, "devices": []}

    async def _drive(self, expire_ts):
        d, dialog_mod, rt, calls = self._build()
        role_id = "butler"
        d._pending_skill_desc[role_id] = expire_ts

        async def simple_none(message, agent=None):
            return None

        async def simple_hit(message, agent=None):
            return ("好的，已处理。", {"action": "noop"})

        # A′（批44 组5，1006）连带：`dialog.py` 现在把 runtime 一起递给 `check_simple_command(message, rt)`
        # ⇒ 两个替身都必须吃第二个参数，否则本组腿 TypeError（1006 现读：容器面＋宿主面各红一条同名腿）

        # 过期腿：把简单命令打成命中，好让「走出 pending 块」这一步确定性返回；
        # 未过期腿：简单命令不命中，pending 块必须把这句话当描述喂生成器。
        with patch.object(dialog_mod, "get_runtime", lambda: rt), \
                patch.object(dialog_mod, "check_simple_command",
                             simple_hit if expire_ts < time.time() else simple_none), \
                patch.object(dialog_mod.repo, "add_turn", lambda *a, **k: None), \
                patch.object(d, "speak_as_role", self._stub_speak), \
                patch("butler.skills.engines.llm_decide.ask.pending_ask_manager",
                      SimpleNamespace(check=lambda room: None)):
            result = await d.on_wakeup(role_id, "客厅", "今天天气怎么样", member="家人")
        return calls, result

    async def test_expired_pending_does_not_feed_generator(self):
        # ⛔ 用 0.0 当「早已过期」：dialog.py:330 是 `if _pending_exp:`，0.0 是假值 ⇒ 整块被跳过、
        # 生成器当然没被喂，这条腿会「绿得没有理由」（测的是没进分支，不是分支修好了）。
        # 用一个正数且已过去的时间戳，才真走到 :331 的过期判定。
        calls, result = await self._drive(expire_ts=time.time() - 600.0)
        self.assertEqual(
            calls, [],
            "第六轮 P1-22：过期分支注释写着「走正常流程」，但 :361 的 creator 仍是真值 ⇒ 普通闲聊被当技能描述")
        self.assertNotIn("skill_create", result,
                         "过期后必须真走出 pending 块（⛔ 仍带 skill_create 返回）")

    async def test_fresh_pending_still_feeds_generator(self):
        calls, result = await self._drive(expire_ts=time.time() + 60.0)  # 未过期
        self.assertEqual(calls, ["今天天气怎么样"],
                         "对照腿：未过期时仍必须喂生成器（修⛔ 把正路一起堵掉）")
        self.assertTrue(result.get("skill_create"),
                        "对照腿：未过期仍从 pending 块返回 skill_create")


if __name__ == "__main__":
    unittest.main()
