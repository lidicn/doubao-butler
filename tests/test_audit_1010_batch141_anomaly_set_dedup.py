"""批141 验收（先红）：持续异常去重钥匙＝异常集合快照（DCD 20261008-DB五件 件二 甲）。

旧机制：DEDUP_WINDOW=3600 时间窗 + 小时粒度键 ⇒ 同一异常每小时换话重推
（现证：近 28 天异常类推送 397 条、387/397 正文互不相同、84/105 相邻间隔落 1–2h 桶）。
甲口径：
  - 新异常出现推一条；恢复正常推一条；持续期间不再重复；
  - 恶化＝同键严重度升档，算状态变化、可再推（正文措辞变化⛔ 触发重推）；
  - 窗口型规则（no_activity 只在 9–11 点检测窗内存在）窗闭⛔ 算「恢复正常」
    ⇒ recoverable=False 的键消失不生成恢复条；
  - 安全类（ALERT/critical）首现照推——本批改的是「重推节奏」，⛔ 压首推。

本节口径覆盖 `test_audit_1009_batch85_anomaly_dedup_prune.py`：时间窗机制已退役，
那份测腿随批作废；其「无界增长」关切由本文件
test_active_state_prunes_cleared_keys 承接（集合快照天然有界，随清随删）。
"""
import unittest
from unittest import mock

from butler.timeseries import anomaly as anom
from butler.timeseries.anomaly import AnomalyEvent, AnomalyDetector


def ev(key, severity="warning", message="正文", recoverable=True):
    return AnomalyEvent(key=key, entity_id=f"sensor.{key}", anomaly_type="abnormal",
                        severity=severity, message=message, recoverable=recoverable)


class ShapeRuler(unittest.TestCase):
    def setUp(self):
        with open(anom.__file__, encoding="utf-8") as f:
            self.src = f.read()

    def test_time_window_mechanism_retired(self):
        # 甲：去重钥匙是状态快照，⛔ 时间窗——旧常量/旧缓存必须从模块 API 退役
        self.assertFalse(hasattr(anom, "DEDUP_WINDOW"))
        self.assertFalse(hasattr(anom, "_dedup_cache"))

    def test_mode_anomaly_key_has_no_hour_suffix(self):
        # 小时粒度键跨钟点换钥匙 ⇒ 老钥匙查不到，是逐小时重推的三凑之一
        self.assertNotIn("%Y%m%d%H", self.src)

    def test_window_rule_marks_itself_unrecoverable(self):
        # 到点无活动＝窗口规则，窗闭不是恢复
        self.assertIn("recoverable=False", self.src)


class SetDedupTransitionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = dict(getattr(anom, "_active_events", {}))
        getattr(anom, "_active_events", {}).clear()
        self.summaries = []
        self.recoveries = []

    def tearDown(self):
        if hasattr(anom, "_active_events"):
            anom._active_events.clear()
            anom._active_events.update(self._saved)

    def _cycle(self, no_activity=(), appliance=(), critical=(), mode=()):
        det = AnomalyDetector(rt=None)
        async def _mk(lst):
            return lst
        det._check_no_activity = lambda: _mk(list(no_activity))
        det._check_appliance_timeout = lambda: _mk(list(appliance))
        det._check_critical_offline = lambda: _mk(list(critical))
        det._check_mode_anomaly = lambda: _mk(list(mode))

        async def fake_summary(events):
            self.summaries.append(list(events))

        async def fake_recovery(events):
            self.recoveries.append(list(events))

        det._push_anomaly_summary = fake_summary
        det._push_recovery = fake_recovery
        return det

    async def _run(self, **kw):
        det = self._cycle(**kw)
        with mock.patch.object(anom.ts, "record_device_anomaly") as rec:
            returned = await det.check_all()
        return returned, rec

    async def test_new_anomaly_pushes_once(self):
        a, b = ev("A"), ev("B")
        returned, rec = await self._run(appliance=[a, b])
        self.assertEqual(len(self.summaries), 1)
        self.assertEqual(len(self.recoveries), 0)
        self.assertEqual({e.key for e in self.summaries[0]}, {"A", "B"})
        self.assertEqual(rec.call_count, 2)

    async def test_persistent_anomaly_with_changed_text_does_not_repush(self):
        # 甲的心脏格：正文里「已运行 N 分钟」每分钟在变，⛔ 再被当成新异常
        await self._run(appliance=[ev("A", message="已运行 10 分钟")])
        self.summaries.clear()
        returned, rec = await self._run(appliance=[ev("A", message="已运行 70 分钟")])
        self.assertEqual(self.summaries, [])
        self.assertEqual(self.recoveries, [])
        self.assertEqual(returned, [])
        self.assertEqual(rec.call_count, 0)

    async def test_recovered_anomaly_pushes_recovery_once_only(self):
        await self._run(appliance=[ev("A")])
        self.summaries.clear()
        await self._run(appliance=[])          # 恢复 ⇒ 恰好一条恢复推
        self.assertEqual(len(self.recoveries), 1)
        self.assertEqual({e.key for e in self.recoveries[0]}, {"A"})
        self.summaries.clear()
        self.recoveries.clear()
        await self._run(appliance=[])          # 恢复后继续正常 ⇒ ⛔ 再推
        self.assertEqual(self.recoveries, [])

    async def test_severity_escalation_counts_as_state_change(self):
        # 「恶化时再推」走状态语义：同键升档可重推，降档/持平不推
        await self._run(appliance=[ev("A", severity="warning")])
        self.summaries.clear()
        await self._run(appliance=[ev("A", severity="critical")])
        self.assertEqual(len(self.summaries), 1)
        self.assertEqual(self.summaries[0][0].severity, "critical")
        self.summaries.clear()
        await self._run(appliance=[ev("A", severity="warning")])  # 降档不重推
        self.assertEqual(self.summaries, [])

    async def test_window_rule_end_is_not_recovery(self):
        na = ev("no_activity_lidicn_20261010", recoverable=False)
        await self._run(no_activity=[na])
        self.summaries.clear()
        await self._run(no_activity=[])        # 11:00 窗闭 ⇒ 无恢复条
        self.assertEqual(self.recoveries, [])

    async def test_new_key_while_others_persist_pushes_only_the_new(self):
        await self._run(appliance=[ev("A")])
        self.summaries.clear()
        await self._run(appliance=[ev("A"), ev("B")])
        self.assertEqual(len(self.summaries), 1)
        self.assertEqual({e.key for e in self.summaries[0]}, {"B"})

    async def test_active_state_prunes_cleared_keys(self):
        # 批85「无界增长」关切的承接腿：键随消失即出账，这张 dict 有界
        await self._run(appliance=[ev("A"), ev("B")])
        await self._run(appliance=[ev("A")])
        self.assertIn("A", anom._active_events)
        self.assertNotIn("B", anom._active_events)


class PushContentTests(unittest.IsolatedAsyncioTestCase):
    class _FakeBark:
        def __init__(self):
            self.calls = []

        async def push(self, **kw):
            self.calls.append(kw)

    def setUp(self):
        self._saved = dict(getattr(anom, "_active_events", {}))
        getattr(anom, "_active_events", {}).clear()
        self.bark = self._FakeBark()
        import butler.runtime as rt_mod
        self._rt_patch = mock.patch.object(rt_mod, "get_runtime",
                                           return_value=mock.Mock(bark=self.bark, tts=None))
        self._rt_patch.start()

    def tearDown(self):
        self._rt_patch.stop()
        if hasattr(anom, "_active_events"):
            anom._active_events.clear()
            anom._active_events.update(self._saved)

    def _cycle(self, appliance=()):
        det = AnomalyDetector(rt=None)
        async def _mk(lst):
            return lst
        det._check_no_activity = lambda: _mk([])
        det._check_appliance_timeout = lambda: _mk(list(appliance))
        det._check_critical_offline = lambda: _mk([])
        det._check_mode_anomaly = lambda: _mk([])
        return det

    async def test_critical_first_push_keeps_critical_priority(self):
        # ALERT 不压：critical 首现照常推，priority 仍是 critical
        det = self._cycle(appliance=[ev("router_offline", severity="critical", message="路由器掉线")])
        with mock.patch.object(anom.ts, "record_device_anomaly"):
            await det.check_all()
        self.assertEqual(len(self.bark.calls), 1)
        self.assertEqual(self.bark.calls[0]["priority"], "critical")
        self.assertEqual(self.bark.calls[0]["group"], "anomaly")

    async def test_recovery_push_shape(self):
        det = self._cycle(appliance=[ev("A")])
        with mock.patch.object(anom.ts, "record_device_anomaly"):
            await det.check_all()
        self.bark.calls.clear()
        det2 = self._cycle(appliance=[])
        with mock.patch.object(anom.ts, "record_device_anomaly"):
            await det2.check_all()
        self.assertEqual(len(self.bark.calls), 1)
        call = self.bark.calls[0]
        self.assertIn("恢复", call["title"])
        self.assertEqual(call["group"], "anomaly")
        self.assertNotEqual(call["priority"], "critical")  # 恢复不是警报


if __name__ == "__main__":
    unittest.main()
