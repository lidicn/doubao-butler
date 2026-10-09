"""批138（DCD 20261006 计划 · homesdk 落地细案 模块5）：presence 发布腿收编 homesdk.presence.advertise。

判据两面：
  (甲) 形状尺——butler/bus/mqtt_client.py 里 ⛔ 再出现手写 will_set(ADM_STATUS…)、
      自拼 offline 载荷（adm_status_json）、绕过库的 _publish_raw 腿；
      在线/下线/LWT 三条腿统一走 homesdk.presence.advertise。尺子配负控。
  (乙) 行为——
      * LWT 在 **start()（CONNECT 之前）** 注册：paho 只在 CONNECT 报文里带遗嘱，
        构造期设与连接后设都不如"发起连接的那一刻"语义清楚；未 start 不得有发布副作用。
      * status.version == caps.version == 计划号（契约 v2.0 §A：DB=2.7，⛔ 包 __version__=1.0.0），
        两腿同一常量单点。
      * 成员名过 presence 的 ADM_MEMBERS 白名单（拼错主题＝ UnknownMember 当场抛）。

前置：homesdk==0.3.2。
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import butler.bus.mqtt_client as mc_mod
from butler.bus.mqtt_client import MQTTClient
from butler.bus.topics import ADM_CAPS, ADM_STATUS
from homesdk.adm.status import decode_status
from homesdk.presence import ADM_MEMBERS

REPO_ROOT = mc_mod.__file__
SRC = open(REPO_ROOT, encoding="utf-8").read()


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.will = None
        self.published = []
        self.connect_calls = []

    def will_set(self, topic, payload, qos=1, retain=False):
        self.calls.append("will_set")
        self.will = {"topic": topic, "payload": payload, "qos": qos, "retain": retain}

    def publish(self, topic, payload, qos=1, retain=False):
        self.calls.append("publish")
        self.published.append({"topic": topic, "payload": payload,
                               "qos": qos, "retain": retain})

    def connect_async(self, *args, **kwargs):
        self.calls.append("connect_async")
        self.connect_calls.append((args, kwargs))

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def is_connected(self):
        return True


def _settings_stub() -> SimpleNamespace:
    return SimpleNamespace(mqtt_client_id="test-butler")


class _Fixture(unittest.TestCase):
    """共同夹具：get_client/resolve_credentials/broker_settings 三腿替身，构造不碰 env。"""

    def setUp(self):
        self.fake = _FakeClient()
        self._patchers = [
            mock.patch.object(mc_mod, "get_client", lambda *a, **k: self.fake),
            mock.patch.object(mc_mod, "resolve_credentials", lambda *a, **k: ("u", "p")),
            mock.patch.object(mc_mod, "broker_settings",
                              lambda: ("h.example", 1883, 60)),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)
        self.loop = mock.MagicMock()
        self.mc = MQTTClient(_settings_stub(), self.loop)


class ShapeRuler(_Fixture):
    FORBIDDEN = ("will_set(", "_publish_raw", "adm_status_json", "encode_status(")
    REQUIRED = ("from homesdk.presence import", "advertise(")
    OLD_SHAPE_SAMPLES = [
        'self._client.will_set(ADM_STATUS, self.adm_status_json(False), qos=1, retain=True)',
        'return encode_status(STATE_ONLINE if online else STATE_OFFLINE, version=__version__)',
        'self._publish_raw(ADM_STATUS, self.adm_status_json(True), qos=1, retain=True)',
    ]

    def test_ruler_bites_old_shape(self):
        for i, sample in enumerate(self.OLD_SHAPE_SAMPLES):
            hits = [tok for tok in self.FORBIDDEN if tok in sample]
            self.assertTrue(hits, "负控样本 %d 尺子咬不住：%r" % (i, sample))

    def test_no_handcrafted_presence_legs(self):
        hits = [tok for tok in self.FORBIDDEN if tok in SRC]
        self.assertEqual(hits, [], "mqtt_client.py 残留手写 presence 腿：%s" % hits)

    def test_uses_homesdk_presence(self):
        missing = [tok for tok in self.REQUIRED if tok not in SRC]
        self.assertEqual(missing, [], "presence.advertise 未接上：%s" % missing)

    def test_member_name_whitelisted(self):
        name = getattr(mc_mod, "ADM_MEMBER", None)
        self.assertEqual(name, "doubao-butler",
                         "成员名单点缺失（presence 白名单名，拼错=UnknownMember）")
        self.assertIn(name, ADM_MEMBERS)


class ContractVersion(_Fixture):
    def test_plan_version_constant(self):
        self.assertEqual(getattr(mc_mod, "ADM_PLAN_VERSION", None), "2.7",
                         "契约 v2.0 §A：status/caps 的 version=计划号（DB 2.7），⛔ __version__")

    def test_status_and_caps_versions_match(self):
        self.mc._publish_adm_online()
        sts = [m for m in self.fake.published if m["topic"] == ADM_STATUS]
        caps = [m for m in self.fake.published if m["topic"] == ADM_CAPS]
        self.assertEqual(len(sts), 1, sts)
        self.assertEqual(len(caps), 1, caps)
        obj = decode_status(sts[0]["payload"])
        cap = json.loads(caps[0]["payload"])
        self.assertEqual(obj["state"], "online")
        self.assertEqual(obj["version"], mc_mod.ADM_PLAN_VERSION)
        self.assertEqual(cap["version"], obj["version"],
                         "status.version 与 caps.version 必须同值单点")


class LifecycleLegs(_Fixture):
    def test_construction_has_no_wire_side_effects(self):
        # 未 start ⇒ ⛔ LWT 已设、⛔ 有发布：副作用只能随"发起连接"出现。
        self.assertIsNone(self.fake.will)
        self.assertEqual(self.fake.published, [])

    def test_start_registers_lwt_before_connect(self):
        self.mc.start()
        self.assertIsNotNone(self.fake.will, "start() 未注册 LWT（CONNECT 前最后一刻）")
        self.assertEqual(self.fake.will["topic"], ADM_STATUS)
        self.assertTrue(self.fake.will["retain"])
        obj = decode_status(self.fake.will["payload"])
        self.assertEqual(obj["state"], "offline")
        self.assertEqual(obj["version"], mc_mod.ADM_PLAN_VERSION)
        self.assertLess(self.fake.calls.index("will_set"),
                        self.fake.calls.index("connect_async"),
                        "LWT 必须在 connect 之前注册：paho 只在 CONNECT 报文里带遗嘱")

    def test_stop_publishes_offline_json(self):
        self.mc.stop()
        sts = [m for m in self.fake.published if m["topic"] == ADM_STATUS]
        self.assertEqual(len(sts), 1, sts)
        self.assertTrue(sts[0]["retain"])
        self.assertEqual(decode_status(sts[0]["payload"])["state"], "offline")


if __name__ == "__main__":
    unittest.main()
