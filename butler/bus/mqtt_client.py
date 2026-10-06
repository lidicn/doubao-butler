"""paho-mqtt 封装：自动重连、LWT 遗嘱、订阅分发到 asyncio 队列。

paho 的网络线程与 asyncio 事件循环不在同一线程，消息通过 call_soon_threadsafe 投入队列，
由 app 的消费者协程取出后交给对话核心。发布操作线程安全（paho 内部已加锁）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion

from butler import __version__
from butler.bus.topics import ADM_CAPS, ADM_STATUS, PUB_STATUS, SUB_TOPICS, SUB_TV_RESULT
from butler.mcp.schema_gen import get_tool_names as mcp_tool_names
from butler.config import Settings
from butler.logging_setup import get_logger

logger = get_logger("butler.mqtt")


class MQTTClient:
    def __init__(self, settings: Settings, loop: asyncio.AbstractEventLoop):
        self.s = settings
        self.loop = loop
        self.queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=2000)
        self.on_result = None  # 由 app 注入 (payload)->None；zap-tv 回执短路通道
        self.connected = asyncio.Event()
        self.dropped = 0                  # P1-3：队列满丢件计数（⛔ 只打日志＝丢了多少不可查）
        self._client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
            clean_session=False,
        )
        self._client.username_pw_set(settings.mqtt_user, settings.mqtt_password)
        # ADM_STATUS 心跳周期。caps 里的 hb/stale 两格、以及心跳协程实际睡的时长，
        # 都由这一枚派生——⛔ 三处各写一个 60（下发的是 60、实际跳 90 那类自相矛盾）。
        self.adm_hb_interval = 60.0
        # LWT 挂 ADM_STATUS：契约表 §1.1 的离线语义由 broker 代发，载荷是字面量 `offline`。
        # ⛔ JSON——homesdk.presence.is_online() 比的是 "online" 这个串，形状混发＝
        # 别的仓把管家读成永不在线（PUB_STATUS 那条既有主题不动，仍发 JSON 心跳）。
        self._client.will_set(ADM_STATUS, self.adm_status_literal(False), qos=1, retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info("MQTT connected as %s", self.s.mqtt_user)
            for t in SUB_TOPICS:
                client.subscribe(t, qos=1)
            self._publish(PUB_STATUS, {"online": True, "ts": time.time()}, qos=1, retain=True)
            self._publish_adm_online()
            self.loop.call_soon_threadsafe(self.connected.set)
            self.loop.call_soon_threadsafe(self._ensure_heartbeat)
        else:
            logger.error("MQTT connect failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        logger.warning("MQTT disconnected: %s", reason_code)
        self.loop.call_soon_threadsafe(self.connected.clear)

    def _note_bad_payload(self, topic: str, raw: bytes, why: str) -> None:
        """把"我收到但用不了"变成一条可查的痕迹。字段名刻意避开 text=/token= 形状（日志脱敏器会吃整行）。"""
        key = f"bad|{topic}|{why}"
        seen = self.__dict__.setdefault("_bad_seen", {})  # 键只有 主题×3 种原因，天然有界
        now = time.time()
        if now - seen.get(key, 0.0) < 60.0:
            return
        seen[key] = now
        logger.warning("WO-ME-208 MQTT 载荷不可用，已丢弃：主题=%s 原因=%s 字节数=%d 头字节=%s",
                       topic, why, len(raw), raw[:12].hex())

    def _enqueue(self, topic: str, payload: dict) -> None:
        """跑在事件循环里：队列满在这里咬，计数与告警也就落在这里。"""
        try:
            self.queue.put_nowait((topic, payload))
        except asyncio.QueueFull:
            self._note_dropped(topic)

    def _note_dropped(self, topic: str) -> None:
        """丢件先变成数，再变成 60 秒一条的痕迹（节流形制同 _note_bad_payload）。"""
        self.dropped += 1
        now = time.time()
        if now - self.__dict__.get("_drop_logged_at", 0.0) < 60.0:
            return
        self.__dict__["_drop_logged_at"] = now
        logger.warning("MQTT_QUEUE_FULL 队列满丢弃：累计=%d 最近主题=%s（60 秒内不重复播报）",
                       self.dropped, topic)

    def _on_message(self, client, userdata, msg):
        raw = msg.payload or b""
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self._note_bad_payload(msg.topic, raw, "非UTF-8")
            text = raw.decode("utf-8", "replace")  # 行为不变：仍然尽力解，只是从此有声
        if "\ufffd" in text:
            self._note_bad_payload(msg.topic, raw, "含替换符U+FFFD")
        # ADM 生态链约定：adm/<成员>/status 载荷是字面量 online/offline（非 JSON）。
        # 契约表 §1.1 + homesdk.presence.is_online 同式——这里必须跳过 JSON 解析，
        # 否则字面量在 json.loads 阶段就被丢弃，adm_peers 永远空（MA联动收尾附带发现）。
        if msg.topic.startswith("adm/") and msg.topic.endswith("/status"):
            payload = text.strip()
        else:
            try:
                payload = json.loads(text)
            except Exception:
                self._note_bad_payload(msg.topic, raw, "JSON解析失败")
                return
        try:
            if msg.topic == SUB_TV_RESULT and self.on_result is not None:
                try:
                    self.on_result(payload)
                except Exception:
                    logger.exception("on_result callback error")
                return
            # P1-3：队列满是在**事件循环里**咬的，闸也就必须落在那里——旧形制的 except 挂在
            # 网络线程那句 call_soon_threadsafe 上，异常永不到货＝死闸（丢件只进 loop 日志）。
            self.loop.call_soon_threadsafe(self._enqueue, msg.topic, payload)
        except (asyncio.QueueFull, RuntimeError) as e:
            # 循环已关／线程边界当场失败：⛔ 冒泡回 paho 网络线程（它会带走整条收消息链路）
            self._note_dropped(msg.topic)
            logger.warning("MQTT submit failed topic=%s err=%s", msg.topic, e)

    def _publish(self, topic: str, payload: dict, qos: int = 1, retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=qos, retain=retain)

    def publish(self, topic: str, payload: dict, qos: int = 1, retain: bool = False) -> None:
        try:
            self._publish(topic, payload, qos, retain)
        except Exception as e:
            logger.warning("publish %s failed: %s", topic, e)

    def _publish_raw(self, topic: str, text: str, qos: int = 1, retain: bool = False) -> None:
        """字面量主题专用：契约表 §1.1 的 status 载荷就是 `online`/`offline` 两个词。
        ⛔ 复用 _publish——那条腿 json.dumps 会把 offline 编成 "offline"，
        按字面量比在线的仓（homesdk.presence）会把管家读成永远不在线。
        """
        self._client.publish(topic, text, qos=qos, retain=retain)

    def start(self) -> None:
        self._client.connect_async(self.s.mqtt_host, self.s.mqtt_port, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        try:
            self._publish(PUB_STATUS, {"online": False, "ts": time.time()}, qos=1, retain=True)
            self._publish_raw(ADM_STATUS, self.adm_status_literal(False), qos=1, retain=True)
        except Exception as e:
            # P2-9 ②类（批42 组4）：吞掉的是**下线通知**⇒ 契约表 §1.1 另一头仍以为管家在线
            logger.warning("MQTT offline notice failed: %s", e)
        self._client.loop_stop()
        try:
            self._client.disconnect()
        except Exception:
            pass

    def adm_status_literal(self, online: bool) -> str:
        """ADM_STATUS 载荷单点：契约表 §1.1 的字面量 `online`/`offline`。
        LWT、在线、优雅下线三条腿共用——⛔ 三处各拼一遍，迟早拼成两种形状。
        """
        return "online" if online else "offline"

    def adm_caps_payload(self) -> dict:
        """adm/<成员>/caps：契约表要求的 mcp/tools/version，新鲜度两格现在随这条下发
        （status 已是字面量，装不下 ts/hb/stale；消费侧靠 LWT＋新鲜度两条腿判在线）。
        """
        detail = self.adm_status_payload(online=True)
        return {
            "service": "doubao-butler",
            "mcp": True,
            "tools": mcp_tool_names(),
            "version": "2.7",  # 契约 v2.0 §A：version=计划号（非包 __version__=1.0.0）
            "ts": detail["ts"],
            "hb_interval_sec": detail["hb_interval_sec"],
            "stale_after_sec": detail["stale_after_sec"],
        }

    def adm_status_payload(self, online: bool) -> dict:
        """ADM_STATUS 载荷单点（下线与在线两条腿共用，⛔ 各拼一遍字面量）。

        `hb_interval_sec`/`stale_after_sec` 是**给消费侧的契约**：retained 载荷不会自己过期，
        订阅方要么按 `now - ts > stale_after_sec` 判「不再新鲜」，要么等真 offline 那一条。
        后者在硬杀场景要等 broker 侧 keepalive 判死（§14.11 量在文书里），所以这两条腿各管一段。
        """
        return {
            "online": bool(online),
            "ts": time.time(),
            "service": "doubao-butler",
            "hb_interval_sec": self.adm_hb_interval,
            "stale_after_sec": int(round(self.adm_hb_interval * 2.5)),
        }

    def _publish_adm_online(self) -> None:
        """§13.4 在线探测：retained 状态 + 能力摘要，供 ADM 生态各仓读取。"""
        try:
            self._publish_raw(ADM_STATUS, self.adm_status_literal(True), qos=1, retain=True)
            self._publish(ADM_CAPS, self.adm_caps_payload(), qos=1, retain=True)
        except Exception as e:
            logger.warning("adm status publish failed: %s", e)

    def _ensure_heartbeat(self) -> None:
        if getattr(self, "_adm_hb_task", None) is None:
            self._adm_hb_task = self.loop.create_task(self.adm_heartbeat())

    async def adm_heartbeat(self, interval: float | None = None) -> None:
        interval = self.adm_hb_interval if interval is None else float(interval)
        while True:
            await asyncio.sleep(interval)
            try:
                if self._client.is_connected():
                    self._publish_adm_online()
                else:
                    logger.debug("adm heartbeat skipped: mqtt offline")
            except Exception as e:
                logger.warning("adm heartbeat failed: %s", e)

    async def wait_connected(self, timeout: float = 15) -> bool:
        try:
            await asyncio.wait_for(self.connected.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False
