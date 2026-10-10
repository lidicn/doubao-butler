"""审计 2026-10-10 批165：`event_stream` 把 `old_state` 当 dict 用——现网每天崩 277 次的那条腿。

现网证据（2026-10-10 14:03:54Z 单点读数，`docker logs --since 24h doubao-butler`，只取计数）：

```
log_lines=17880
AttributeError 行 277 次；traceback 末帧固定在
  File "/app/butler/core/event_stream.py", line 223, in _handle_event
    old_s = (old_state.get("state") or "").strip()
AttributeError: 'str' object has no attribute 'get'
伴随形制：WARNING … handle_event 抛错（该事件丢弃，订阅不断）（抑制 368 条）
```

为什么这条不在 D 名册里也不该等名册：批45 修的是**同一函数**的 `origin` 形状腿（见
`tests/test_audit_1006_batch45_event_stream_origin.py`），当时崩点就写在 :223 前面那几行；
`old_state`／`new_state` 两枚载荷形状⛔ 有 guard。后果不是"日志吵"：

1. 抛出即被 `_connect` 的 `except Exception: continue` 吞掉 ⇒ **该次状态变化一行都没落 `home_events`**
   （落库在崩点之后），MA 凌晨拉到的窗口就此缺页；
2. `（抑制 368 条）` 说明单窗内被吞的是**几百条**，⛔ "偶发一帧"。

修法（本批口径）：
- 形状不符契约的载荷⛔ 当成"没有变化"：`old_state` 不是 dict ⇒ 按「无法证明未变化」**放行该次变化**
  （`new_state` 自己是完好的，事件内容有效）；
- `new_state` 不是 dict ⇒ 这条没法处理，丢弃**且必须留痕**（⛔ 静默 return）；
- 两腿都要 `warn_throttled` 出形制名（`type(...)` 与 `entity_id`），⛔ 把状态值本身打进日志
  ——`sensor.xiaomi_*` 的 state 就是用户说话原文。

自报盲点：
- 本门⛔ 声称测到"现网那条生产者到底是谁"：`state_changed` 的 `old_state` 按 HA 契约只会是对象或 null，
  打出字符串的是仓外的投递方（NR／AF／自定义 fire），我没有它的载荷证据；本批只保证
  **"来了畸形帧不再吞事件"**，⛔ 拿它当"上游已修"。
- 假件只挡 DB 边界（`get_conn`），断言的是崩点与落库的**先后顺序**；真落库那条腿归批45。
- 改在盘上⛔ 等于生效：现网跑的是权威树＋容器里那份（行号与镜像面差 5 行），要过 rebase＋重建窗。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]

ENTITY = "light.ke_ting"
# 形制腿专用实体：state 故意写成一句话，用来证明日志⛔ 把原文带出去
EAR_ENTITY = "sensor.xiaomi_living_conversation"
UTTERANCE = "把客厅灯关掉顺便讲个笑话"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _import_es():
    try:
        import butler.core.event_stream as es
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest("宿主面缺依赖（%s）⇒ 运行时腿只在容器内跑" % type(exc).__name__)
    return es


class _Conn:
    """DB 边界假件：只记 INSERT 的形状与条数。"""

    def __init__(self):
        self.rows = []

    def execute(self, sql, params=None):
        self.rows.append((sql, params))

    def commit(self):
        pass


class _Harness(unittest.TestCase):
    def setUp(self):
        self.es = _import_es()
        self.conn = _Conn()
        p = mock.patch.object(self.es, "get_conn", lambda: self.conn)
        p.start()
        self.addCleanup(p.stop)
        # 节流态挂在 logging_setup 上且是模块级的：⛔ 让上一门的第二条被这一门的窗口吃掉
        import butler.logging_setup as ls
        p2 = mock.patch.dict(ls._throttle_state, {}, clear=True)
        p2.start()
        self.addCleanup(p2.stop)
        self.s = self.es.EventStream(None)

    def _event(self, entity_id=ENTITY, new="on", old="off"):
        return {
            "event_type": "state_changed",
            "origin": "LOCAL",
            "data": {
                "entity_id": entity_id,
                "old_state": {"entity_id": entity_id, "state": old, "attributes": {}},
                "new_state": {"entity_id": entity_id, "state": new,
                              "attributes": {"friendly_name": "客厅灯"}},
            },
        }


class OldStateStringLeg(_Harness):
    """真缺陷腿：`old_state` 是字符串 ⇒ 不再抛、事件照常进窗口并落库。"""

    def test_str_old_state_does_not_raise(self):
        ev = self._event()
        ev["data"]["old_state"] = "off"          # 投递方把 old_state 打成了裸字符串
        _run(self.s._handle_event(ev))           # 改前：AttributeError 直接从这里抛出
        self.assertIn(ENTITY, self.s.get_current_state())

    def test_str_old_state_still_reaches_the_persist_leg(self):
        ev = self._event(new="on", old=None)
        ev["data"]["old_state"] = "off"
        _run(self.s._handle_event(ev))
        recent = self.s.get_recent(600)
        self.assertEqual(1, len(recent), "滑动窗口没收到：%s" % recent)
        self.assertEqual("on", recent[0]["state"])
        self.assertEqual(1, len(self.conn.rows),
                         "崩点在落库之前＝该次变化一行没写；修好后必须落 1 行")

    def test_str_old_state_logs_shape_but_not_the_state_text(self):
        ev = self._event(entity_id=EAR_ENTITY, new=UTTERANCE)
        ev["data"]["old_state"] = ""
        with self.assertLogs("butler.core.event_stream", level="WARNING") as cap:
            _run(self.s._handle_event(ev))
        text = "\n".join(cap.output)
        self.assertIn("old_state", text, "形状腿要留痕，⛔ 换个名字都查不到")
        self.assertIn(EAR_ENTITY, text)
        self.assertNotIn(UTTERANCE, text, "⛔ 把用户说话原文打进日志")


class OldStateNoneLeg(_Harness):
    """`old_state` 为 null 是 HA 合法形状（首次见到该实体）：照常算"有变化"。"""

    def test_none_old_state_counts_as_change(self):
        ev = self._event()
        ev["data"]["old_state"] = None
        _run(self.s._handle_event(ev))
        self.assertEqual(1, len(self.s.get_recent(600)))


class NewStateStringLeg(_Harness):
    """`new_state` 是字符串＝这条事件没有可用载荷：丢弃，但⛔ 静默。"""

    def test_str_new_state_drops_with_warning(self):
        ev = self._event()
        ev["data"]["new_state"] = "on"
        with self.assertLogs("butler.core.event_stream", level="WARNING") as cap:
            _run(self.s._handle_event(ev))
        self.assertEqual(0, len(self.s.get_recent(600)), "载荷不可用却进了窗口")
        self.assertEqual(0, len(self.conn.rows), "载荷不可用却落了库")
        self.assertIn("new_state", "\n".join(cap.output))


class UnchangedAndNormalControlsLeg(_Harness):
    """正控：修完⛔ 顺手把「状态没变」也放进来（现网 3,991 次/24h 的定时器抖动会淹掉窗口）。"""

    def test_identical_dict_states_are_still_skipped(self):
        _run(self.s._handle_event(self._event(new="on", old="on")))
        self.assertEqual(0, len(self.s.get_recent(600)))
        self.assertEqual(0, len(self.conn.rows))

    def test_normal_dict_change_lands_without_warning(self):
        # 打的是 es 侧的绑定（es 用 from…import 拿到 warn_throttled，改 logging_setup 上的名字拦不住）
        with mock.patch.object(self.es, "warn_throttled") as wt:
            _run(self.s._handle_event(self._event(new="on", old="off")))
            wt.assert_not_called()
        self.assertEqual(1, len(self.s.get_recent(600)))
        self.assertEqual(1, len(self.conn.rows))


class ShapeRulerOnHandleEvent(unittest.TestCase):
    """形状尺：`_handle_event` 里两枚载荷都必须先过 isinstance 才准 `.get`。

    合成负控证明这把尺会咬（⛔ 只跑真文件——恒绿与恒红都不改变真文件读数）。
    """

    NEEDLES = ("isinstance(new_state, dict)", "isinstance(old_state, dict)")

    def _body(self):
        src = (ROOT / "butler" / "core" / "event_stream.py").read_text(encoding="utf-8")
        start = src.index("async def _handle_event")
        end = src.index("\n    def ", start + 10)
        return src[start:end]

    def test_both_payload_legs_are_guarded(self):
        body = self._body()
        for n in self.NEEDLES:
            self.assertIn(n, body, "形状 guard 缺失：%s" % n)

    def test_ruler_bites_on_unguarded_synthetic(self):
        synthetic = ("    async def _handle_event(self, event):\n"
                     "        old_state = data.get('old_state') or {}\n"
                     "        old_s = (old_state.get('state') or '')\n")
        for n in self.NEEDLES:
            self.assertNotIn(n, synthetic, "尺子失灵的合成件它也放行⇒ 本尺无价值")


if __name__ == "__main__":
    unittest.main()
