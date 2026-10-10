"""批175 判据门 · 模式关联动作把 `entity_id` 递进 HA 请求体（P1-6 / P1-47 / B-N6）。

对应报告：`doc/审计报告/归档/doubao-butler-第十七轮审计报告.md:92`（P1-47）
与 `doc/审计报告/归档/doubao-butler-最终审计报告.md:91`（同一条编号 P1-6，形制不同名）。

现读（2026-10-10T17:33Z，镜像面）：
  · `butler/integrations/ha.py:233` 的签名是 `call_service(self, domain, service, data=None)`
    —— **没有 `entity_id` 这个形参**；`:242` 的发出的 JSON body 就是 `data or {}`。
  · `butler/modes/engine.py:221-226` 却按 `entity_id=action.get("entity_id")` 传关键字
    ⇒ 每次调用 `TypeError: call_service() got an unexpected keyword argument 'entity_id'`；
  · 该异常被 `:230-232` 的内层 `except` 以 **`logger.debug`** 吞掉 ⇒ 生产日志级别下完全不可见。

⚠ 本门测的是**潜伏腿**，不是现网现象：`MODE_ACTIONS`（`engine.py:97` 起）里 `movie`／`sleep`／
`away` 的动作**全部是注释**，`guest`／`daily` 是空表，`_apply_mode_actions` 在 `:213-214`
`if not actions: return` 提前退出 ⇒ 今天一条也不会执行。所以本门全绿**不等于**现网行为有任何变化，
它钉的是「谁把注释填回来那天，动作必须真的发得出去、发不出去必须看得见」。

两条反控形制，缺一门就假：
1. 断言 body **含** `entity_id`（`light.living_room_main`），同时断言无 `entity_id` 的动作
   body **不含**该键也**不含 null 占位** ⇒「无论有没有都塞一个 entity_id: None」过不了这扇门。
2. 桩件 `call_service` 签名与 `ha.py:233` **逐字对齐**（三枚形参，无 `entity_id`）⇒ 改前的
   真实代码在这里就是 `TypeError`，且被 `logger.debug` 吞掉 ⇒ 断言"HA 收到一次调用"直接落空。
   ⛔ 不许把桩件签名改成能吃 `entity_id` —— 那等于把被测码拗成门的形状。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from butler.modes import engine as modes_engine
from butler.modes.engine import ModeEngine


class _StubHA:
    """签名逐字对齐 `butler/integrations/ha.py:233`（⛔ 没有 entity_id 形参）。"""

    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._raise_exc = raise_exc

    async def call_service(self, domain: str, service: str, data: dict | None = None) -> str:
        if self._raise_exc is not None:
            raise self._raise_exc
        self.calls.append({"domain": domain, "service": service, "data": data})
        return "ok (200)"


class _StubRT:
    def __init__(self, ha: _StubHA) -> None:
        self.ha = ha


def _drive(engine: ModeEngine, new_mode: str, old_mode: str = "") -> None:
    """`_apply_mode_actions` 只 `ensure_future`，所以需要一圈跑动的循环＋让出。"""
    async def _run():
        engine._apply_mode_actions(new_mode, old_mode)
        for _ in range(20):
            await asyncio.sleep(0)
    asyncio.run(_run())


class Batch175ModeActionHaBodyTests(unittest.TestCase):

    def _engine_with(self, ha: _StubHA) -> ModeEngine:
        eng = ModeEngine.__new__(ModeEngine)  # 绕开 __init__ 的 _load_current()（要开时序库）
        eng.rt = _StubRT(ha)
        return eng

    def _patch_actions(self, mode: str, actions: list[dict]):
        return mock.patch.dict(modes_engine.MODE_ACTIONS, {mode: actions}, clear=False)

    def test_entity_id_reaches_ha_request_body(self):
        ha = _StubHA()
        action = {"domain": "light", "service": "turn_on",
                  "entity_id": "light.living_room_main", "data": {"brightness": 80}}
        with self._patch_actions("movie", [action]):
            _drive(self._engine_with(ha), "movie")
        self.assertEqual(len(ha.calls), 1, "HA 应当收到一次调用（收到 0 次＝调用侧抛了 TypeError）")
        body = ha.calls[0]["data"] or {}
        self.assertEqual(body.get("entity_id"), "light.living_room_main",
                         f"entity_id 必须进 body，实读 body={body!r}")
        self.assertEqual(body.get("brightness"), 80, "原有 data 键⛔ 被折叠动作吃掉")

    def test_action_without_entity_id_does_not_inject_null_key(self):
        ha = _StubHA()
        action = {"domain": "climate", "service": "turn_off", "data": {"preset_mode": "away"}}
        with self._patch_actions("away", [action]):
            _drive(self._engine_with(ha), "away")
        self.assertEqual(len(ha.calls), 1)
        body = ha.calls[0]["data"] or {}
        self.assertNotIn("entity_id", body,
                         "没有 entity_id 的动作⛔ 塞 null 占位（HA 会把 None 当实体名读）")
        self.assertEqual(body, {"preset_mode": "away"})

    def test_action_with_entity_id_and_no_data_still_sends_a_body(self):
        ha = _StubHA()
        action = {"domain": "light", "service": "turn_off", "entity_id": "light.living_room"}
        with self._patch_actions("sleep", [action]):
            _drive(self._engine_with(ha), "sleep")
        self.assertEqual(len(ha.calls), 1)
        body = ha.calls[0]["data"] or {}
        self.assertEqual(body, {"entity_id": "light.living_room"})

    def test_domain_and_service_stay_positional_arguments(self):
        ha = _StubHA()
        action = {"domain": "fan", "service": "turn_on", "entity_id": "fan.bedroom"}
        with self._patch_actions("movie", [action]):
            _drive(self._engine_with(ha), "movie")
        self.assertEqual(len(ha.calls), 1)
        self.assertEqual(ha.calls[0]["domain"], "fan")
        self.assertEqual(ha.calls[0]["service"], "turn_on")

    def test_mode_action_failure_is_logged_at_warning_not_debug(self):
        # 改前：TypeError 走 logger.debug ⇒ 生产级别看不见 ⇒ 本门 assertLogs(WARNING) 落空。
        eng = self._engine_with(_StubHA())
        action = {"domain": "light", "service": "turn_on",
                  "entity_id": "light.kitchen", "data": None}
        broken_ha = _StubHA(raise_exc=RuntimeError("ha unreachable"))
        eng.rt = _StubRT(broken_ha)
        with self._patch_actions("movie", [action]):
            with self.assertLogs("butler.modes.engine", level="WARNING") as captured:
                _drive(eng, "movie")
        joined = "\n".join(captured.output)
        self.assertIn("light.turn_on", joined,
                      f"WARNING 日志里要能认出是哪枚动作失败，实读={joined!r}")
        self.assertEqual(broken_ha.calls, [], "失败桩件不该记下任何调用")

    def test_empty_actions_for_mode_is_a_noop(self):
        ha = _StubHA()
        with self._patch_actions("guest", []):
            _drive(self._engine_with(ha), "guest")
        self.assertEqual(ha.calls, [], "空动作表应当直接返回，不产生任何 HA 调用")

    def test_missing_rt_or_missing_ha_attribute_is_a_noop(self):
        ha = _StubHA()
        eng = ModeEngine.__new__(ModeEngine)
        eng.rt = None
        with self._patch_actions("movie", [{"domain": "light", "service": "turn_on",
                                            "entity_id": "light.x"}]):
            _drive(eng, "movie")
        self.assertEqual(ha.calls, [], "rt=None 时不该有任何动作执行")

        class _NoHA:
            pass
        eng2 = ModeEngine.__new__(ModeEngine)
        eng2.rt = _NoHA()
        with self._patch_actions("movie", [{"domain": "light", "service": "turn_on",
                                            "entity_id": "light.x"}]):
            _drive(eng2, "movie")  # 不该抛
        self.assertEqual(ha.calls, [])


if __name__ == "__main__":
    unittest.main()
