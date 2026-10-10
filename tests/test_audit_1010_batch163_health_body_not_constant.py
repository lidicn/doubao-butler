"""批163（审计 D11「观测语义矛盾」）：`/api/health` 的 body ⛔ 由常量撑着。

第十五轮 still_open 里 D11 的原文：「`/api/health` 的 `online` 字段是字面常量 `True`」；
第十六轮给出的修复口径：`online` 改 `all(comps.values())`，**状态码恒 200 保留**
（Dockerfile HEALTHCHECK 只认 200，翻码＝容器 unhealthy，那是台账 §38-6 的裁点，本批⛔ 动）。

盘上现状（本批开工前现读）：`butler/api/system_routes.py:40` 仍是 `return ok({"online": True,`，
旁边只补了 `degraded` 与 `components`——只看 `online` 的消费者照样一片绿，说谎的那条腿没断。

契约依据（`E:/NAS/homesdk/doc/ADM联动主题注册表与消息契约.md` §分级处置）：
「MQTT 断连→health 报 degraded」＝降级+标记，本批把 `online` 接到同一份判据上，⛔ 新造语义。
"""
from __future__ import annotations

import asyncio
import os
import threading
import types
import unittest
from pathlib import Path

REPO = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _runtime(mqtt_up: bool, sched_up: bool, peers: dict | None = None):
    connected = threading.Event()
    if mqtt_up:
        connected.set()
    rt = types.SimpleNamespace()
    rt.mqtt = types.SimpleNamespace(connected=connected)
    rt.scheduler = object() if sched_up else None
    rt._sched = None
    rt.scheduler_job_failures = []
    rt.adm_peers = peers if peers is not None else {}
    return rt


def _call_health(rt) -> tuple[int, dict]:
    import butler.api.system_routes as sr
    old = sr.get_runtime
    sr.get_runtime = lambda: rt
    try:
        resp = asyncio.run(sr.health(None))
    finally:
        sr.get_runtime = old
    import json
    return resp.status_code, json.loads(bytes(resp.body).decode("utf-8"))


class HealthBodyReflectsComponents(unittest.TestCase):
    """行为腿：`online` 必须跟着 `_core_components` 的现读结果走。"""

    def test_all_components_up_reports_online_true(self):
        code, body = _call_health(_runtime(True, True))
        data = body["data"]
        self.assertTrue(data["online"], "全都在线时 online 该是 True（这条也防我把判据取反）")
        self.assertFalse(data["degraded"])
        self.assertEqual(code, 200)

    def test_mqtt_down_reports_online_false(self):
        code, body = _call_health(_runtime(False, True))
        data = body["data"]
        self.assertFalse(data["online"], "MQTT 断了还说 online=True＝审计 D11 那句谎话")
        self.assertTrue(data["degraded"])
        self.assertEqual(code, 200, "状态码⛔ 跟着翻：HEALTHCHECK 只认 200（台账 §38-6 裁点）")

    def test_scheduler_job_failure_reports_online_false(self):
        rt = _runtime(True, True)
        rt.scheduler_job_failures = ["skill_a"]
        code, body = _call_health(rt)
        self.assertFalse(body["data"]["online"], "V37 那格：作业注册缺腿＝调度器不算活着")
        self.assertEqual(code, 200)

    def test_offline_adm_peer_reports_online_false(self):
        rt = _runtime(True, True, peers={"adm/memory-agent/status": False})
        code, body = _call_health(rt)
        self.assertFalse(body["data"]["online"], "对端离线也是组件离线，⛔ 只写 degraded 不写 online")
        self.assertIn("adm_memory-agent", body["data"]["components"])

    def test_components_roster_still_reported(self):
        code, body = _call_health(_runtime(True, False))
        comps = body["data"]["components"]
        self.assertEqual(comps["scheduler"], False)
        self.assertEqual(comps["mqtt"], True)


class ShapeRulerOnHealthLiteral(unittest.TestCase):
    """形状尺：system_routes.py 里⛔ 再出现"整字面量 online True"。先证它会咬。"""

    NEEDLE = '"online": True'

    def _hits(self, source: str) -> list[int]:
        return [i + 1 for i, ln in enumerate(source.splitlines()) if self.NEEDLE in ln]

    def test_ruler_bites_synthetic_source(self):
        synthetic = "x = 1\nreturn ok({%s,\n})\n" % self.NEEDLE
        self.assertEqual(self._hits(synthetic), [2], "尺子先证明它会咬：合成源码那一行必须命中")

    def test_health_route_has_no_literal_online_true(self):
        src = (REPO / "butler" / "api" / "system_routes.py").read_text(encoding="utf-8")
        self.assertEqual(self._hits(src), [],
                         f"仍在原地写死 {self.NEEDLE}（⛔ 抄 mqtt_client.py 的 presence 载荷口径）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
