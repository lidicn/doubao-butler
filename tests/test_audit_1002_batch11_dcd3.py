"""批11 验收＝DCD 裁定③（优先级 TTL 落地：critical 不过期／warning 1800s／info 300s + 过期要可查）。

裁定文书＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md:44-52`：
  > **裁定 A。** `PRIORITY_TTL` 全树引用只有定义行本身，而文件头、`DECISION_DROP` 注释、
  > 两处 docstring 共四处文字在同一条承诺上盖章——**L4 这一层是空号**。
  > **落地点**：消息进风控时带时间戳（`check_tts` 已有 `now`）+ 一条过期分支 + 审计表（让「为什么没响」可查）。
  > TTL 从**消息进风控那一刻**起算（⛔「播报失败重投」起算：失败风暴下永不到期）。

现读（权威树 HEAD `9aa5862`，本批开工前 `cat -n` 逐条点开）＝改前的形状：
  · `guard/push_guard.py:36 PRIORITY_TTL`＝那枚空号：全仓 `PRIORITY_TTL` 命中只有定义行；
  · 真正在跑的队列是 `tts/queue.py`：`:121 ttl_s: float = 300.0` 只有一个默认值 ⇒
    **告警（PRIORITY_ALERT=1）也在 300s 上过期**，方向与裁定表相反；
    `:277 ttl = self.config.ttl_s if item.ttl_s is None else float(item.ttl_s)`＝优先级不参与 TTL；
    `:488 _sweep()` 到期只 `self._dropped["expired"] += dropped`（`:500`）＝**一条计数，没有一条「这条为什么没播」**；
  · `notify/router.py:120 ttl_s: float | None = None`，全仓 ⛔ 任何调用方给它赋过值 ⇒
    现网每一条通知都吃默认 300s（本批的落点就在这条链上）；
  · `api/tts_routes.py:262-293`：route 自己播完才 `pg.tts_pop()`＋`tts_unlock()`，
    guard 的 `_tts_queue` 是按调用 1:1 排空的计数器，条目在里面**不产生年龄** ⇒
    在 guard 里写过期分支＝再造一条假承诺（那套队列的去留是裁定⑤，本批不占）。

⛔ 本文件不覆盖的（留给台账／下一批裁）：
  1. 裁定⑤「两套队列归一」——本批只让 TTL 在**现网真正在跑的那条队列**上生效并留审计，
     guard 的 `_tts_queue`/`tts_pop` 仍未参与播放；
  2. TTL 数值本身的产品语义（1800s 是否合适）；本批把裁定表里的三个数接上链路的**默认值**，
     可经 `TTSQueueConfig.ttl_by_priority` 改，⛔ 改默认数＝产品语义变更。

命名纪律：全部写成 `unittest.TestCase` 方法（⛔ 模块级 `test_*`）。
运行时腿按跟办 3：假时钟＋假 speaker，审计表写在 `tempfile.mkdtemp()` 的独立库里，⛔ 碰活库。
"""
from __future__ import annotations

import re
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from butler.guard import push_guard as pg_mod
from butler.guard.push_guard import PushGuard
from butler.runtime import _RT
from butler.tts import singleton
from butler.tts.queue import (
    PRIORITY_ALERT,
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    TTSItem,
    TTSQueue,
    TTSQueueConfig,
)

_MISSING = object()


