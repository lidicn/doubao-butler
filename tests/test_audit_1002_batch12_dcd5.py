"""批12 验收：DCD 裁定⑤-2「过载语义改＝丢最低优先级、保 ALERT/critical、当前这条不吞」。

裁定原文＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md:78`：
  2. **过载语义改**：丢最低优先级、**保 ALERT/critical**、**当前这条不吞**；
被治的现状（`butler/tts/queue.py` 旧 `_q.clear()` 那条腿）：入队后条数 >= 阈值 ⇒ `dropped = len+1`
+ 整队清空 ⇒ 已在队里的告警和当前这条新消息一起被吞，且全家静音 `overload_pause_s`。

本节⛔ 含 ⑤-1/⑤-3（风控那口假队列与两处阈常量并一处）——那半有产品语义岔路，已递决策申请，
见台账 §13。本文件只测队列侧（`butler/tts/queue.py`）一家。

运行时腿按跟办 3（`:108`）＝假时钟＋直接操作队列，⛔ 播声、⛔ 碰活库。
"""
from __future__ import annotations

import re
import time
import unittest
from pathlib import Path

from butler.tts import queue as q_mod
from butler.tts.queue import (
    PRIORITY_ALERT,
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    REASON_DROPPED_PAUSED,
    REASON_QUEUED,
    TTSQueue,
    TTSQueueConfig,
)


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


def _texts(q: TTSQueue) -> list[str]:
    return [it.text for it in q.items()]


def _arm_overload_pause(q: TTSQueue, ft: FakeTime, secs: float = 600.0) -> None:
    """直接把队列置于「过载暂停」态（本批用例要的是暂停期间的待遇，不是怎么进去的）。"""
    q._pause_reason = "overload"
    q._paused_until = ft.t + secs


