"""批137（DCD 20261006 计划 · homesdk 落地细案 模块4）：MQTT 客户端构造收编 homesdk.mqtt。

判据两面：
  (甲) 形状尺——butler/bus/mqtt_client.py 里手写等价物（自拼 mqtt.Client、自塞
      username_pw_set、自读 settings.mqtt_user/host/port、硬编码 keepalive=60）归零；
      homesdk.mqtt 三个入口（get_client / broker_settings / resolve_credentials）在场。
      尺子配负控：OLD_SHAPE_SAMPLES 必须被同一把尺咬住（防"恒绿尺"）。
  (乙) 行为——缺凭据/半对凭据在**构造期**抛 MqttCredentialsMissing（fail-closed，
      ⛔ 匿名回退＝DeskPilot 那扇"username 空就匿名连"的门）；start() 的
      broker 三元组来自 broker_settings()（MQTT_HOST 缺失抛 MissingEnv 且**不发 connect**）。

前置：homesdk==0.3.2（vendor/ 固化 wheel），paho-mqtt 在测试环境可装（本文件不需要真连）。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import butler.bus.mqtt_client as mqtt_client_mod
from butler.bus.mqtt_client import MQTTClient
from homesdk.config import MissingEnv
from homesdk.mqtt import MqttCredentialsMissing

REPO = Path(__file__).resolve().parent.parent
MQTT_CLIENT_SRC = (REPO / "butler" / "bus" / "mqtt_client.py").read_text(encoding="utf-8")
CONFIG_SRC = (REPO / "butler" / "config.py").read_text(encoding="utf-8")


def _env_scrub(**sets):
    """PATH 安全快照：只摘 MQTT*/HOMESDK* 凭据与地址键，其余 env（PATH 等）原样保留。

    ⛔ mock.patch.dict(clear=True) 裸用——清空 PATH 会让 trio 的惰性 ctypes 导入当场崩
    （批136 实证），这里只按键名过滤。
    """
    base = {
        k: v
        for k, v in os.environ.items()
        if "MQTT" not in k and not k.startswith("HOMESDK_")
    }
    base.update({k: v for k, v in sets.items() if v is not None})
    return mock.patch.dict(os.environ, base, clear=True)


class _FakeClient:
    """homesdk.mqtt.get_client 的替身：记录构造参数与 connect_async 落点。"""

    def __init__(self):
        self.will = None
        self.connect_calls = []
        self.loop_started = 0

    def will_set(self, topic, payload, qos=1, retain=False):
        self.will = {"topic": topic, "payload": payload, "qos": qos, "retain": retain}

    def connect_async(self, *args, **kwargs):
        self.connect_calls.append((args, kwargs))

    def loop_start(self):
        self.loop_started += 1

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def publish(self, *args, **kwargs):
        pass


def _settings_stub() -> SimpleNamespace:
    # 只剩 mqtt_client_id：其余 mqtt_* 字段应随本批从 Settings 删除——
    # 若实现还在偷读它们，SimpleNamespace 会当场 AttributeError（比读真值更早咬）。
    return SimpleNamespace(mqtt_client_id="test-butler")


class ShapeRuler(unittest.TestCase):
    """(甲) 形状尺：手写等价物归零 + homesdk 三入口在场 + 尺子自校准负控。"""

    FORBIDDEN_MQTT = (
        "username_pw_set",
        "CallbackAPIVersion",
        "mqtt.Client(",
        "settings.mqtt_user",
        "settings.mqtt_password",
        "settings.mqtt_host",
        "settings.mqtt_port",
        "self.s.mqtt_user",
        "self.s.mqtt_host",
        "self.s.mqtt_port",
        "192.168.2.200",
        "keepalive=60",
    )
    REQUIRED_MQTT = ("get_client(", "broker_settings(", "resolve_credentials(")
    FORBIDDEN_CONFIG = (
        "mqtt_host",
        "mqtt_port",
        "mqtt_user",
        "mqtt_password",
        '"MQTT_USER"',
        '"MQTT_PASSWORD"',
    )
    OLD_SHAPE_SAMPLES = [
        'self._client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id="x")',
        'self._client.username_pw_set(settings.mqtt_user, settings.mqtt_password)',
        'logger.info("MQTT connected as %s", self.s.mqtt_user)',
        'self._client.connect_async(self.s.mqtt_host, self.s.mqtt_port, keepalive=60)',
        'mqtt_host: str = "192.168.2.200"',
    ]

    def test_ruler_bites_old_shape(self):
        # 负控：同一把尺对旧形状样本必须咬到（防恒绿尺）。
        for i, sample in enumerate(self.OLD_SHAPE_SAMPLES):
            hits = [tok for tok in self.FORBIDDEN_MQTT + self.FORBIDDEN_CONFIG if tok in sample]
            self.assertTrue(hits, "负控样本 %d 尺子咬不住：%r" % (i, sample))

    def test_mqtt_client_no_handcrafted_shape(self):
        hits = [tok for tok in self.FORBIDDEN_MQTT if tok in MQTT_CLIENT_SRC]
        self.assertEqual(hits, [], "mqtt_client.py 残留手写等价物：%s" % hits)

    def test_mqtt_client_uses_homesdk_entries(self):
        missing = [tok for tok in self.REQUIRED_MQTT if tok not in MQTT_CLIENT_SRC]
        self.assertEqual(missing, [], "homesdk.mqtt 入口未接上：%s" % missing)

    def test_config_no_broker_address_or_credential_fields(self):
        # 整词判定：mqtt_client_id 是保留字段，⛔ 被 "mqtt_" 前缀类模式误伤。
        hits = [tok for tok in self.FORBIDDEN_CONFIG if tok in CONFIG_SRC]
        self.assertEqual(hits, [], "config.py 残留 broker 地址/凭据字段：%s" % hits)


class ConstructionFailClosed(unittest.TestCase):
    """(乙) 构造期 fail-closed：缺凭据/半对凭据都抛，不产出半配置客户端。"""

    def test_missing_credentials_raise(self):
        loop = mock.MagicMock()
        with _env_scrub():
            with self.assertRaises(MqttCredentialsMissing):
                MQTTClient(_settings_stub(), loop)

    def test_half_pair_raises(self):
        # 只带用户名不带口令＝匿名形状（homesdk 文档原话），必须同样咬。
        loop = mock.MagicMock()
        with _env_scrub(MQTT_USER="solo"):
            with self.assertRaises(MqttCredentialsMissing):
                MQTTClient(_settings_stub(), loop)

    def test_full_pair_builds_with_homesdk_get_client(self):
        loop = mock.MagicMock()
        fake = _FakeClient()
        seen = {}

        def spy_get_client(client_id=None, *, peer=None, **kw):
            seen["client_id"] = client_id
            seen["peer"] = peer
            return fake

        with _env_scrub(MQTT_USER="u9", MQTT_PASSWORD="p9"):
            with mock.patch.object(mqtt_client_mod, "get_client", spy_get_client):
                mc = MQTTClient(_settings_stub(), loop)
        self.assertEqual(seen.get("client_id"), "test-butler")
        self.assertEqual(seen.get("peer"), "butler")
        self.assertEqual(getattr(mc, "_mqtt_user", None), "u9")
        # 批138 起 LWT 随 start()（CONNECT 之前）注册——构造期只配客户端，不留发布副作用。
        self.assertIsNone(fake.will)


class StartUsesBrokerSettings(unittest.TestCase):
    """(乙') start() 的地址来自 broker_settings()：现读 env、缺 host 不发 connect。"""

    def _build(self, fake):
        loop = mock.MagicMock()
        with _env_scrub(MQTT_USER="u9", MQTT_PASSWORD="p9",
                        MQTT_HOST="h.example", MQTT_PORT="1884", MQTT_KEEPALIVE="45"):
            with mock.patch.object(mqtt_client_mod, "get_client", lambda *a, **k: fake):
                mc = MQTTClient(_settings_stub(), loop)
        return mc

    def test_broker_tuple_flows_into_connect_async(self):
        fake = _FakeClient()
        mc = self._build(fake)
        with _env_scrub(MQTT_USER="u9", MQTT_PASSWORD="p9",
                        MQTT_HOST="h.example", MQTT_PORT="1884", MQTT_KEEPALIVE="45"):
            mc.start()
        self.assertEqual(len(fake.connect_calls), 1, fake.connect_calls)
        args, kwargs = fake.connect_calls[0]
        host, port = args[0], args[1]
        keepalive = kwargs.get("keepalive", args[2] if len(args) > 2 else None)
        self.assertEqual(host, "h.example")
        self.assertEqual(port, 1884)
        self.assertEqual(keepalive, 45)
        self.assertEqual(fake.loop_started, 1)

    def test_missing_host_fails_before_connect(self):
        fake = _FakeClient()
        mc = self._build(fake)
        with _env_scrub(MQTT_USER="u9", MQTT_PASSWORD="p9"):
            with self.assertRaises(MissingEnv):
                mc.start()
        self.assertEqual(fake.connect_calls, [], "缺 MQTT_HOST 仍发起了 connect＝假 fail-closed")


if __name__ == "__main__":
    unittest.main()