class FakeTime:
    """可控时钟：epoch 推进 + 固定本地正午（避开夜间静默窗口对本批用例的干扰）。"""

    def __init__(self, start: float = 1_700_000_000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def localtime(self, ts: float):
        return time.struct_time((2026, 10, 2, 12, 0, 0, 4, 275, -1))

    def advance(self, secs: float) -> None:
        self.t += secs


def _queue(**kw) -> tuple[TTSQueue, FakeTime]:
    ft = FakeTime()
    q = TTSQueue(speaker=None, config=kw.pop("config", None),
                 clock=ft, localtime=ft.localtime, **kw)
    return q, ft


class BandTtlTest(unittest.TestCase):
    """裁定表的三个数必须接进**现网那条队列**：critical 不过期／warning 1800s／info 300s。"""

    def test_alert_band_never_expires(self):
        q, ft = _queue()
        item = q.enqueue("燃气报警", priority=PRIORITY_ALERT).item
        ft.advance(10 * 86400)
        q.status()
        self.assertEqual(len(q), 1, "告警在队列里过了 10 天还在（critical 不过期）")

    def test_high_band_lives_1800s(self):
        q, _ft = _queue()
        item = q.enqueue("水培该换水了", priority=PRIORITY_HIGH).item
        self.assertEqual(item.expires_at - item.created_at, 1800)

    def test_standard_band_lives_300s(self):
        q, _ft = _queue()
        for p in (PRIORITY_NORMAL, 4, 5):
            item = q.enqueue(f"标准带 p{p}", priority=p).item
            self.assertEqual(item.expires_at - item.created_at, 300, f"priority={p}")

    def test_explicit_ttl_still_wins_over_the_band_table(self):
        """调用方显式给的 ttl_s ⛔ 被档位表吃掉——那是第二条「接上」变成「改写」。"""
        q, _ft = _queue()
        item = q.enqueue("自己带时长", priority=PRIORITY_ALERT, ttl_s=10).item
        self.assertEqual(item.expires_at - item.created_at, 10)

    def test_band_table_is_configurable(self):
        """档位表是**默认值**不是硬编码：配置能改，裁数值时不用动代码。"""
        q, _ft = _queue(config=TTSQueueConfig(ttl_by_priority={PRIORITY_ALERT: 5.0}))
        item = q.enqueue("配置改过的告警档", priority=PRIORITY_ALERT).item
        self.assertEqual(item.expires_at - item.created_at, 5)

    def test_expired_alert_survives_overload_pause(self):
        """过载暂停期间保留的 P1：TTL 从暂停结束时刻起算那句（queue.py:302）⛔ 因为本批而失效。"""
        q, ft = _queue()
        q.enqueue("告警", priority=PRIORITY_ALERT)
        q._pause_reason = "overload"
        q._paused_until = ft.t + 600
        ft.advance(3000)
        q.status()
        self.assertEqual(len(q), 1)


class ExpiryAuditTest(unittest.TestCase):
    """DCD③「审计表（让『为什么没响』可查）」：过期那条要带着身份出去，⛔ 只留一个计数。"""

    def test_expired_item_is_handed_to_the_audit_hook(self):
        seen: list[tuple] = []
        q, ft = _queue(on_expired=lambda item, now: seen.append((item, now)))
        item = q.enqueue("5 分钟前的旧提醒", priority=PRIORITY_NORMAL, trace_id="tr-9").item
        ft.advance(301)
        q.status()  # status 里有 _sweep
        self.assertEqual(len(seen), 1, "过期一条 ⇒ 审计一条")
        got_item, got_now = seen[0]
        self.assertIs(got_item, item)
        self.assertEqual(got_item.trace_id, "tr-9")
        self.assertAlmostEqual(got_now, ft.t)

    def test_alert_is_not_handed_to_the_audit_hook(self):
        seen: list[tuple] = []
        q, ft = _queue(on_expired=lambda item, now: seen.append((item, now)))
        q.enqueue("告警不过期", priority=PRIORITY_ALERT)
        ft.advance(10 * 86400)
        q.status()
        self.assertEqual(seen, [], "不过期的东西不许出现在过期审计里（假绿通道）")

    def test_audit_hook_failure_does_not_break_the_queue(self):
        """审计腿自身出错 ⇒ 只丢审计，⛔ 丢播放队列（fail-open，与裁定②那份定义同策）。"""
        def boom(item, now):
            raise RuntimeError("audit down")

        q, ft = _queue(on_expired=boom)
        q.enqueue("旧提醒", priority=PRIORITY_NORMAL)
        q.enqueue("还新的", priority=PRIORITY_HIGH)
        ft.advance(301)
        st = q.status()
        self.assertEqual(st["size"], 1, "过期那条照常出队，另一条照常留着")
        self.assertEqual(st["dropped"]["expired"], 1)


class GuardAuditTableTest(unittest.TestCase):
    """审计落点＝现成那张 `push_audit`（push_guard.db），面板与 `/api/guard/audit` 已经在读它。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="dbt_batch11_"))
        self.pg = PushGuard(data_dir=str(self.dir))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_record_ttl_drop_writes_a_queryable_row(self):
        self.pg.record_ttl_drop(text="浇水提醒", priority="info",
                                age_s=305.0, ttl_s=300.0,
                                job_id="tts-1700000000-1", trace_id="tr-9")
        conn = sqlite3.connect(self.pg._db_path)
        rows = conn.execute(
            "SELECT channel, action, priority, reason, body_preview FROM push_audit"
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        channel, action, priority, reason, preview = rows[0]
        self.assertEqual((channel, action, priority), ("tts", "drop", "info"))
        self.assertIn("ttl expired", reason)
        self.assertIn("305s", reason)
        self.assertIn("trace=tr-9", reason)
        self.assertEqual(preview, "浇水提醒")
        got = self.pg.get_audit(action="drop")
        self.assertEqual(len(got), 1, "「为什么没响」能从审计查询里拿到")

    def test_record_ttl_drop_never_raises_when_db_is_unwritable(self):
        """风控自己的库写不动 ⇒ 记一条 warning，⛔ 把播放链路带下去。"""
        broken = PushGuard(data_dir="")           # 无 db_path：只走内存审计
        broken.record_ttl_drop(text="x", priority="warning", age_s=9.0, ttl_s=1.0)
        self.assertEqual(len(broken.get_audit(action="drop")), 1)


class NoSecondDefinitionTest(unittest.TestCase):
    """判例「说了不做比没做更坏」：guard 那枚没人读的空号要被**拆掉**，数字只留一处。"""

    def test_guard_no_longer_ships_an_unread_ttl_table(self):
        self.assertFalse(hasattr(pg_mod, "PRIORITY_TTL"),
                         "空号要么接上要么拆掉；本批接的是队列那侧，guard 这张表应当消失")

    def test_guard_prose_stops_claiming_l4_ttl(self):
        """判据钉的是**那句原承诺**（「L4 TTL 过期：按优先级设置不同 TTL」＝本层在做），⛔ 「L4」这个标签。

        我第一版把探针写成 `"L4 TTL"`，改口后的行（「L4 TTL 过期：执行在真正排队的 tts/queue.py」）
        照样命中＝判据测的是词不是语义。红跑证据留 `workorders/readings/1002b11/`。
        """
        doc = pg_mod.__doc__ or ""
        self.assertNotIn("L4 TTL 过期：按优先级设置不同 TTL", doc,
                         "文件头那行承诺的是「本层做 TTL」，本层不做了就得改口")
        self.assertIn("butler/tts/queue.py", doc, "改口要指到真正执行的那侧，⛔ 只删不指")

    def test_ttl_numbers_exist_in_exactly_one_table(self):
        from butler.tts import queue as q_mod
        table = q_mod.PRIORITY_TTL_S
        self.assertEqual(table[PRIORITY_ALERT], 0)
        self.assertEqual(table[PRIORITY_HIGH], 1800)
        self.assertEqual(table[PRIORITY_NORMAL], 300)

    def test_level_names_have_a_real_caller(self):
        """审计行的 priority 用风控那侧的词表（critical/warning/info），⛔ 再造一份。"""
        from butler.tts import queue as q_mod
        self.assertEqual(q_mod.level_name_for(PRIORITY_ALERT), "critical")
        self.assertEqual(q_mod.level_name_for(PRIORITY_HIGH), "warning")
        self.assertEqual(q_mod.level_name_for(PRIORITY_NORMAL), "info")
        self.assertEqual(q_mod.level_name_for(5), "info")

    def test_this_batch_ships_no_new_zero_caller_symbol(self):
        """裁定③ 治的病＝「全树引用只有定义行本身」；本批自己新加的符号⛔ 许重演一遍。

        判据按判例给两条合法出路：**接上**（定义文件之外≥1 引用）或**拆掉**（全树 0 引用）。
        尺＝逐文件读文本，⛔ 拿 md5/`id()` 一类代理证据充当。
        真值来源＝`grep -rn "ttl_for_priority\\|level_name_for\\|record_ttl_drop" butler --include=*.py`。
        """
        from butler.tts import queue as q_mod
        repo = Path(q_mod.__file__).parents[2]
        family = {
            "ttl_for_priority": "butler/tts/queue.py",
            "level_name_for": "butler/tts/queue.py",
            "record_ttl_drop": "butler/guard/push_guard.py",
        }
        src = {}
        for p in sorted(repo.glob("butler/**/*.py")):
            src[p.relative_to(repo).as_posix()] = p.read_text(encoding="utf-8-sig", errors="ignore")
        self.assertGreater(len(src), 50, "扫描面塌了＝这把尺量不到东西（分母自证）")
        for name, own in family.items():
            hits = [rel for rel, text in src.items() if re.search(r"\b%s\b" % name, text)]
            if not hits:
                continue                      # 拆掉了：全树 0 引用，不是空号
            callers = [rel for rel in hits if rel != own]
            self.assertTrue(callers, "%s 在 %s 之外零引用＝又一枚空号" % (name, own))

    def test_the_unread_helper_this_batch_made_is_gone(self):
        """本批一度造出第二枚空号＝`ttl_for_priority`（生产走 `config.ttl_for()`，它 0 读者）。

        处置＝拆（`scripts/audit_1002/patch_dcd3_del_zerocaller_1002.py`）。这条腿是给下一次
        「顺手加个便利函数」准备的闸：⛔ 加回来而没人接＝本腿红。
        红跑证据＝上一格在未拆的树上是 `FAILED (failures=1)`（`workorders/readings/1002b11/red_zerocaller_leg.txt`）；
        本腿对同一棵树（HEAD `0f8b8fb`）的红＝`workorders/readings/1002b11/red_gone_leg_vs_head.txt`。
        """
        from butler.tts import queue as q_mod
        self.assertFalse(hasattr(q_mod, "ttl_for_priority"),
                         "符号还在＝我本批造的空号没拆；要么接上要么删，⛔ 留着当样子货")
        text = Path(q_mod.__file__).read_text(encoding="utf-8-sig")
        self.assertNotIn("ttl_for_priority", text, "定义删了但注释/文档串里还留着名字＝半途拆")


class SingletonWiringTest(unittest.TestCase):
    """现网只建一次队列（`singleton.init_queue`）⇒ 审计腿必须在那里接上，且晚绑定（裁定②同款）。"""

    def setUp(self):
        self._saved_queue = singleton._queue
        singleton._queue = None
        self._saved_pg = getattr(_RT, "push_guard", _MISSING)

    def tearDown(self):
        singleton._queue = self._saved_queue
        if self._saved_pg is _MISSING:
            try:
                del _RT.push_guard
            except AttributeError:
                pass
        else:
            _RT.push_guard = self._saved_pg

    def test_init_queue_installs_the_expiry_audit_hook(self):
        class _Mgr:
            ha = None

        q = singleton.init_queue(_Mgr())
        self.assertIsNotNone(q.on_expired, "默认审计腿要在建队列那一刻接上，否则现网还是只剩计数")

    def test_default_hook_reaches_the_live_push_guard(self):
        """晚绑定：`rt.push_guard` 在 `app.py:450` 才挂上，比队列晚 ⇒ 判定只能每次现取。"""
        calls: list[dict] = []

        class _StubGuard:
            def record_ttl_drop(self, **kw):
                calls.append(kw)

        _RT.push_guard = _StubGuard()
        item = TTSItem(text="旧提醒", priority=PRIORITY_NORMAL, trace_id="tr-1", job_id="j-1")
        item.created_at = 1_700_000_000.0
        item.expires_at = item.created_at + 300
        singleton._audit_expiry_to_guard(item, 1_700_000_301.0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["priority"], "info")
        self.assertEqual(calls[0]["trace_id"], "tr-1")
        self.assertAlmostEqual(calls[0]["age_s"], 301.0, places=3)

    def test_default_hook_is_fail_open_without_a_guard(self):
        try:
            del _RT.push_guard
        except AttributeError:
            pass
        item = TTSItem(text="旧提醒", priority=PRIORITY_ALERT)
        singleton._audit_expiry_to_guard(item, time.time())  # ⛔ 抛异常


if __name__ == "__main__":
    unittest.main(verbosity=2)
