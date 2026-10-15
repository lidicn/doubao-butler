"""批224 · DB127：`adm/*/status` 的「载荷读不懂」与「对端确实离线」在返回面上同形。

来源＝第五期 33 轮审计汇总报告 DB127（台账 `doc/审计报告分诊台账-20261002.md:8490` 名册行，
`:8525` 记「本批只落了留痕，真修要 tri-state」）。

现网可达的形状（⛔ 盯 raw str 分支——那条在生产里到不了）：`mqtt_client._on_message` 对
`adm/*/status` 先过 `decode_status`，真解不开的在入口处记坏件丢弃，压根走不进 `_consume`；
能走进来的**永远是一枚 dict**。所以同形发生在 dict 分支：

    {"state": "offline"}   邻居真下线          → False
    {"ts": 1}              JSON 合法、无状态字段 → False   ← 载荷缺陷，不是邻居故障

两形都返回 False ⇒ `app.py` 的离线边沿把后者也记成 `ADM_ERR_PEER_OFFLINE`，
归因指着对端，实际是我读不懂。

修法＝判读单点返回四值（online／offline／degraded／unparsable），
`adm_status_is_online()` 由它派生 ⇒ bool 面逐格不变（进 `rt.adm_peers` 的仍是 bool，
`system_routes._core_components()` 的 `bool(online)` 与 health 的 degraded 算式零变化），
只有归因那条日志分家。

命名纪律＝全部 unittest.TestCase 方法，模块级 test_* 为 0（回归门按 AST 分母对账）。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import unittest
from types import SimpleNamespace

import butler.bus.topics as topics
from butler.runtime import Runtime
from butler.bus.topics import SUB_ADM_MA_STATUS
from homesdk.adm.status import STATE_DEGRADED, STATE_OFFLINE, STATE_ONLINE, encode_status

TOPICS_SRC = pathlib.Path(__file__).resolve().parents[1] / "butler" / "bus" / "topics.py"

MISSING = "butler/bus/topics.py 没有 adm_status_state（DB127 未修）"


def _state(payload):
    fn = getattr(topics, "adm_status_state", None)
    if fn is None:
        unittest.TestCase().fail(MISSING)
    return fn(payload)


class TriStateFace(unittest.TestCase):
    """判读单点要给出第四种返回形，且 ⛔ 改动已有两形的归属。"""

    def test_legible_states_keep_their_own_token(self):
        self.assertEqual(_state(encode_status("online", version="2.7")), STATE_ONLINE)
        self.assertEqual(_state(encode_status("offline", version="2.7")), STATE_OFFLINE)
        self.assertEqual(_state(encode_status("degraded", version="2.7")), STATE_DEGRADED)
        self.assertEqual(_state({"state": "online"}), STATE_ONLINE)
        self.assertEqual(_state({"state": "offline"}), STATE_OFFLINE)
        self.assertEqual(_state({"state": "degraded"}), STATE_DEGRADED)
        # 旧字面量（0.3.1 形）与旧 dict 缓存形都还是「读得懂」
        self.assertEqual(_state("online"), STATE_ONLINE)
        self.assertEqual(_state(b"offline"), STATE_OFFLINE)
        self.assertEqual(_state({"online": True}), STATE_ONLINE)
        self.assertEqual(_state({"online": False}), STATE_OFFLINE)

    def test_unreadable_shapes_are_the_fourth_token(self):
        unparsable = getattr(topics, "ADM_STATE_UNPARSABLE", "unparsable")
        for payload in ("", "{not-json", "<<>>", b"", None, 123, ["online"], {},
                        {"ts": 1}, {"state": None}, {"state": "weird"}, {"state": 7}):
            self.assertEqual(_state(payload), unparsable, f"该判「读不懂」的形判错了：{payload!r}")

    def test_legacy_online_field_that_is_not_boolean_is_unparsable(self):
        """旧 dict 缓存形里 `online` 值非法（`"maybe"`）＝我读不懂，⛔ 当成「对端下线」。"""
        unparsable = getattr(topics, "ADM_STATE_UNPARSABLE", "unparsable")
        self.assertEqual(_state({"online": "maybe"}), unparsable)
        self.assertFalse(topics.adm_status_is_online({"online": "maybe"}))

    def test_degraded_counts_as_present(self):
        """降档≠离线（原有语义，⛔ 借 tri-state 顺手翻成离线）。"""
        self.assertTrue(topics.adm_status_is_online({"state": "degraded"}))
        self.assertTrue(topics.adm_status_is_online(encode_status("degraded", version="x")))

    def test_bool_face_is_unchanged_leg_by_leg(self):
        """bool 面逐格回归：改前判在场／离线的每一格，改后同判（health 读的就是这枚 bool）。"""
        present = ["online", b"online", " online ", encode_status("online", version="x"),
                   {"state": "online"}, {"state": "degraded"}, {"online": True}]
        absent = ["offline", b"offline", b" offline \n", encode_status("offline", version="x"),
                  {"state": "offline"}, {"online": False}, {"ts": 1}, "", "{not-json", None, {}]
        for payload in present:
            self.assertTrue(topics.adm_status_is_online(payload), f"在场形被翻成离线：{payload!r}")
        for payload in absent:
            self.assertFalse(topics.adm_status_is_online(payload), f"离线形被翻成在场：{payload!r}")


class SinglePointOfJudgement(unittest.TestCase):
    """两把尺 ⛔ 各写一遍：bool 必须由状态派生，判定仍然只有一个落点。"""

    def _segment(self, name):
        tree = ast.parse(TOPICS_SRC.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(
                    TOPICS_SRC.read_text(encoding="utf-8"), node) or ""
        return ""

    def test_bool_helper_delegates_to_state(self):
        body = self._segment("adm_status_is_online")
        self.assertTrue(body, "找不到 adm_status_is_online（这把尺先失效）")
        self.assertIn("adm_status_state(", body,
                      "bool 面没走状态单点＝两份判定各写一遍，下一轮又会分叉")
        self.assertNotIn("decode_status", body,
                         "bool 里还自己解载荷＝判定没并成一枚，tri-state 只是第三份拷贝")

    def test_state_leg_is_the_only_decoder(self):
        body = self._segment("adm_status_state")
        self.assertTrue(body, MISSING)
        self.assertIn("decode_status", body, "状态腿没挂 homesdk 的 decode_status＝单点跑了")


class ConsumerAttribution(unittest.TestCase):
    """`app.py` 的离线边沿：两形两码，⛔ 把「我读不懂」记成「邻居下线」。"""

    def _run_consume(self, payloads, prefill):
        import butler.app as app_mod

        async def body():
            rt = Runtime()
            rt.adm_peers = {SUB_ADM_MA_STATUS: prefill}
            queue = asyncio.Queue()
            for item in payloads:
                queue.put_nowait((SUB_ADM_MA_STATUS, item))
            rt.mqtt = SimpleNamespace(queue=queue)
            task = asyncio.ensure_future(app_mod._consume(rt))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return rt

        return asyncio.run(body())

    def test_genuine_offline_edge_still_logs_peer_offline(self):
        """护栏（改前改后都该绿）：真下线的那条不许被这次归因改造弄丢错号。"""
        with self.assertLogs("butler.app", level="INFO") as cm:
            self._run_consume([{"state": "offline"}], prefill=True)
        joined = "\n".join(cm.output)
        self.assertIn("ADM_ERR_PEER_OFFLINE", joined)
        self.assertNotIn("ADM_ERR_PAYLOAD_INVALID", joined)

    def test_unreadable_payload_logs_payload_invalid(self):
        """DB127 的正腿：合法 JSON、无状态字段 ⇒ 载荷缺陷，⛔ 报邻居离线。"""
        with self.assertLogs("butler.app", level="INFO") as cm:
            self._run_consume([{"ts": 1}], prefill=True)
        joined = "\n".join(cm.output)
        self.assertIn("ADM_ERR_PAYLOAD_INVALID", joined,
                      "读不懂的载荷仍被记成 PEER_OFFLINE＝归因指着对端，实际在我这边")
        self.assertNotIn("ADM_ERR_PEER_OFFLINE", joined,
                         "载荷缺陷被报成邻居下线（DB127 未修）")

    def test_peer_cache_stays_a_bool(self):
        """`rt.adm_peers` 存 bool：把状态串塞进去，`bool("offline")` 是 True＝health 假绿。"""
        for payload in ({"state": "offline"}, {"ts": 1}):
            rt = self._run_consume([payload], prefill=True)
            self.assertIs(rt.adm_peers[SUB_ADM_MA_STATUS], False, f"{payload!r} 存进去的不是 bool")

    def test_online_peer_is_not_flagged(self):
        """护栏：在场载荷不该留任何错误痕迹。"""
        rt = self._run_consume([{"state": "online"}], prefill=True)
        self.assertIs(rt.adm_peers[SUB_ADM_MA_STATUS], True)

    def test_unreadable_is_logged_without_an_edge(self):
        """读不懂 ≠ 只在 online→offline 边沿才有声：首次到货就该有痕迹（⛔ 静默降级）。"""
        with self.assertLogs("butler.app", level="INFO") as cm:
            self._run_consume([{"ts": 1}], prefill=False)
        self.assertIn("ADM_ERR_PAYLOAD_INVALID", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