class OverloadShedTest(unittest.TestCase):
    """⑤-2 的三个「要」：当前这条收、告警档不丢、只丢最低优先档。"""

    def test_overload_accepts_the_current_message(self):
        """旧码在触发那一刻返回 accepted=False——那正是裁定点名的「当前这条被吞」。"""
        q, _ft = _queue(config=TTSQueueConfig(overload_threshold=5))
        for i in range(4):
            self.assertTrue(q.enqueue(f"m{i}").accepted)
        res = q.enqueue("触发过载的这条")
        self.assertTrue(res.accepted, "当前这条不吞（裁定 :78）")
        self.assertIsNotNone(res.item)
        self.assertEqual(res.reason, REASON_QUEUED, "⛔ 为「收下但丢过别人」再造一枚新原因码")

    def test_overload_no_longer_clears_the_queue(self):
        q, _ft = _queue(config=TTSQueueConfig(overload_threshold=5))
        for i in range(4):
            q.enqueue(f"m{i}")
        q.enqueue("第五条")
        self.assertEqual(len(q), 4, "只腾出位置，⛔ 整队清空（旧码这里＝0）")

    def test_shed_takes_the_lowest_band_first(self):
        """丢的是最低优先档（标准带 3/4/5），高优与告警档⛔ 动。"""
        q, _ft = _queue(config=TTSQueueConfig(overload_threshold=5))
        q.enqueue("高优", priority=PRIORITY_HIGH)
        for i in range(3):
            q.enqueue(f"标准{i}", priority=PRIORITY_NORMAL)
        q.enqueue("触发的一条", priority=PRIORITY_NORMAL)
        priorities = [it.priority for it in q.items()]
        self.assertIn(PRIORITY_HIGH, priorities, "被丢的应该是标准带，高优档⛔ 陪绑")
        self.assertEqual(len(q), 4)

    def test_shed_prefers_the_oldest_in_that_band(self):
        """同带内丢最旧的（与裁定③「旧提醒不该补播」同一个方向）。"""
        cfg = TTSQueueConfig(overload_threshold=4, ttl_by_priority={PRIORITY_NORMAL: 3000.0})
        q, ft = _queue(config=cfg)
        q.enqueue("旧1", priority=PRIORITY_NORMAL)
        ft.advance(1)
        q.enqueue("旧2", priority=PRIORITY_NORMAL)
        ft.advance(1)
        q.enqueue("旧3", priority=PRIORITY_NORMAL)
        ft.advance(1)
        q.enqueue("新", priority=PRIORITY_NORMAL)
        self.assertEqual(_texts(q), ["旧2", "旧3", "新"])

    def test_alert_band_is_never_shed(self):
        """队列全是告警档时：没得丢就照收当前这条，⛔ 为了腾位置去丢告警。"""
        q, _ft = _queue(config=TTSQueueConfig(overload_threshold=3))
        q.enqueue("告警1", priority=PRIORITY_ALERT)
        q.enqueue("告警2", priority=PRIORITY_ALERT)
        res = q.enqueue("告警3", priority=PRIORITY_ALERT)
        self.assertTrue(res.accepted)
        self.assertEqual(len(q), 3, "全是告警档⇒宁可越过阈值一条，⛔ 丢告警也⛔ 吞当前")
        self.assertEqual([it.priority for it in q.items()], [1, 1, 1])

    def test_dropped_counter_counts_only_shed_items(self):
        """旧码 `dropped = len+1` 把「当前这条」也记成丢弃⇒计数说谎。"""
        q, _ft = _queue(config=TTSQueueConfig(overload_threshold=5))
        for i in range(4):
            q.enqueue(f"m{i}")
        q.enqueue("第五条")
        self.assertEqual(q.status()["dropped"]["overload"], 1,
                         "阈值 5、队列 4 条⇒只该丢 1 条（旧码记 5）")

    def test_overload_pause_is_not_extended_by_later_overloads(self):
        """暂停只从第一次触发起算；后续过载⛔ 把窗口续期（否则洪水下非告警负载永久饿死）。"""
        q, ft = _queue(config=TTSQueueConfig(overload_threshold=3, overload_pause_s=600.0))
        q.enqueue("告警1", priority=PRIORITY_ALERT)
        q.enqueue("告警2", priority=PRIORITY_ALERT)
        q.enqueue("告警3", priority=PRIORITY_ALERT)      # 触发：全告警档，无项可丢
        st1 = q.status()
        self.assertTrue(st1["paused"])
        self.assertEqual(st1["pause_reason"], "overload")
        self.assertEqual(len(q), 3, "告警档⛔ 被清（旧码在这里整队清空⇒只剩后来那 1 条）")
        self.assertAlmostEqual(st1["pause_remaining_s"], 600.0, places=3)
        ft.advance(100)
        q.enqueue("告警4", priority=PRIORITY_ALERT)      # 再次触发
        self.assertAlmostEqual(q.status()["pause_remaining_s"], 500.0, places=3,
                               msg="剩余时长该继续走，⛔ 被重新 arm 回 600")

    def test_the_wholesale_reset_reason_is_gone(self):
        """`REASON_OVERLOAD_RESET`＝「整队重置」那套语义的名字⇒ 拆（⛔ 留着当样子货）。"""
        self.assertFalse(hasattr(q_mod, "REASON_OVERLOAD_RESET"),
                         "旧原因码还在＝reset 那套语义没拆干净")
        repo = Path(q_mod.__file__).parents[2]
        own = Path(__file__).resolve()
        hits = []
        for p in sorted(list(repo.glob("butler/**/*.py")) + list(repo.glob("tests/**/*.py"))):
            if p.resolve() == own:
                continue          # 本文件正文里就写着这个名字（判据自身），⛔ 把它当残留
            text = p.read_text(encoding="utf-8-sig", errors="ignore")
            if re.search(r"\bREASON_OVERLOAD_RESET\b", text):
                hits.append(p.relative_to(repo).as_posix())
        self.assertEqual(hits, [], "生产或其它测试里还留着这个名字＝半途拆")

    def test_queue_docstring_matches_the_new_overload_semantics(self):
        """探针钉的是那句承诺，⛔ 词（批11 §12-5-1 学的）：模块头写着「清空」就是假承诺。"""
        doc = q_mod.__doc__ or ""
        self.assertNotIn("清空", doc, "文件头还写着过载⇒清空＝与本批行为相反")
        self.assertIn("最低优先级", doc, "改口必须指到真正发生的那侧")
        self.assertNotIn("已清空", (q_mod.TTSQueueConfig().overload_text or ""),
                         "播报文案说「已清空」而行为是丢最低优先＝说了不做")
        self.assertIn("告警", q_mod.TTSQueueConfig().overload_text,
                      "文案要说到告警照播，⛔ 只删不指")


