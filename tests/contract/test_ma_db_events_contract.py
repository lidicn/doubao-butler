"""MA→DB 事件面契约闸（契约表 §1.2 ＋ §二 域名铁律 ＋ 计划第 6 步 ①的「事件」半边）。

上位：`E:\\NAS\\homesdk\\doc\\ADM联动主题注册表与消息契约.md` v1.0。
两条现读分歧本档点名（⛔ 把它抹平成绿）：
  A) §1.2 写的 `ma/presence` 载荷是 `{trace_id, ts, present:[…], rooms:[…]}`，
     而 DB 的消费端读的是 `{"members":[{name,member_id,room,via,confidence,…}], "total", "ts"}`
     ——表里那对 `present/rooms` 分不出「谁在哪个房间」，DB 的定位引擎要的是逐人逐房；
     这条按「已知分歧-待 DCD 裁」钉住，改了实现或改了表都要翻这条。
  B) §1.2 把 `ma/device-health` 的消费方写成 DB，DB 现在只 `logger.info`（预留）。
另有一条硬要求：§1.1 的 `adm/<成员>/status` 载荷是字面量 `online`/`offline`，
DB 是**消费侧**也要按字面量读（拿到 str 还去 `.get("online")` 会抛 AttributeError，
对端在线缓存永远空＝看着在跑、其实从没填过）。
"""
import ast
import unittest
from pathlib import Path

import butler as _butler_pkg
import butler.bus.topics as topics

REPO = Path(_butler_pkg.__file__).resolve().parent.parent
APP_SRC = REPO / "butler" / "app.py"

# 表 §1.2 原文载荷（出处：契约表「1.2 事件类」表体）
TABLE_MA_PRESENCE = {"trace_id": "t-1", "ts": 1.0, "present": ["lidicn"], "rooms": ["客厅"]}
# MA 今天实际发的形状（出处：`butler/presence/sources.py` MaPresenceSource 的类文档）
LIVE_MA_PRESENCE = {"members": [{"name": "lidicn", "member_id": "lidicn", "room": "客厅",
                                 "via": "arcface", "confidence": 0.95, "trigger": "face_detected"}],
                    "total": 1, "ts": "2026-10-02T03:00:00+08:00"}


class _StubEngine:
    def __init__(self):
        self.config = {"ma_presence": {"enabled": True, "signal_type": "arcface_recognized"}}
        self.signals = []

    def map_room_name(self, name):
        return {"客厅": "living_room", "书房": "study"}.get(name, "")

    def report_signal(self, **kw):
        self.signals.append(kw)


class MaEventContractTest(unittest.TestCase):
    def setUp(self):
        from butler.presence.sources import MaPresenceSource
        self.engine = _StubEngine()
        self.src = MaPresenceSource(self.engine)

    def test_00_subscribed_event_topics_match_the_table(self):
        self.assertEqual(topics.SUB_MA_PRESENCE, "ma/presence")
        self.assertEqual(topics.SUB_MA_DEVICE_HEALTH, "ma/device-health")
        self.assertIn(topics.SUB_MA_PRESENCE, topics.SUB_TOPICS)
        self.assertIn(topics.SUB_MA_DEVICE_HEALTH, topics.SUB_TOPICS)

    def test_01_ruler_bites_on_the_shape_db_actually_consumes(self):
        n = self.src.handle_message(LIVE_MA_PRESENCE)
        self.assertEqual(n, 1, "夹具/桩没生效＝本档全部分母为 0")
        self.assertEqual(self.engine.signals[0]["user_id"], "lidicn")
        self.assertEqual(self.engine.signals[0]["room"], "living_room")

    def test_02_contract_table_shape_is_still_not_consumed(self):
        """已知分歧 A：表 §1.2 的 present/rooms 形状 DB 现在一条信号都产不出。
        钉住「0」不是钉住它对——DCD 裁完（改表或改 MA 发布端）这条必须翻。"""
        self.assertEqual(self.src.handle_message(TABLE_MA_PRESENCE), 0)

    def test_03_device_health_has_no_consumer_yet(self):
        """分歧 B：表里写「消费方 DB」，码上只有日志。数的是调用，不是散文。"""
        tree = ast.parse(APP_SRC.read_text(encoding="utf-8-sig"))
        handled = 0
        for node in ast.walk(tree):
            if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                    and node.left.id == "topic" and any(
                        isinstance(c, ast.Name) and c.id == "SUB_MA_DEVICE_HEALTH" for c in node.comparators)):
                handled += 1
        self.assertGreaterEqual(handled, 1, "app.py 里连分支都没有＝我对现读的记忆过期了，尺要重做")
        src_text = APP_SRC.read_text(encoding="utf-8-sig")
        self.assertEqual(src_text.count("ma_device_health"), 0,
                         "已经有处理函数了？那这条的『只日志』口径过期，请连同 DCD 单一起翻")

    def test_04_db_never_publishes_into_another_repos_namespace(self):
        """§二 域名铁律：`ma/*` 归 MA、`af/*` 归 AF、`adm/<别人>/*` 归别人，DB 只许订阅。"""
        bad = []
        for py in sorted((REPO / "butler").rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=str(py))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("publish", "_publish", "_publish_raw")
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    t = node.args[0].value
                    if t.startswith(("ma/", "af/")) or (t.startswith("adm/") and not t.startswith("adm/doubao-butler/")):
                        bad.append("%s:%s %s" % (py.name, node.lineno, t))
        self.assertEqual(bad, [], "DB 直推了他仓域名：%s" % bad)


class PeerStatusLiteralTest(unittest.TestCase):
    """§1.1 消费侧：`adm/<成员>/status` 的字面量口径。"""

    def test_05_literal_online_is_the_only_true_value(self):
        self.assertTrue(topics.adm_status_is_online("online"))
        self.assertTrue(topics.adm_status_is_online(b"online"))
        self.assertTrue(topics.adm_status_is_online(" online "))
        self.assertFalse(topics.adm_status_is_online("offline"))
        self.assertFalse(topics.adm_status_is_online(""))
        self.assertFalse(topics.adm_status_is_online(None))

    def test_06_transition_accepts_json_but_only_for_the_online_flag(self):
        self.assertTrue(topics.adm_status_is_online({"online": True}))
        self.assertFalse(topics.adm_status_is_online({"online": False}))
        self.assertFalse(topics.adm_status_is_online({"ts": 1}))

    def test_07_app_dispatch_uses_the_helper_not_payload_get(self):
        src = APP_SRC.read_text(encoding="utf-8-sig")
        self.assertEqual(src.count('payload.get("online")'), 0,
                         "str 载荷上 .get(\"online\") 会抛 AttributeError＝对端在线缓存永远空")
        self.assertGreaterEqual(src.count("adm_status_is_online("), 1, "消费侧没走单点 helper")


if __name__ == "__main__":
    unittest.main()
