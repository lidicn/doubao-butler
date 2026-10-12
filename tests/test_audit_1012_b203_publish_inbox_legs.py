# -*- coding: utf-8 -*-
"""批203（DB-02 投递件）：`28310ab` 同笔回退的第 2、3 格恢复腿。

依据：DCD `20261012-DB-ADMFired口径回退与MA状态腿-裁定.md` 补记一 二（最小集三处**扩为六处**）
      ＋ ADM 链主 `投递/DB-02.md` §一-2/§三（A1 发布腿、A2 inbox 码面）。

覆盖三件：
  (A) **status 发布腿**（`butler/bus/mqtt_client.py`）：LWT／在线／优雅下线三条腿的载荷必须是
      契约 v2.0 §7.1 的 encode_status JSON，⛔ 字面量 `online`/`offline`；version 单点与 caps 同值。
  (B) **inbox 错误码面**（`butler/bus/inbox.py`）：8 处联动失败日志带 `ADM_ERR_*` 常量，
      ⛔ 本仓自造前缀 `INBOX_DROP`/`INBOX_FAIL`/`AF_CHANNEL_ERROR`（契约 §7.2「失败必须带码」）。
  (C) 形状尺的**双向校准**：每把尺配一格「合成样例应咬」＋一格「真码应放行」——
      只跑真文件的形状尺，恒绿与恒红都不改变任何读数。

⛔ 本档不 import `butler.app`（import 期开库）；⛔ 不连真 broker（全用替身客户端）。
⚠ 已知不闭合腿：`homesdk.presence.advertise` 内部才调 encode_status，所以本档对 mqtt_client
   判的是「不自拼载荷＋三条腿经库」，不是「mqtt_client 里出现 encode_status 字样」——
   后者正是批138 门（`test_audit_1010_batch138_presence_adoption.py:89`）列的 FORBIDDEN 形状。
"""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from butler.bus import mqtt_client as mqtt_client_mod
from butler.bus.topics import ADM_CAPS, ADM_STATUS, PUB_STATUS, adm_status_is_online

REPO = Path(__file__).resolve().parent.parent
MQTT_SRC = (REPO / "butler/bus/mqtt_client.py").read_text(encoding="utf-8")
INBOX_SRC = (REPO / "butler/bus/inbox.py").read_text(encoding="utf-8")
ADHOC_PREFIXES = ("INBOX_DROP", "INBOX_FAIL", "AF_CHANNEL_ERROR")


