"""小爱耳朵：通过 HA websocket 直接订阅 conversation 传感器，不经过 Node-RED。

职责：
1. 连接 HA websocket，订阅 state_changed 事件
2. 过滤出各小爱音箱的 conversation 传感器（device.ha_sensor）
3. 匹配唤醒词 → 路由到对应角色；没说唤醒词时仅私人助理房间按房间默认角色响应
4. 调用 dialog.on_wakeup 完成对话+播报

与 NR 的关系：NR 不再需要 function 节点做唤醒词匹配，也不需要转发——butler 自己订阅。
"""
from __future__ import annotations

import asyncio
import time

import aiohttp

from butler.logging_setup import get_logger, warn_throttled

logger = get_logger("butler.xiaomi_ear")


class XiaomiEar:
    def __init__(self, runtime):
        self.rt = runtime
        self._task = None
        self._running = False

    def start(self):
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="xiaomi_ear")
        logger.info("xiaomi_ear started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _run(self):
        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("xiaomi_ear disconnected: %s, retry in 5s", e)
                await asyncio.sleep(5)

    async def _connect(self):
        s = self.rt.settings
        ws_url = s.ha_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"

        # 建立 ha_sensor(entity_id) -> device 映射
        sensor_map: dict[str, object] = {}
        for dev in self.rt.devices.all():
            sensor = getattr(dev, "ha_sensor", "") or ""
            if sensor:
                sensor_map[sensor] = dev

        if not sensor_map:
            logger.warning("xiaomi_ear: no devices with ha_sensor configured, sleep 30s")
            await asyncio.sleep(30)
            return

        logger.info("xiaomi_ear connecting to %s (%d conversation sensors: %s)",
                    ws_url, len(sensor_map), list(sensor_map.keys()))

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, heartbeat=30) as ws:
                # ---- HA websocket 认证 ----
                msg = await ws.receive_json()
                if msg.get("type") == "auth_required":
                    await ws.send_json({"type": "auth", "access_token": s.ha_token})
                    msg = await ws.receive_json()
                    if msg.get("type") != "auth_ok":
                        raise RuntimeError(f"HA websocket auth failed: {msg}")
                    logger.info("xiaomi_ear HA auth ok")

                # ---- 订阅 state_changed ----
                await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
                logger.info("xiaomi_ear subscribed state_changed, listening...")

                async for msg in ws:
                    try:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = msg.json()
                            except Exception:
                                warn_throttled(logger, "ear.frame_json", "ear 帧解析失败（连接不断，继续收下一帧）")
                                continue
                            if not isinstance(data, dict):
                                continue
                            if data.get("type") == "event":
                                ev = data.get("event", {})
                                if isinstance(ev, dict):
                                    await self._handle_event(ev, sensor_map)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                    except Exception:
                        warn_throttled(logger, "ear.handle_event", "ear handle_event 抛错（该帧丢弃，订阅与监听不断）")
                        continue

    # 回声抑制：butler 播报后 N 秒内忽略同设备的 ear 事件
    _echo_cooldown: dict = {}  # device_id -> timestamp
    _role_mode: dict = {}  # device_id -> {"role_id": str, "expires_at": float}
    ROLE_MODE_TIMEOUT = 90  # 角色模式90秒无对话自动退出

    async def _handle_event(self, event: dict, sensor_map: dict):
        data = event.get("data", {})
        entity_id = data.get("entity_id", "")
        dev = sensor_map.get(entity_id)
        if not dev:
            return

        new_state = data.get("new_state")
        if not new_state:
            return

        text = (new_state.get("state") or "").strip()
        if not text or text in ("unknown", "unavailable"):
            return

        # v2.4.1: filter out system-generated prompts written by external automation
        # (Node-RED / AF / xiaomi speaker itself writes 'AI生成：...' to conversation sensor)
        if text.startswith("AI生成：") or text.startswith("AI生成:"):
            logger.info("ear filtered system-prompt [%s] text=%s", dev.id, text[:60])
            return

        # 回声抑制：该设备最近 6 秒内 butler 刚播报过，忽略（防止自己听自己）
        last_speak = self._echo_cooldown.get(dev.id, 0)
        if time.time() - last_speak < 15.0:
            logger.info("ear echo-suppressed [%s] text=%s (%.1fs ago)", dev.id, text[:40], time.time()-last_speak)
            return

        room = getattr(dev, "room", "") or ""
        logger.info("ear [%s|%s] %s", entity_id, room, text[:80])

        # === 电子书语音控制（播放中时响应停止/暂停/继续/上下章）===
        ab = getattr(self.rt, "audiobook", None)
        if ab and ab.state.playing:
            action = self._match_audiobook_cmd(text)
            if action:
                logger.info("ear audiobook cmd [%s] %s", dev.id, action)
                if action == "stop":
                    await ab.stop()
                elif action == "pause":
                    await ab.pause()
                elif action == "resume":
                    await ab.resume()
                elif action == "next":
                    await ab.next_chapter()
                elif action == "prev":
                    await ab.prev_chapter()
                return

        # === 角色模式（连续对话）===
        # 用户说"打开贾维斯"后进入角色模式，后续90秒内所有对话直接由该角色处理
        mode = self._role_mode.get(dev.id)
        now = time.time()
        if mode and now < mode["expires_at"]:
            # 检查是否退出角色模式
            if any(kw in text for kw in ["关闭贾维斯", "关闭凯撒", "关闭露娜",
                                           "退出", "退下", "没事了", "好了退下"]):
                del self._role_mode[dev.id]
                logger.info("ear role-mode exited [%s]", dev.id)
                return
            # 刷新超时，直接交给当前角色处理
            mode["expires_at"] = now + self.ROLE_MODE_TIMEOUT
            role_id = mode["role_id"]
            wake_btn = getattr(dev, "wake_button", "") or ""
            if wake_btn and self.rt.ha:
                try:
                    await self.rt.ha.call_service("button", "press", {"entity_id": wake_btn})
                except Exception as e:
                    # P2-9 ②类（批42 组5）：按钮按不下＝HA 侧硬故障，⛔ 无声＝把它伪装成「没听见」
                    logger.warning("ear role-mode wake-button press failed [%s] %s: %s", dev.id, wake_btn, e)
            await self.rt.dialog.on_wakeup(role_id, room, text, source_device=dev.id, source="xiaoai")
            return
        elif mode:
            # 超时，清除
            del self._role_mode[dev.id]

        # === "打开{角色名}"指令：进入角色模式 ===
        open_role = self._match_open_role(text)
        if open_role:
            self._role_mode[dev.id] = {"role_id": open_role.id, "expires_at": now + self.ROLE_MODE_TIMEOUT}
            logger.info("ear role-mode entered [%s] -> %s (%ds)", dev.id, open_role.id, self.ROLE_MODE_TIMEOUT)
            # 不回复，等用户说后续内容（小爱已经回复"搞定"）
            return

        # 1. 匹配唤醒词（所有角色的 wake_words）
        role, cleaned = self.rt.dialog.match_role_by_text(text)
        if role:
            wake_btn = getattr(dev, "wake_button", "") or ""
            if wake_btn and self.rt.ha:
                try:
                    await self.rt.ha.call_service("button", "press",
                                                   {"entity_id": wake_btn})
                    logger.info("ear wake-button interrupt %s", wake_btn)
                except Exception as e:
                    logger.warning("ear wake-button failed: %s", e)
            await self.rt.dialog.on_wakeup(role.id, room, cleaned, source_device=dev.id, source="xiaoai")
            return

        # 2. 没说唤醒词：仅私人助理房间按房间默认角色响应
        dr = self.rt.dialog.match_role_by_room(room)
        if dr and getattr(dr, "scope", "") == "private":
            await self.rt.dialog.on_wakeup(dr.id, room, text, source="xiaoai")

    def _match_open_role(self, text: str):
        """匹配'打开贾维斯'/'打开凯撒'/'打开露娜'等指令，返回对应角色。"""
        import re
        for role in self.rt.roles.all():
            # 角色名（如"贾维斯"）和唤醒词都可以作为"打开XX"的目标
            names = [getattr(role, "name", "")] + list(getattr(role, "wake_words", []) or [])
            for name in names:
                if not name:
                    continue
                if re.search(rf"打开{re.escape(name)}", text) or text.strip() == f"打开{name}":
                    return role
        return None

    def _match_audiobook_cmd(self, text: str) -> str | None:
        """匹配电子书语音控制指令，返回 stop/pause/resume/next/prev。"""
        t = text.strip()
        # 停止
        if any(k in t for k in ["停止播放", "别播了", "不要播了", "停下", "闭嘴", "别念了", "不要念了"]):
            return "stop"
        # 暂停
        if any(k in t for k in ["暂停播放", "暂停", "先别播", "等一下", "等会儿"]):
            return "pause"
        # 继续
        if any(k in t for k in ["继续播放", "继续", "接着播", "接着听", "继续念"]):
            return "resume"
        # 下一章
        if any(k in t for k in ["下一章", "下一节", "往后", "跳过", "换一章"]):
            return "next"
        # 上一章
        if any(k in t for k in ["上一章", "上一节", "往前", "回去", "重听"]):
            return "prev"
        return None