class AlertExemptionTest(unittest.TestCase):
    """⑤-2「保 ALERT/critical」的另一半＝暂停期间告警当场能播（裁定风险确认那句「该响的没响」）。"""

    def test_alert_is_dequeued_during_overload_pause(self):
        q, ft = _queue()
        q.enqueue("火警", priority=PRIORITY_ALERT)
        _arm_overload_pause(q, ft)
        item = q.dequeue()
        self.assertIsNotNone(item, "过载暂停⛔ 挡告警档（旧码这里返回 None＝全家静音期间火警也不响）")
        self.assertEqual(item.priority, PRIORITY_ALERT)

    def test_non_alert_still_waits_during_overload_pause(self):
        q, ft = _queue()
        q.enqueue("普通提醒", priority=PRIORITY_HIGH)
        _arm_overload_pause(q, ft)
        self.assertIsNone(q.dequeue(), "豁免只给告警档，⛔ 顺手扩成全部放行")

    def test_manual_pause_still_blocks_alerts(self):
        """手动 pause 的语义⛔ 被本批改动（不在裁定⑤ 的面上）。"""
        q, _ft = _queue()
        q.enqueue("火警", priority=PRIORITY_ALERT)
        q.pause("manual")
        self.assertIsNone(q.dequeue())

    def test_alert_paused_ttl_is_no_longer_deferred(self):
        """旧码靠「把 TTL 顺延到暂停结束」保住告警不静默过期；⑤ 起它当场能播⇒那条分支的前提没了。"""
        cfg = TTSQueueConfig(overload_threshold=5, ttl_by_priority={PRIORITY_ALERT: 300.0})
        q, ft = _queue(config=cfg)
        _arm_overload_pause(q, ft, 600.0)
        res = q.enqueue("告警", priority=PRIORITY_ALERT)
        self.assertTrue(res.accepted)
        self.assertAlmostEqual(res.item.expires_at - res.item.created_at, 300.0, places=3,
                               msg="⛔ 再顺延到暂停之后——它现在当场就能播")

    def test_new_non_alert_load_is_still_rejected_during_pause(self):
        q, ft = _queue()
        _arm_overload_pause(q, ft)
        res = q.enqueue("暂停期间来的普通提醒")
        self.assertFalse(res.accepted)
        self.assertEqual(res.reason, REASON_DROPPED_PAUSED)


class BatchTwelveHygieneTest(unittest.TestCase):
    """判例回身咬自己（批11 栽过一次）：本批新符号⛔ 许是第二枚空号。

    尺的口径按批11 §12-4 那条判例＝**只有 def 行本身**才算空号；私有助手在自家文件里被
    调用是合法形态，所以这把尺数的是**调用行**，⛔ 数「命中它的文件数」（那样会把
    一个只在队列内被走的私有方法误判成空号＝坏判据，判据要在每个合法选项下都成立）。
    """

    HELPER = "_shed_lowest_for_room"
    OWNER = "butler/tts/queue.py"

    def test_batch12_helper_exists_and_is_actually_called(self):
        repo = Path(q_mod.__file__).parents[2]
        src = {}
        for p in sorted(repo.glob("butler/**/*.py")):
            src[p.relative_to(repo).as_posix()] = p.read_text(encoding="utf-8-sig", errors="ignore")
        self.assertGreater(len(src), 50, "扫描面塌了＝这把尺量不到东西（分母自证）")
        self.assertIn(self.OWNER, src, f"扫不到 {self.OWNER}＝扫描面不对")
        lines = [l.strip() for l in src[self.OWNER].splitlines()
                 if re.search(r"\b%s\b" % self.HELPER, l)]
        self.assertTrue(lines, f"{self.HELPER} 还不存在＝本批的让位逻辑没落地")
        defs = [l for l in lines if l.startswith("def ")]
        self.assertEqual(len(defs), 1, f"{self.HELPER} 定义了 {len(defs)} 次＝第二份定义")
        calls = [l for l in lines if not l.startswith("def ")]
        self.assertTrue(calls, f"{self.HELPER} 只有 def 行＝又一枚空号（批11 §12-4 同款）")

    def test_batch12_ships_no_other_new_zero_caller_symbol(self):
        """本批没有第二枚符号；若有，必须要么被走到、要么根本不在树上。"""
        repo = Path(q_mod.__file__).parents[2]
        names = ("REASON_OVERLOAD_RESET",)
        src = {}
        for p in sorted(repo.glob("butler/**/*.py")):
            src[p.relative_to(repo).as_posix()] = p.read_text(encoding="utf-8-sig", errors="ignore")
        self.assertGreater(len(src), 50, "扫描面塌了＝这把尺量不到东西（分母自证）")
        for name in names:
            hits = [rel for rel, text in src.items()
                    if re.search(r"\b%s\b" % name, text)]
            self.assertEqual(hits, [], f"{name} 还在生产码里＝那套语义没拆（命中文件：{hits}）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