def butler_tree_files():
    for path in sorted((REPO / "butler").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path, path.read_text(encoding="utf-8", errors="replace")


# 授权名册＝8 条「收件箱丢件／分发失败」腿（ec35455 转的就是这 8 条）。
# ⚠ `INBOX_EVENT_WRITE_FAILED`（`inbox.py:190`）**不在这 8 条里**——被回退前那一版也没转它，
# 所以本档⛔ 把它算进判据（那会把尺子做成「恢复完也红」的恒红腿）；它是一格**新发现**，写进回执。
DROP_FAIL_MARKS = ("INBOX_DROP", "INBOX_FAIL", "inbox drop", "inbox fail")


def drop_fail_logger_calls(src: str) -> list[ast.Call]:
    out: list[ast.Call] = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("info", "warning", "error")
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            continue
        text = node.args[0].value
        if any(mark in text for mark in DROP_FAIL_MARKS):
            out.append(node)
    return out


def _fake_settings() -> SimpleNamespace:
    # 本档跑的是「回退后」的构造腿（直连 paho，批137 的 get_client 未授权恢复），
    # 所以 settings 仍要带 host/port/凭据三枚——它们眼下还在被读。
    return SimpleNamespace(mqtt_client_id="test-butler", mqtt_host="localhost",
                           mqtt_port=1883, mqtt_user="u", mqtt_password="p")


class _FakePahoClient:
    """只记不发：捕获 will_set／publish 两条腿。"""

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
        return SimpleNamespace(rc=0, mid=0)

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


class PublishLegBehaviour(unittest.TestCase):
    """(A) 三条腿的真行为：喂替身客户端，断言出口载荷是 §7.1 JSON 且两侧同形。"""

    def setUp(self):
        _FakePahoClient.instances = []
        p = mock.patch.object(mqtt_client_mod.mqtt, "Client",
                              lambda *a, **kw: _FakePahoClient())
        p.start()
        self.addCleanup(p.stop)
        self.mc = mqtt_client_mod.MQTTClient(_fake_settings(), mock.MagicMock())
        self.paho = _FakePahoClient.instances[-1]

    def _sent(self, topic):
        return [m for m in self.paho.published if m["topic"] == topic]

    def _plan_version(self) -> str:
        ver = getattr(mqtt_client_mod, "ADM_PLAN_VERSION", None)
        self.assertIsInstance(ver, str, "mqtt_client 没有 ADM_PLAN_VERSION 单点")
        return ver

    def _contract_json(self, text: str) -> dict:
        # 先证「它是 JSON 对象」再解——否则字面量载荷在 json.loads 那层抛 ValueError，
        # RED 会挂成 ERROR（读不出「为什么红」）。
        self.assertTrue(isinstance(text, str) and text.startswith("{"),
                        f"载荷不是 §7.1 的 JSON 对象：{text!r}")
        obj = json.loads(text)
        self.assertEqual(set(obj.keys()), {"state", "ts", "degraded", "reasons", "version"}, obj)
        return obj

    def test_plan_version_single_point_exists(self):
        ver = self._plan_version()
        self.assertTrue(ver, "ADM_PLAN_VERSION 是空串")

    def test_lwt_payload_is_json_offline(self):
        self.mc.start()
        self.assertIsNotNone(self.paho.will, "start() 未挂 LWT")
        self.assertEqual(self.paho.will["topic"], ADM_STATUS)
        self.assertTrue(self.paho.will["retain"])
        obj = self._contract_json(self.paho.will["payload"])
        self.assertEqual(obj["state"], "offline", obj)
        self.assertEqual(obj["version"], self._plan_version())

    def test_online_leg_publishes_contract_json(self):
        self.mc._publish_adm_online()
        sent = self._sent(ADM_STATUS)
        self.assertEqual(len(sent), 1, self.paho.published)
        obj = self._contract_json(sent[0]["payload"])
        self.assertEqual(obj["state"], "online", obj)
        self.assertFalse(obj["degraded"])
        self.assertEqual(obj["reasons"], [])
        caps = self._sent(ADM_CAPS)
        self.assertEqual(len(caps), 1, self.paho.published)

    def test_graceful_offline_leg_is_json(self):
        self.mc.stop()
        sent = self._sent(ADM_STATUS)
        self.assertEqual(len(sent), 1, self.paho.published)
        self.assertEqual(self._contract_json(sent[0]["payload"])["state"], "offline")
        # PUB_STATUS 那条既有 JSON 心跳⛔ 一起改形
        pub = self._sent(PUB_STATUS)
        self.assertEqual(len(pub), 1, self.paho.published)
        self.assertIsInstance(json.loads(pub[0]["payload"]), dict)

    def test_online_and_caps_versions_match(self):
        self.mc._publish_adm_online()
        st = self._contract_json(self._sent(ADM_STATUS)[0]["payload"])
        caps = json.loads(self._sent(ADM_CAPS)[0]["payload"])
        self.assertEqual(st["version"], self._plan_version())
        self.assertEqual(st["version"], caps["version"],
                         "status.version 与 caps.version 两份真值（契约 v2.0 §A）")

    def test_online_offline_roundtrip_same_shape(self):
        self.mc._publish_adm_online()
        self.mc.stop()
        sts = self._sent(ADM_STATUS)
        self.assertEqual([self._contract_json(m["payload"])["state"] for m in sts],
                         ["online", "offline"], sts)
        self.assertEqual({self._contract_json(m["payload"])["version"] for m in sts},
                         {self._plan_version()})

    def test_consumer_single_point_reads_whatever_we_publish(self):
        # 发布腿与消费腿必须互认。⚠ 这一格是**兼容守卫**，恢复前后都应绿（旧字面量也能被
        # 判读单点读对）——它⛔ 当 RED 驱动用，红的时候要看上面那几格。
        self.mc._publish_adm_online()
        self.mc.stop()
        sts = self._sent(ADM_STATUS)
        self.assertEqual([adm_status_is_online(m["payload"]) for m in sts], [True, False], sts)


class PublishLegShape(unittest.TestCase):
    """(A′) 发布腿形状：字面量单点必须消失，三条腿必须经库（⛔ 本仓再拼一份载荷）。"""

    def test_adm_status_literal_helper_is_gone(self):
        self.assertNotIn("adm_status_literal", MQTT_SRC)
        self.assertFalse(hasattr(mqtt_client_mod.MQTTClient, "adm_status_literal"))

    def test_three_legs_go_through_advertise(self):
        calls = [n.func.id for n in ast.walk(ast.parse(MQTT_SRC))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "advertise"]
        self.assertGreaterEqual(len(calls), 3, f"advertise 调用点 {len(calls)} 处：{calls}")

    def test_no_self_serialized_raw_publish_left(self):
        # 批138 门的 FORBIDDEN 形状：_publish_raw 是「本仓自己拼好再原样发」的腿，恢复后不该还在
        self.assertNotIn("_publish_raw", MQTT_SRC)

    def test_rule_bites_synthetic_literal_helper(self):
        # 负控/校准：把被回退的那一版喂进同一把尺，必须咬住（否则上面三格是恒绿）
        synth = ('def adm_status_literal(self, online):\n    return "online" if online else "offline"\n'
                 'self._publish_raw(ADM_STATUS, self.adm_status_literal(True))\n')
        self.assertIn("adm_status_literal", synth)
        self.assertIn("_publish_raw", synth)

    def test_butler_tree_has_no_literal_status_helper(self):
        hits = [str(p.relative_to(REPO)) for p, text in butler_tree_files()
                if "adm_status_literal" in text]
        self.assertEqual(hits, [], f"残留字面量发布单点：{hits}")


class InboxErrorCodeFace(unittest.TestCase):
    """(B) inbox 码面：失败日志带 `ADM_ERR_*`，⛔ 自造前缀。"""

    def test_inbox_imports_adm_errors(self):
        self.assertIn("from homesdk.adm.errors import", INBOX_SRC)

    def test_every_drop_or_fail_log_carries_a_code(self):
        # 契约 §7.2「失败必须带码」：判据不是「有几条带码」而是「一条不许漏」
        dropfail = drop_fail_logger_calls(INBOX_SRC)
        self.assertGreaterEqual(len(dropfail), 8, f"只认出 {len(dropfail)} 处丢件/失败日志，名册可能读窄了")
        bare = [n.lineno for n in dropfail
                if not any(isinstance(a, ast.Name) and a.id.startswith("ADM_ERR") for a in n.args)]
        self.assertEqual(bare, [], f"这些丢件/失败日志没带 ADM_ERR_* 码：行 {bare}")

    def test_inbox_has_no_adhoc_prefix_literals(self):
        for token in ADHOC_PREFIXES:
            self.assertNotIn(f'"{token}', INBOX_SRC, token)

    def test_butler_tree_has_no_adhoc_prefixes(self):
        hits = [f"{p.relative_to(REPO)}: {t}" for p, text in butler_tree_files()
                for t in ADHOC_PREFIXES if t in text]
        self.assertEqual(hits, [], "残留 ad-hoc 错误前缀：\n" + "\n".join(hits))

    def test_rule_bites_synthetic_adhoc_prefix(self):
        # 校准：把「被回退的那一版」的形状喂同一把尺，必须**认出腿、且判它没码**
        # （⛔ 只跑真文件的尺：恒绿与恒红都不改变任何读数）
        synth = ('def f(self):\n'
                 '    logger.info("INBOX_DROP channel=%s reason=x", c)\n'
                 '    logger.warning("INBOX_FAIL channel=%s err=%s", c, e)\n'
                 '    logger.error("AF_CHANNEL_ERROR y")\n')
        legs = drop_fail_logger_calls(synth)
        self.assertEqual(len(legs), 2, f"尺只认出 {len(legs)} 条：{[n.lineno for n in legs]}")
        self.assertEqual([n.lineno for n in legs if not any(
            isinstance(a, ast.Name) and a.id.startswith("ADM_ERR") for a in n.args)],
            [n.lineno for n in legs], "带码判据在合成样例上没咬住")
        got = [t for t in ADHOC_PREFIXES if t in synth]
        self.assertEqual(got, list(ADHOC_PREFIXES), got)


if __name__ == "__main__":
    unittest.main()
