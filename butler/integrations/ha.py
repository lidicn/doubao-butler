"""Home Assistant REST：小爱音箱 tts.speak（兜底语音通道）+ 传感器状态读取。"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx

from butler.config import Settings
from butler.logging_setup import get_logger, warn_throttled

logger = get_logger("butler.ha")

# X08A 播完即停的停止提前量（毫秒）。
# 原理：player_play_operation stop 经小米云端（api2.mina.mi.com）下发，实测延迟 ~680-740ms；
# 在 position >= duration - LEAD 时发 stop，指令到达设备时恰好播完，截断的只是
# edge-tts 句尾静音（人耳无感），实现「播一次即停、无重复尾」。
# 若网络波动明显可适当调大（截断句尾）或调小（残留更短重复）。
XIAOMI_STOP_LEAD_MS = 800


class HAError(Exception):
    """HA 调用基础异常。"""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class HAEntityNotFound(HAError):
    """实体或服务不存在（HTTP 404）。"""
    pass


class HAUnauthorized(HAError):
    """HA 鉴权失败（HTTP 401/403）。"""
    pass


class HATimeout(HAError):
    """HA 调用超时。"""
    pass


class HAClient:
    def __init__(self, settings: Settings):
        self.s = settings
        # 每个设备的 watch 停止任务（会话管理：新播放先取消旧 watch，防误停后续播放）
        self._xiaomi_watch_tasks: dict[str, asyncio.Task] = {}
        self._xiaomi_token_refresh_task: asyncio.Task | None = None
        # did -> 播放方式缓存（devices.json mtime 变化时自动重建）
        self._play_method_cache: dict[str, str] = {}
        self._devices_mtime: float = 0.0

    async def start_xiaomi_token_refresh(self) -> None:
        """启动小米 service_token 自动刷新后台任务（每 2 小时一次）。"""
        if not self.s.xiaomi_pass_token:
            logger.warning("xiaomi_pass_token 未配置，跳过自动刷新")
            return
        if self._xiaomi_token_refresh_task and not self._xiaomi_token_refresh_task.done():
            return
        self._xiaomi_token_refresh_task = asyncio.create_task(self._xiaomi_token_refresh_loop())
        logger.info("xiaomi token auto-refresh started (interval=2h)")

    async def _xiaomi_token_refresh_loop(self) -> None:
        """后台循环：每 2 小时刷新一次 service_token；失败每 60 秒重试（最多 6 次）。

        mina 换票链路（step3）偶发直接 401（实测同一 token 下 5 次里 1-2 次），
        2 小时才重试一次会连续错过、旧 service_token 过期后 TTS 播报全线 401。
        """
        while True:
            for attempt in range(6):
                try:
                    await self._refresh_xiaomi_token()
                    break
                except Exception as e:
                    logger.warning("xiaomi token refresh failed (attempt %d/6): %s",
                                   attempt + 1, e)
                    await asyncio.sleep(60)
            await asyncio.sleep(2 * 3600)

    async def _refresh_xiaomi_token(self) -> None:
        """用 pass_token 刷新 service_token，热更新到 settings（无需重启）。"""
        import base64
        import hashlib

        device_id = uuid.uuid4().hex[:12]
        user_agent = (
            f"Android-7.1.1-1.0.0-ONEPLUS A3010-136-{device_id} "
            f"APP/xiaomi.smarthome APPV/62830"
        )

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Step 1: serviceLogin
            r1 = await client.get(
                "https://account.xiaomi.com/pass/serviceLogin",
                params={"sid": "micoapi", "_json": "true"},
                cookies={
                    "passToken": self.s.xiaomi_pass_token,
                    "userId": self.s.xiaomi_user_id,
                    "deviceId": device_id,
                    "sdkVersion": "3.8.6",
                },
                headers={"User-Agent": user_agent},
            )
            raw = r1.text
            if raw.startswith("&&&START&&&"):
                raw = raw.replace("&&&START&&&", "", 1)
            data = json.loads(raw)
            if data.get("code") != 0:
                raise RuntimeError(f"serviceLogin failed: code={data.get('code')}")

            location = data["location"]
            ssecurity = data["ssecurity"]
            nonce = str(data["nonce"])

            # Step 2: clientSign
            client_sign = base64.b64encode(
                hashlib.sha1(f"nonce={nonce}&{ssecurity}".encode()).digest()
            ).decode()

            # Step 3: 跟随 location 拿 serviceToken
            sep = "&" if "?" in location else "?"
            sts_url = f"{location}{sep}_userIdNeedEncrypt=true&clientSign={client_sign}"
            await client.get(sts_url, headers={"User-Agent": user_agent})

            # 从 cookies 提取 serviceToken
            # httpx cookies.get/items 遇跨域同名 cookie（登录链路会同时落两枚 cUserId）
            # 抛 CookieConflict，改为直接过滤 cookie jar，绕开按名查找的冲突。
            try:
                service_token = client.cookies.get("serviceToken")
            except httpx.CookieConflict:
                service_token = None
            if not service_token:
                for cookie in client.cookies.jar:
                    if cookie.name == "serviceToken" and cookie.value:
                        service_token = cookie.value
                        break

            if not service_token:
                raise RuntimeError("failed to extract serviceToken from cookies")

            # 热更新到 settings（无需重启容器）
            self.s.xiaomi_service_token = service_token
            self.s.xiaomi_ssecurity = ssecurity
            logger.info("xiaomi service_token refreshed (len=%d)", len(service_token))

    async def resolve_play_method(self, did: str) -> str:
        """按 did 反查 devices.json 的该设备播放方式（xiaomi_play），缺省 url。

        存在理由：TTS 队列/直连腿曾硬编码 method="music"，绕过逐设备配置，
        使 Emily 房间改 url 的止血对队列路径无效（player_play_music 自激复发）。
        """
        try:
            p = Path(self.s.data_dir) / "devices.json"
            if p.stat().st_mtime != self._devices_mtime:
                self._play_method_cache.clear()
                self._devices_mtime = p.stat().st_mtime
        except OSError:
            pass
        if did in self._play_method_cache:
            return self._play_method_cache[did]
        method = "url"
        try:
            p = Path(self.s.data_dir) / "devices.json"
            devs = json.loads(p.read_text(encoding="utf-8"))
            for dev in devs.values():
                if dev.get("type") != "xiaomi" or not dev.get("ha_player_entity"):
                    continue
                aid = await self.get_xiaoai_id(dev["ha_player_entity"])
                if not aid:
                    continue
                m = dev.get("xiaomi_play") or "url"
                self._play_method_cache[aid] = m
                if aid == did:
                    method = m
            logger.info("play_method cache rebuilt: %d devices", len(self._play_method_cache))
        except Exception as e:
            logger.warning("resolve_play_method(%s) failed: %s", did[:8], e)
        self._play_method_cache.setdefault(did, method)
        return self._play_method_cache[did]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.s.ha_token}", "Content-Type": "application/json"}

    async def tts_speak(self, message: str, entity_id: str = "tts.doubao_tts") -> bool:
        async with self._play_lock("ha:" + str(entity_id)):
            return await self._tts_speak_inner(message, entity_id)

    async def _tts_speak_inner(self, message: str, entity_id: str = "tts.doubao_tts") -> bool:
        if not self.s.ha_token:
            logger.info("HA token 未配置，跳过小爱发声")
            return False
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.post(
                    f"{self.s.ha_url.rstrip('/')}/api/services/tts/speak",
                    headers=self._headers(),
                    json={"entity_id": entity_id, "message": message},
                )
                return r.status_code == 200
        except Exception as e:
            logger.warning("HA tts_speak failed: %s", e)
            return False

    async def get_state(self, entity_id: str) -> dict | None:
        if not self.s.ha_token:
            return None
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(f"{self.s.ha_url.rstrip('/')}/api/states/{entity_id}", headers=self._headers())
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            logger.warning("HA get_state failed: %s", e)
        return None

    async def get_states(self) -> list:
        """获取 HA 所有实体状态（用于设备模糊匹配）。"""
        if not self.s.ha_token:
            return []
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(f"{self.s.ha_url.rstrip('/')}/api/states", headers=self._headers())
                if r.status_code == 200:
                    return r.json()
        except Exception as e:
            logger.warning("HA get_states failed: %s", e)
        return []

    async def call_service(self, domain: str, service: str, data: dict | None = None) -> str:
        """调用 HA 服务（控制设备）。返回简短结果字符串。"""
        if not self.s.ha_token:
            return "HA token 未配置"
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{self.s.ha_url.rstrip('/')}/api/services/{domain}/{service}",
                    headers=self._headers(),
                    json=data or {},
                )
                if r.status_code == 200:
                    return f"ok ({r.status_code})"
                return f"http {r.status_code}: {r.text[:200]}"
        except Exception as e:
            logger.warning("HA call_service %s.%s failed: %s", domain, service, e)
            return f"error: {e}"

    async def call_service_strict(self, domain: str, service: str, data: dict | None = None) -> dict:
        """调用 HA 服务，失败时抛类型化异常。成功返回响应 JSON。"""
        if not self.s.ha_token:
            raise HAError("HA token 未配置")
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{self.s.ha_url.rstrip('/')}/api/services/{domain}/{service}",
                    headers=self._headers(),
                    json=data or {},
                )
                if r.status_code == 200:
                    return r.json() if r.content else {}
                if r.status_code == 404:
                    raise HAEntityNotFound(f"实体或服务不存在: {domain}.{service}", status_code=404)
                if r.status_code in (401, 403):
                    raise HAUnauthorized(f"HA 鉴权失败 ({r.status_code})", status_code=r.status_code)
                raise HAError(f"HTTP {r.status_code}: {r.text[:200]}", status_code=r.status_code)
        except httpx.TimeoutException:
            raise HATimeout(f"HA 调用超时: {domain}.{service}")
        except HAError:
            raise
        except Exception as e:
            raise HAError(f"HA 调用异常: {e}")

    async def tts_play_url(self, url: str, entity_id: str, repeat_off: bool = True) -> str:
        async with self._play_lock("ha:" + str(entity_id)):
            return await self._tts_play_url_inner(url, entity_id, repeat_off)

    async def _tts_play_url_inner(self, url: str, entity_id: str, repeat_off: bool = True) -> str:
        """tts_speak 模式（URL 兜底）：用我们合成的 edge-tts mp3 URL 在 media_player 播放。"""
        if not self.s.ha_token or not entity_id:
            return "no token/entity"
        try:
            if repeat_off:
                # 注意：HA media_player.repeat_set 的 repeat 必须是布尔值，
                # 传字符串 "off" 会被忽略；且很多小爱 media_player 根本不支持该服务，
                # 因此小爱会把远程 mp3 当成「网络电台流」单曲循环。见 schedule_media_stop。
                await self.call_service("media_player", "repeat_set",
                                        {"entity_id": entity_id, "repeat": False})
            return await self.call_service(
                "media_player", "play_media",
                {"entity_id": entity_id, "media_content_id": url, "media_content_type": "music"},
            )
        except Exception as e:
            logger.warning("HA tts_play_url failed: %s", e)
            return f"error: {e}"

    async def intelligent_speaker(self, entity_id: str, text: str, execute: bool = False) -> str:
        """小爱音箱直读：调用 xiaomi_miot.intelligent_speaker 让音箱直接朗读文本。
        不需要管家合成音频，用音箱默认音色，延迟低，无循环问题。"""
        if not self.s.ha_token or not entity_id:
            return "no token/entity"
        try:
            return await self.call_service(
                "xiaomi_miot", "intelligent_speaker",
                {"entity_id": entity_id, "text": text, "execute": execute},
            )
        except Exception as e:
            logger.warning("HA intelligent_speaker failed: %s", e)
            return f"error: {e}"

        """通过 HA notify 平台让小爱音箱播报文本（如 xiaomi_miot 的 execute_text_directive）。"""
        if not self.s.ha_token:
            return "HA token 未配置"
        data = {"message": message}
        if entity_id:
            data["entity_id"] = entity_id
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{self.s.ha_url.rstrip('/')}/api/services/notify/send_message",
                    headers=self._headers(),
                    json=data,
                )
                if r.status_code == 200:
                    return "ok"
                return f"http {r.status_code}: {r.text[:200]}"
        except Exception as e:
            logger.warning("HA notify_message failed: %s", e)
            return f"error: {e}"

    async def notify_message(self, message: str, entity_id: str | None = None) -> str:
        async with self._play_lock("ha:" + str(entity_id)):
            return await self._notify_message_inner(message, entity_id)

    async def _notify_message_inner(self, message: str, entity_id: str | None = None) -> str:
        """推送文本到指定 HA entity（小爱音箱播报）。返回 "ok" 或错误描述。"""
        if not entity_id:
            return "no_entity"
        try:
            res = await self.intelligent_speaker(entity_id, message, execute=True)
            # intelligent_speaker returns "ok (200)" on success, error strings on failure
            if res and isinstance(res, str) and res.startswith("ok"):
                return "ok"
            logger.warning("HA notify_message failed: %s", res)
            return str(res)
        except Exception as e:
            logger.warning("HA notify_message failed: %s", e)
            return str(e)

    async def get_xiaoai_id(self, entity_id: str) -> str | None:
        """从小爱 media_player 实体属性取 xiaoai_id（mina 设备 ID），用于直连接口。"""
        if not entity_id:
            return None
        st = await self.get_state(entity_id)
        if not st:
            return None
        return (st.get("attributes") or {}).get("xiaoai_id")

    async def _xiaomi_ubus(self, device_id: str, method: str, message: dict, path: str = "mediaplayer") -> dict:
        """底层直连 mina ubus 调用（小爱云端）。"""
        if not (self.s.xiaomi_service_token and self.s.xiaomi_user_id):
            return {"code": -1, "message": "xiaomi token 未配置"}
        data = {
            "deviceId": device_id,
            "message": json.dumps(message, ensure_ascii=False),
            "method": method,
            "path": path,
            "requestId": "app_ios_" + uuid.uuid4().hex[:30],
        }
        headers = {
            "X-XIAOMI-PROTOCAL-FLAG-CLI": "PROTOCAL-HTTP2",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "MISoundBox/2.0.0 android/10",
        }
        cookies = {
            "userId": str(self.s.xiaomi_user_id),
            "serviceToken": self.s.xiaomi_service_token,
            "yetAnotherServiceToken": self.s.xiaomi_service_token,
            "locale": "zh_CN",
            "channel": "MI_APP_STORE",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    "https://api2.mina.mi.com/remote/ubus",
                    headers=headers, cookies=cookies, data=data,
                )
                # HTTP 层失败⛔ 当业务成功：小米云的错误体常常自己就是 JSON，
                # 直接 `return r.json()` 会把厂商 `code` 命名空间的东西透给调用方，
                # 而"凭据坏了／网关挂了"这一层事实被丢掉。失败信封用本文件已有的形状。
                if r.status_code >= 400:
                    return {"code": r.status_code, "message": r.text[:200]}
                try:
                    return r.json()
                except Exception:
                    return {"code": r.status_code, "message": r.text[:200]}
        except Exception as e:
            logger.warning("_xiaomi_ubus %s failed: %s", method, e)
            return {"code": -1, "message": str(e)}

    def _play_lock(self, key: str) -> asyncio.Lock:
        """per-device 独占锁：序列化同一物理设备的发声指令下发（v2.5 收口，零旁路挂在驱动层）。"""
        locks = getattr(self, "_play_locks", None)
        if locks is None:
            locks = {}
            self._play_locks = locks
        lk = locks.get(key)
        if lk is None:
            lk = asyncio.Lock()
            locks[key] = lk
        return lk

    async def play_xiaomi_url_once(self, device_id: str, url: str, method: str = "url") -> dict:
        async with self._play_lock("xiaomi:" + str(device_id)):
            return await self._play_xiaomi_url_once(device_id, url, method)

    async def _play_xiaomi_url_once(self, device_id: str, url: str, method: str = "url") -> dict:
        """直连小爱云端播放远程 mp3（一次性，不循环）。

        method:
          "url"   -> player_play_url（一次性投射，适用 LX06 等大多数固件）
          "music" -> player_play_music（标准音乐 payload，适用红米触屏 X08A 等只认此接口
                     的固件；该类固件播单条会单曲循环，发完会补 player_set_loop 0 关闭循环）

        复用 HA xiaomi_miot 已登录的小米云端 serviceToken。
        """
        if not (self.s.xiaomi_service_token and self.s.xiaomi_user_id):
            return {"code": -1, "message": "xiaomi token 未配置"}

        if method == "music":
            audio_id = "1582971365183456177"
            music = {
                "payload": {
                    "audio_type": "MUSIC",
                    "audio_items": [{
                        "item_id": {
                            "audio_id": audio_id,
                            "cp": {"album_id": "-1", "episode_index": 0, "id": "355454500", "name": "xiaowei"},
                        },
                        "stream": {"url": url},
                    }],
                    "list_params": {
                        "listId": "-1", "loadmore_offset": 0,
                        "origin": "xiaowei", "type": "MUSIC",
                    },
                },
                "play_behavior": "REPLACE_ALL",
            }
            msg = {"startaudioid": audio_id, "music": json.dumps(music, ensure_ascii=False)}
            logger.info("play_xiaomi_url_once[music] device=%s url=%s", device_id, url)
            rj = await self._xiaomi_ubus(device_id, "player_play_music", msg)
            # 尝试关闭单曲循环（部分固件有效；X08A 实测无效，仅作辅助）
            # 只在播放真成功后补发：播放已经失败时再打一发等于对同一个刚返 4xx 的端点加一次流量。
            if str(rj.get("code")) == "0":
                await self._xiaomi_ubus(device_id, "player_set_loop", {"loop_type": 0})
            return rj

        # url 模式（不循环，LX06 等支持 player_play_url 的设备）
        try:
            play_type = int(self.s.xiaomi_play_type)
        except (ValueError, TypeError):
            play_type = 2
        msg = {"url": url, "type": play_type, "media": "app_ios"}
        logger.info("play_xiaomi_url_once[url] device=%s url=%s type=%s", device_id, url, play_type)
        return await self._xiaomi_ubus(device_id, "player_play_url", msg)

    async def _xiaomi_play_status(self, device_id: str):
        """返回 (position_ms, duration_ms, status)。X08A player_get_play_status 实测字段：
        info.play_song_detail.position / .duration / status（1=播, 2=停）。
        """
        rj = await self._xiaomi_ubus(device_id, "player_get_play_status", {})
        try:
            obj = json.loads((rj.get("data") or {}).get("info") or "{}")
            detail = obj.get("play_song_detail") or {}
            return detail.get("position", 0), detail.get("duration", 0), obj.get("status")
        except Exception:
            return 0, 0, None

    async def _xiaomi_stop_playback(self, device_id: str) -> dict:
        """player_play_operation stop：X08A 真正有效的停止通道（实测即时生效）。

        注意：交接单曾测得 player_pause / player_stop 为空操作——那是因为方法名不对；
        X08A 上正确的停止指令是 player_play_operation {"action":"stop","media":"app_ios"}，
        播放中调用后 ~0.7s 内 status 由 1 变 2 且 position 冻结（实测验证）。
        """
        return await self._xiaomi_ubus(device_id, "player_play_operation", {"action": "stop", "media": "app_ios"})

    async def _xiaomi_clear_queue(self, device_id: str) -> dict:
        """清空播放队列（REPLACE_ALL + 空列表）。保留作兜底/排查用。

        交接单曾以此作为唯一停止手段；v2 改用 player_play_operation stop（更干净：
        不清队列、position 冻结）。仅在 operation stop 异常时的最终兜底使用。
        """
        audio_id = "1582971365183456177"
        music = {
            "payload": {
                "audio_type": "MUSIC",
                "audio_items": [],
                "list_params": {"listId": "-1", "loadmore_offset": 0, "origin": "xiaowei", "type": "MUSIC"},
            },
            "play_behavior": "REPLACE_ALL",
        }
        msg = {"startaudioid": audio_id, "music": json.dumps(music, ensure_ascii=False)}
        return await self._xiaomi_ubus(device_id, "player_play_music", msg)

    def schedule_xiaomi_stop(self, device_id: str, audio_secs: float) -> None:
        """music 模式精确停止（X08A 单曲循环专用，v2）。

        停止通道：player_play_operation stop（实测即时有效，~0.7s 内 status→2）。
        提前量：XIAOMI_STOP_LEAD_MS=800ms（≈ 实测云端延迟 700ms + 余量），在
        position >= duration - 800ms 时发出，stop 到达设备时恰好播完（截断的只是
        edge-tts 句尾静音，人耳无感），无循环、无截断、无重复尾。

        会话管理：同一设备已有 watch 任务时先取消（新播放的 REPLACE_ALL 会替换队列，
        防止旧 watch 在新播放期间误发 stop）。首次未观测到播放必须 continue 等待
        （player_play_music 异步生效的过渡态），否则会提前退出导致永不停止。

        fire-and-forget。
        """
        if not device_id:
            return

        # 会话管理：取消本设备上一个 watch 任务（防并发误停）
        old = self._xiaomi_watch_tasks.get(device_id)
        if old and not old.done():
            old.cancel()
            logger.info("xiaomi watch cancelled for %s (new playback)", device_id)

        async def _watch() -> None:
            prev = 0
            seen = False
            playing_seen = False
            expected_ms = int(audio_secs * 1000)
            for _ in range(900):  # 最多约 90s
                await asyncio.sleep(0.1)
                try:
                    pos, dur, status = await self._xiaomi_play_status(device_id)
                except Exception:
                    warn_throttled(logger, "ha.watch_status", "watch 读播放状态失败（该轮跳过，轮询不中断）")
                    continue
                if status is None:
                    continue
                if status == 1:  # 播放中
                    playing_seen = True
                    # 已知时长时，在播完前 LEAD ms 发 stop（edge-tts 句尾有静音，截断无感），
                    # 避免回环重复。注意：短音频（dur < LEAD）时 dur-LEAD 为负数，
                    # 必须等 pos>0 后再判断，否则 pos=0 就会误触发 stop。
                    effective_dur = dur
                    if dur < expected_ms * 0.5:
                        effective_dur = expected_ms
                    if effective_dur and pos > 0 and pos >= effective_dur - XIAOMI_STOP_LEAD_MS:
                        rj = await self._xiaomi_stop_playback(device_id)
                        logger.info("xiaomi stop@end(pos>=eff_dur-%d) pos=%d dev_dur=%d exp_ms=%d -> %s",
                                    XIAOMI_STOP_LEAD_MS, pos, dur, expected_ms, rj.get("code"))
                        return
                    if seen and pos < prev - 300:  # position 突降 = 循环回绕 -> 立即停
                        rj = await self._xiaomi_stop_playback(device_id)
                        logger.info("xiaomi stop@loop pos=%d prev=%d -> %s", pos, prev, rj.get("code"))
                        return
                    if pos > 300:
                        seen = True
                    prev = pos
                else:
                    # 未播放中：若从未观测到播放，说明设备尚未开始（player_play_music 异步生效中），
                    # 继续等待，切勿提前退出；否则一旦观测过播放又回到停止，才算真的停了。
                    if playing_seen:
                        return
            # 兜底：超时仍在播则强制 stop（优先 operation stop，失败再清队列）
            try:
                rj = await self._xiaomi_stop_playback(device_id)
                logger.info("xiaomi stop@timeout (op stop) -> %s code=%s", device_id, rj.get("code"))
                if rj.get("code") != 0:
                    await self._xiaomi_clear_queue(device_id)
                    logger.info("xiaomi stop@timeout (clear_queue fallback) -> %s", device_id)
            except Exception as e:
                logger.warning("xiaomi stop failed for %s: %s", device_id, e)

        try:
            task = asyncio.create_task(_watch())
            self._xiaomi_watch_tasks[device_id] = task
        except Exception as e:
            # P2-9 ②类（批42）：没武装成＝「播完该停」静默消失，必须响一声
            logger.warning("xiaomi stop watch not armed for %s: %s", device_id, e)

    # ── 通用 media_player 停止（不需要小米 token，走 HA） ──────────
    # 完全不需要自己管 token！HA 的 xiaomi_miot 集成自己会处理 token 刷新。
    _media_stop_tasks: dict[str, asyncio.Task] = {}

    async def schedule_media_stop(self, entity_id: str, audio_secs: float) -> None:
        """轮询 position/duration，播完前连续发 pause 防止循环。"""
        if not entity_id:
            return

        # 取消之前的任务
        old = self._media_stop_tasks.get(entity_id)
        if old and not old.done():
            old.cancel()

        async def _watch():
            start_time = asyncio.get_running_loop().time()
            prev_pos = 0
            seen_playing = False

            for _ in range(120):  # 最多 60 秒
                await asyncio.sleep(0.2)
                try:
                    st = await self.get_state(entity_id)
                except Exception:
                    warn_throttled(logger, "ha.watch_state", "watch 读媒体状态失败（该轮跳过，轮询不中断）")
                    continue
                if not st:
                    continue

                state = st.get("state")
                attrs = st.get("attributes", {})
                pos = attrs.get("media_position") or 0
                dur = attrs.get("media_duration") or 0

                if state == "playing":
                    seen_playing = True
                    # 播完前 0.5 秒开始连续 pause
                    if dur > 0 and pos >= dur - 0.5:
                        logger.info("stop@end on %s: pos=%.1f >= dur-0.5=%.1f", entity_id, pos, dur - 0.5)
                        for attempt in range(1, 6):
                            try:
                                await self.call_service("media_player", "media_pause", {"entity_id": entity_id})
                            except Exception as e:
                                # P2-9 ②类（批42）：pause 发不出去＝停不下来，⛔ 再无声
                                logger.warning("media_pause failed on %s (stop@end Attempt %d): %s", entity_id, attempt, e)
                            await asyncio.sleep(0.2)
                        return
                    # 循环回绕检测
                    if prev_pos > 0 and pos < prev_pos - 3:
                        logger.info("stop@loop on %s: pos=%.1f < prev=%.1f", entity_id, pos, prev_pos)
                        for attempt in range(1, 6):
                            try:
                                await self.call_service("media_player", "media_pause", {"entity_id": entity_id})
                            except Exception as e:
                                # P2-9 ②类（批42）：pause 发不出去＝停不下来，⛔ 再无声
                                logger.warning("media_pause failed on %s (stop@loop Attempt %d): %s", entity_id, attempt, e)
                            await asyncio.sleep(0.2)
                        return
                    if pos > 0:
                        prev_pos = pos

                # 兜底：超时强制停
                elapsed = asyncio.get_running_loop().time() - start_time
                if elapsed > audio_secs * 2 + 5:
                    if seen_playing:
                        logger.warning("stop@timeout on %s after %.1fs", entity_id, elapsed)
                        for attempt in range(1, 6):
                            try:
                                await self.call_service("media_player", "media_pause", {"entity_id": entity_id})
                            except Exception as e:
                                # P2-9 ②类（批42）：pause 发不出去＝停不下来，⛔ 再无声
                                logger.warning("media_pause failed on %s (stop@timeout Attempt %d): %s", entity_id, attempt, e)
                            await asyncio.sleep(0.2)
                    return

        # 创建并启动 watch task（在 _watch 函数外面！）
        self._media_stop_tasks[entity_id] = asyncio.create_task(_watch())
