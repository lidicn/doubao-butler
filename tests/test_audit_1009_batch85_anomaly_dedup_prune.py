"""批85 验收（先红）：异常去重缓存必须淘汰过期键，⛔ 无界增长。

现网证据（readings/1009_b85/b85_eveningleg6_1009.txt）：异常族近 7 天 105 条推送、
相邻间隔 84/105 落在 1–2h ⇒ 持续异常按 DEDUP_WINDOW=3600 逐小时重推。
每一轮 `_mark_pushed()` 只写不清，而小时粒度键（`mode_anomaly_{user}_{YYYYMMDDHH}`）
每小时换新 ⇒ 这张进程级 dict 只增不减。
"""
import time
import unittest

from butler.timeseries import anomaly as anom
from butler.timeseries.anomaly import AnomalyDetector


class AnomalyDedupPruneTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(anom._dedup_cache)
        anom._dedup_cache.clear()
        self.det = AnomalyDetector(rt=None)

    def tearDown(self):
        anom._dedup_cache.clear()
        anom._dedup_cache.update(self._saved)

    def test_stale_keys_are_pruned_on_mark(self):
        now = time.time()
        for i in range(50):
            anom._dedup_cache[f"stale_{i}"] = now - anom.DEDUP_WINDOW - 1
        self.det._mark_pushed("fresh_key")
        self.assertEqual(len(anom._dedup_cache), 1)

    def test_prune_keeps_keys_inside_window(self):
        now = time.time()
        anom._dedup_cache["in_win"] = now - anom.DEDUP_WINDOW + 5
        for i in range(20):
            anom._dedup_cache[f"out_{i}"] = now - anom.DEDUP_WINDOW - 1
        self.det._mark_pushed("trigger")
        self.assertIn("in_win", anom._dedup_cache)
        self.assertNotIn("out_0", anom._dedup_cache)

    def test_fresh_key_still_suppresses_within_window(self):
        self.det._mark_pushed("k1")
        self.assertTrue(self.det._is_duplicate("k1"))

    def test_key_outside_window_stops_suppressing(self):
        anom._dedup_cache["k2"] = time.time() - anom.DEDUP_WINDOW - 1
        self.assertFalse(self.det._is_duplicate("k2"))


if __name__ == "__main__":
    unittest.main()
