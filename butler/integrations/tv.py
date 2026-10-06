"""TV 端发声：MQTT cmd/tts 主通道 + HTTP /api/tts/play 降级。

TV 播放的是音频 URL（cmd/tts 接收 {"url","volume"}），因此 TTS 合成后的公开 URL 经 MQTT 下发。
HTTP 降级用于 MQTT 不可用时直接让 TV 合成并播放文本。
"""
from __future__ import annotations

import asyncio
import httpx

from butler.bus.mqtt_client import MQTTClient
from butler.config import Settings
from butler.logging_setup import get_logger

logger = get_logger("butler.tv")


class TVClient:
    def __init__(self, settings: Settings, mqtt: MQTTClient | None = None):
        self.s = settings
        self.mqtt = mqtt
        self.loop: asyncio.AbstractEventLoop | None = None
        self._zap_pending: dict[str, asyncio.Future] = {}

    def set_mqtt(self, mqtt: MQTTClient) -> None:
        self.mqtt = mqtt
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    def on_result(self, payload: dict) -> None:
        """zap-tv 回执（MQTT 网络线程回调）：按 request_id 唤醒等待协程。"""
        rid = payload.get("request_id")
        if rid and rid in self._zap_pending:
            future = self._zap_pending.pop(rid)
            if not future.done():
                if self.loop is not None:
                    self.loop.call_soon_threadsafe(future.set_result, payload)
                else:
                    future.set_result(payload)

    def play_url(self, url: str, volume: int | None = None) -> None:
        """主通道：经 MQTT 让电视播放音频 URL。"""
        if self.mqtt is None:
            logger.warning("tv play_url: no mqtt client")
            return
        vol = volume if volume is not None else self.s.tts_volume
        from butler.bus.topics import PUB_TV_TTS

        logger.info("Publishing TTS to %s volume=%s url=%s", PUB_TV_TTS, vol, url)
        self.mqtt.publish(PUB_TV_TTS, {"url": url, "volume": vol})

    def notify(self, payload: dict) -> bool:
        """弹窗通知通道：经 MQTT cmd/notify 推送通知到电视；返回是否真发出去。

        mqtt 未就绪那腿只 logger.warning 就 return（消息丢掉、不抛），
        所以成败必须由这条腿自己说，⛔ 让调用方替它猜。
        """
        if self.mqtt is None:
            logger.warning("tv notify: no mqtt client")
            return False
        from butler.bus.topics import PUB_TV_NOTIFY

        logger.info("Publishing notify to %s title=%s", PUB_TV_NOTIFY, payload.get("title"))
        self.mqtt.publish(PUB_TV_NOTIFY, payload)
        return True

    async def zap(self, channel: str, timeout: float = 25.0) -> tuple[bool, dict]:
        """换台意图：经 MQTT 下发 zap-tv 并等待 result 回执（request_id 匹配）。
        zap-tv 负责：频道名归一化/匹配 channels.json、确保 mytv 前台、adb 数字键注入、LMK 被杀自动重试。"""
        if self.mqtt is None or self.loop is None:
            logger.warning("tv zap: no mqtt client")
            return False, {"error": "mqtt 未就绪"}
        import uuid
        from butler.bus.topics import PUB_TV_CONTROL
        request_id = uuid.uuid4().hex[:12]
        future = self.loop.create_future()
        self._zap_pending[request_id] = future
        logger.info("Publishing zap to %s channel=%s request_id=%s", PUB_TV_CONTROL, channel, request_id)
        self.mqtt.publish(PUB_TV_CONTROL, {"action": "zap", "channel": channel, "request_id": request_id}, qos=1)
        try:
            result = await asyncio.wait_for(future, timeout)
            ok = bool(result.get("ok"))
            if not ok:
                logger.warning("zap %s result not ok: %s", channel, result)
            return ok, result
        except asyncio.TimeoutError:
            self._zap_pending.pop(request_id, None)
            logger.warning("zap %s timeout after %ss", channel, timeout)
            return False, {"error": "超时未收到电视回执"}
    async def play_text(self, text: str, volume: int | None = None) -> bool:
        """降级通道：TV 本地合成并播放（HTTP）。"""
        vol = volume if volume is not None else self.s.tts_volume
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(
                    f"{self.s.tv_http_url.rstrip('/')}/api/tts/play",
                    json={"text": text, "volume": vol},
                )
                return r.status_code == 200
        except Exception as e:
            logger.warning("tv play_text failed: %s", e)
            return False

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self.s.tv_http_url.rstrip('/')}/api/health")
                return r.status_code == 200
        except Exception:
            return False

    async def play_fongmi(self, name: str) -> dict:
        """在飞牛TV上按片名搜索并播放指定影片/节目。

        调用 TV 端 POST /api/fongmi/play（TV 端拉起飞牛TV 并发布 MQTT，由 Node-RED 执行 ADB 完成搜索+播放）。
        返回 TV 端 JSON：{"ok": bool, "name": ..., "initials": ..., "message": ...}。
        TV 离线/接口异常时返回 {"ok": False, "error": ...}。
        """
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "empty_movie_name"}
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.post(
                    f"{self.s.tv_http_url.rstrip('/')}/api/fongmi/play",
                    json={"name": name},
                )
                if r.status_code == 200:
                    try:
                        return r.json()
                    except Exception:
                        return {"ok": True, "name": name, "message": "已提交飞牛TV播放请求"}
                return {"ok": False, "error": f"http_{r.status_code}", "detail": r.text[:200]}
        except Exception as e:
            logger.warning("tv play_fongmi failed: %s", e)
            return {"ok": False, "error": str(e)}
