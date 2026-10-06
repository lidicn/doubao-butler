"""批7（2026-10-02）验收：电视通知腿的「说成功」必须等于「真发了」。

现象（现读，权威树 /vol1/1000/docker/doubao-butler）：
  · `butler/integrations/tv.py:54 def notify(self, payload) -> None` 有两腿——
    `:56-58` mqtt 为 None 时 logger.warning 后 **直接 return**（消息丢掉、不抛），
    `:60-63` 正常时 mqtt.publish(PUB_TV_NOTIFY, payload)；两腿都返回 None。
  · `butler/notify/router.py:302 self.tv.notify(note.tv_payload)` 丢返回值，
    `:303` 无条件 `return ChannelResult(CHANNEL_TV, True)`
    ⇒ 丢弃那腿在 `NotifyResult.ok`（`:141 all(r.ok)`）上读成成功；
      现网三条 tv 调用方（cron_task.py:269／bus/inbox.py:313／api/notify_routes.py:81）
      都只看 res.ok ⇒ 拿到的是假绿。
  · `butler/notify/router.py:74 TVPopupPort` 只声明 `popup`，全树 `def popup` 命中＝
    端口声明本身 + `tests/test_notify_router.py:74` 自带的假件；真身 TVClient 没有 popup
    ⇒ 无 tv_payload 那条分支（`:304`）在生产对象上必然 AttributeError，被 `_deliver`
    `:249-251` 吞成 ok=False。本文件把「此刻的真实形状」也钉成一例。

本文件⛔ 起服务、⛔ 连现网 MQTT、⛔ 碰活库：只用假 mqtt 记录器与临时 Settings。
"""
from __future__ import annotations

import asyncio
import unittest

from butler.config import get_settings
from butler.integrations.tv import TVClient
from butler.notify.router import NotifyRouter, TVPopupPort


class RecorderMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))


def _client(mqtt):
    return TVClient(get_settings(), mqtt=mqtt)


class NotifyReturnTruthTest(unittest.TestCase):
    def test_notify_returns_false_when_mqtt_is_missing(self):
        """丢弃腿必须自己说「没发出去」，⛔ 让调用方替它猜。"""
        out = _client(None).notify({"title": "告警", "message": "客厅漏水"})
        self.assertIs(out, False, f"mqtt 未就绪却返回 {out!r}")

    def test_notify_returns_true_after_publish(self):
        rec = RecorderMqtt()
        out = _client(rec).notify({"title": "提醒", "message": "该喝水了"})
        self.assertIs(out, True, f"已 publish 却返回 {out!r}")
        self.assertEqual(len(rec.published), 1, rec.published)
        topic, payload, _qos = rec.published[0]
        self.assertTrue(topic.endswith("/cmd/notify"), topic)
        self.assertEqual(payload["message"], "该喝水了")


class RouterTruthTest(unittest.TestCase):
    def _route(self, tv, **kw):
        r = NotifyRouter(tv=tv)
        return asyncio.run(r.notify("tv", "客厅漏水", **kw))

    def test_dropped_notify_is_not_reported_ok(self):
        class SilentTV:
            def __init__(self):
                self.seen = []

            def notify(self, payload):
                self.seen.append(payload)
                return False

        tv = SilentTV()
        res = self._route(tv, tv_payload={"title": "告警"})
        cr = res.results["tv"]
        self.assertFalse(cr.ok, "notify 明明丢弃，路由却报 ok=True＝假绿通道")
        self.assertFalse(res.ok, "整条 NotifyResult 不该跟着一起绿")
        self.assertEqual(tv.seen, [{"title": "告警"}])

    def test_delivered_notify_is_reported_ok(self):
        class LoudTV:
            def __init__(self):
                self.seen = []

            def notify(self, payload):
                self.seen.append(payload)
                return True

        tv = LoudTV()
        res = self._route(tv, tv_payload={"title": "提醒"})
        self.assertTrue(res.results["tv"].ok)
        self.assertTrue(res.ok)
        self.assertEqual(tv.seen, [{"title": "提醒"}])

    def test_port_declares_the_method_the_router_calls(self):
        """`_to_tv` 调 notify，端口就得声明 notify，⛔ 只声明 popup 让假件过关。"""
        self.assertTrue(hasattr(TVPopupPort, "notify"),
                        "TVPopupPort 未声明 notify，而 router.py:302 在调它")
        self.assertTrue(hasattr(TVClient, "notify"))

    def test_real_client_without_payload_fails_loudly_today(self):
        """钉住「此刻的形状」：真身 TVClient 没有 popup，无 tv_payload 那腿只能得 ok=False。
        哪天给 TVClient 加了 popup，这条会红——那是要求复核端口与分支的信号，⛔ 顺手删掉它。"""
        self.assertFalse(hasattr(TVClient, "popup"),
                         "TVClient 新增了 popup：本例与 router 的无 payload 分支要一起重看")
        rec = RecorderMqtt()
        res = self._route(_client(rec))
        cr = res.results["tv"]
        self.assertFalse(cr.ok, "没实现 popup 却报成功")
        self.assertTrue(cr.error, "失败要带原因，⛔ 空 error 的 False")
        self.assertEqual(rec.published, [], "这条分支不该发出任何 MQTT 消息")


if __name__ == "__main__":
    unittest.main(verbosity=2)
