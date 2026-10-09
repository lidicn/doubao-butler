"""homesdk 0.3.2 落地验收（规格 §二-2.1/2.2 + §三-1/2）。

覆盖四件：
  (a) adm status 发布腿是 encode_status JSON，含 state/ts/degraded/reasons/version 五键；
  (b) decode_status / adm_status_is_online 兼容旧字面量 "online"/"offline"（过渡期不丢件）；
  (c) butler/ 树下不再残留手写 ad-hoc 错误前缀 INBOX_DROP/INBOX_FAIL/AF_CHANNEL_ERROR
      （homesdk.adm.errors 的 ADM_ERR_* 常量替代；tests/ 里允许出现，故扫描不入库内测试目录）；
  (d) LWT 遗嘱载荷是 decode_status 可读的 state=offline JSON。

前置：pip install homesdk==0.3.2（vendor/ 内有固化 wheel）。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from butler import __version__ as BUTLER_VERSION
from butler.bus import mqtt_client as mqtt_client_mod
from butler.bus.topics import ADM_STATUS, SUB_ADM_AF_STATUS, adm_status_is_online
from homesdk.adm.status import decode_status, encode_status

REPO = Path(__file__).resolve().parent.parent
FORBIDDEN_TOKENS = ("INBOX_DROP", "INBOX_FAIL", "AF_CHANNEL_ERROR")


def _fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        mqtt_client_id="test-butler",
        mqtt_user="u",
        mqtt_password="p",
        mqtt_host="localhost",
        mqtt_port=1883,
    )


class _FakePahoClient:
    """只记不调真 broker：捕获 will_set / publish 两条腿。"""

    instances: list["_FakePahoClient"] = []

    def __init__(self, *args, **kwargs):
        self.will = None
        self.published = []
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        _FakePahoClient.instances.append(self)

    def username_pw_set(self, *args, **kwargs):
        pass

    def will_set(self, topic, payload, qos=1, retain=False):
        self.will = {"topic": topic, "payload": payload, "qos": qos, "retain": retain}

    def publish(self, topic, payload, qos=1, retain=False):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})

    def connect_async(self, *args, **kwargs):
        pass

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def is_connected(self):
        return True


class _MQTTFixture(unittest.TestCase):
    def setUp(self):
        _FakePahoClient.instances = []
        patcher = mock.patch.object(mqtt_client_mod.mqtt, "Client", _FakePahoClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.loop = mock.MagicMock()
        self.mc = mqtt_client_mod.MQTTClient(_fake_settings(), self.loop)
        self.paho = _FakePahoClient.instances[-1]


class StatusPublishLeg(_MQTTFixture):
    """(a) 发布腿：ADM_STATUS 从字面量升级为 encode_status JSON（契约 v2.0 §7.1）。"""

    def test_publish_payload_is_json_with_contract_keys(self):
        self.mc._publish_adm_online()
        sent = [m for m in self.paho.published if m["topic"] == ADM_STATUS]
        self.assertEqual(len(sent), 1, self.paho.published)
        obj = json.loads(sent[0]["payload"])
        self.assertIsInstance(obj, dict)
        self.assertEqual(
            set(obj.keys()), {"state", "ts", "degraded", "reasons", "version"}, obj
        )
        self.assertEqual(obj["state"], "online")
        self.assertEqual(obj["version"], BUTLER_VERSION)
        self.assertFalse(obj["degraded"])
        self.assertEqual(obj["reasons"], [])

    def test_graceful_offline_publish_is_json(self):
        # stop() 里 PUB_STATUS 那条既有 JSON 心跳不动，ADM_STATUS 这条必须走 encode_status
        self.mc.stop()
        sent = [m for m in self.paho.published if m["topic"] == ADM_STATUS]
        self.assertEqual(len(sent), 1, self.paho.published)
        obj = decode_status(sent[0]["payload"])
        self.assertEqual(obj["state"], "offline")

    def test_payload_single_point_roundtrips(self):
        obj = decode_status(self.mc.adm_status_json(True))
        self.assertEqual(obj["state"], "online")
        obj = decode_status(self.mc.adm_status_json(False))
        self.assertEqual(obj["state"], "offline")


class LwtLeg(_MQTTFixture):
    """(d) LWT 遗嘱：offline 由 broker 代发，载荷同样是 encode_status JSON。"""

    def test_lwt_payload_is_json_offline(self):
        self.assertIsNotNone(self.paho.will, "构造时未挂 LWT")
        self.assertEqual(self.paho.will["topic"], ADM_STATUS)
        self.assertTrue(self.paho.will["retain"])
        obj = decode_status(self.paho.will["payload"])
        self.assertEqual(obj["state"], "offline")
        self.assertEqual(obj["version"], BUTLER_VERSION)


class LegacyLiteralCompat(unittest.TestCase):
    """(b) 消费腿：decode_status / 判读单点必须继续读得懂旧字面量。"""

    def test_decode_status_accepts_literals(self):
        self.assertEqual(decode_status("online"), {"state": "online"})
        self.assertEqual(decode_status("offline"), {"state": "offline"})
        self.assertEqual(decode_status(b"online"), {"state": "online"})
        self.assertEqual(decode_status(b" offline \n"), {"state": "offline"})

    def test_decode_status_json_object_passthrough(self):
        text = encode_status("online", version="9.9", ts=123)
        obj = decode_status(text)
        self.assertEqual(obj["state"], "online")
        self.assertEqual(obj["version"], "9.9")

    def test_is_online_helper_reads_both_shapes(self):
        self.assertTrue(adm_status_is_online("online"))
        self.assertFalse(adm_status_is_online("offline"))
        self.assertTrue(adm_status_is_online(b"online"))
        self.assertTrue(adm_status_is_online(encode_status("online", version="x")))
        self.assertFalse(adm_status_is_online(encode_status("offline", version="x")))
        # 旧 dict 缓存形（过渡分支）仍可读
        self.assertTrue(adm_status_is_online({"online": True}))
        self.assertFalse(adm_status_is_online({"online": False}))
        # 不可解 fail-closed，不抛错
        self.assertFalse(adm_status_is_online(""))
        self.assertFalse(adm_status_is_online("{not-json"))

    def test_on_message_routes_adm_status_through_decode(self):
        # 订阅腿：adm/*/status 载荷（旧字面量 或 新 JSON）都要 decode 成 dict 再入队
        captured = []

        def fake_enqueue(topic, payload):
            captured.append((topic, payload))

        mc = mqtt_client_mod.MQTTClient.__new__(mqtt_client_mod.MQTTClient)
        mc.loop = mock.MagicMock()
        mc.loop.call_soon_threadsafe.side_effect = lambda fn, *a: fn(*a)
        mc.on_result = None
        mc._enqueue = fake_enqueue
        mc._note_bad_payload = mock.MagicMock()
        msg = SimpleNamespace(topic=SUB_ADM_AF_STATUS, payload=b"online")
        mc._on_message(None, None, msg)
        self.assertEqual(captured[-1], (SUB_ADM_AF_STATUS, {"state": "online"}))
        msg2 = SimpleNamespace(topic=SUB_ADM_AF_STATUS,
                               payload=encode_status("online", version="1.0").encode())
        mc._on_message(None, None, msg2)
        self.assertEqual(captured[-1][1]["state"], "online")
        # 垃圾载荷：记坏件、不入队、不抛
        n = len(captured)
        mc._on_message(None, None, SimpleNamespace(topic=SUB_ADM_AF_STATUS, payload=b"{broken"))
        self.assertEqual(len(captured), n)
        mc._note_bad_payload.assert_called_once()


class NoAdHocErrorPrefixes(unittest.TestCase):
    """(c) 规格 §三-2：三仓 ad-hoc 错误前缀归零，改 import homesdk.adm.errors 常量。"""

    def test_butler_tree_has_no_handtyped_prefixes(self):
        hits = []
        for path in sorted((REPO / "butler").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in FORBIDDEN_TOKENS:
                if token in text:
                    hits.append(f"{path.relative_to(REPO)}: {token}")
        self.assertEqual(hits, [], "残留 ad-hoc 错误前缀：\n" + "\n".join(hits))

    def test_inbox_and_af_bridge_import_adm_errors(self):
        for rel in ("butler/bus/inbox.py", "butler/af_bridge.py"):
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertIn("from homesdk.adm.errors import", text, rel)

    def test_adm_errors_constants_are_importable(self):
        from homesdk.adm.errors import ADM_ERRORS, ADM_ERR_INTERNAL, is_adm_err

        self.assertTrue(is_adm_err(ADM_ERR_INTERNAL))
        self.assertFalse(is_adm_err("INBOX_DROP"))
        self.assertEqual(len(ADM_ERRORS), 6)


if __name__ == "__main__":
    unittest.main()
