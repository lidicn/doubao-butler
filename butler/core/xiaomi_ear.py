"""小爱耳朵：通过 HA websocket 直接订阅 conversation 传感器，不经过 Node-RED。

职责：
1. 连接 HA websocket，订阅 state_changed 事件
2. 过滤出各小爱音箱的 conversation 传感器（device.ha_sensor）
3. 匹配唤醒词＝点名才答：命中唤醒词才路由到角色；没说唤醒词的整句⛔ 交给对话引擎
   （DCD 2026-10-10 件五 乙。角色模式是用户自己「打开某角色」起的续话，属已点名。）
4. 调用 dialog.on_wakeup 完成对话+播报

与 NR 的关系：NR 不再需要 function 节点做唤醒词匹配，也不需要转发——butler 自己订阅。
"""
from __future__ import annotations

import asyncio
import re
import time

import aiohttp

from butler.logging_setup import get_logger, warn_throttled

logger = get_logger("butler.xiaomi_ear")

# 电子书口令表（批194 A9）：五族关键词是**唯一**词源，⛔ 在匹配函数里再硬编码一份。
# 报告 §六 T14 点名的两枚裸方位词（往后／往前）已摘出表——摘的是关键词，判据形制另见 `_match_audiobook_cmd`。
AUDIOBOOK_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stop", ("停止播放", "别播了", "不要播了", "停下", "闭嘴", "别念了", "不要念了")),
    ("pause", ("暂停播放", "暂停", "先别播", "等一下", "等会儿")),
    ("resume", ("继续播放", "继续", "接着播", "接着听", "继续念")),
    ("next", ("下一章", "下一节", "跳过", "换一章")),
    ("prev", ("上一章", "上一节", "回去", "重听")),
)

_AB_BOUNDARY = re.compile(r"[，,。.!！?？、；;：:～~\s]+")
_AB_MODAL = "吧啊呀呢嘛哦哈"  # 尾随语气词＝⛔ 宾语（「暂停吧」仍是暂停；「暂停一下我去拿东西」不是）


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
                # 批48：握手帧读取加 wait_for 上界，HA 只回 ping 不回帧即超时重连
                msg = await asyncio.wait_for(ws.receive_json(), timeout=15)
                if msg.get("type") == "auth_required":
                    await ws.send_json({"type": "auth", "access_token": s.ha_token})
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=15)
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

    # 回声抑制：butler 播报后该设备的 ear 事件在窗口内忽略
    # 值＝**过期时刻**（⛔ 盖戳时刻）：写侧按本条播报的估算时长把窗口推到播放结束。
    # 字典留在类体＝进程级共享（`dialog.speak_as_role` 手上只有类引用，拿不到 ear 实例），
    # 因此写侧统一走 note_speak——过期键的清扫也挂在写侧（合并行 53：原形状全仓无清理点）。
    _echo_cooldown: dict = {}  # device_id -> expires_at
    ECHO_COOLDOWN_S = 15.0     # 窗口下界：合成＋下发的固定缓冲（沿用原硬编码阈值，数值未动）
    _role_mode: dict = {}  # device_id -> {"role_id": str, "expires_at": float}
    ROLE_MODE_TIMEOUT = 90  # 角色模式90秒无对话自动退出
    # 通用退出词：⛔ 依赖名册，空名册也必须放行（批194 A10 门 test_03）
    ROLE_EXIT_WORDS = ("退出", "退下", "没事了", "好了退下")

    @classmethod
    def note_speak(cls, device_id: str, hold_s: float = 0.0) -> None:
        """登记一次播报的回声窗口：过期时刻 = now + max(下界, 本条文本估算时长)。

        max() 语义与 `dialog.suppress_echo` 同形：后一条短播报⛔ 把正在播的长窗口拉回。
        """
        now = time.time()
        until = now + max(cls.ECHO_COOLDOWN_S, float(hold_s))
        cls._echo_cooldown[device_id] = max(cls._echo_cooldown.get(device_id, 0.0), until)
        for stale in [k for k, v in cls._echo_cooldown.items() if v <= now]:
            del cls._echo_cooldown[stale]

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

        # 回声抑制：该设备仍在 note_speak 登记的窗口内＝自己听自己，忽略（下界见 ECHO_COOLDOWN_S）
        expiry = self._echo_cooldown.get(dev.id, 0.0)
        if time.time() < expiry:
            logger.info("ear echo-suppressed [%s] text=%s (%.1fs left)", dev.id, text[:40],
                        expiry - time.time())
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
            if any(kw in text for kw in self._build_exit_keywords()):
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

        # 2. 没说唤醒词＝没点名：⛔ 接话（DCD 2026-10-10 件五 乙）。
        #    dr 仍取＝只把「本来会被答」的那些记下来，⛔ 扩大留痕分母。
        dr = self.rt.dialog.match_role_by_room(room)
        if dr and getattr(dr, "scope", "") == "private":
            logger.info("ear mention-suppressed [%s|%s] text=%s", dev.id, room, text[:40])

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

    def _build_exit_keywords(self) -> list[str]:
        """退出词表＝通用词＋按名册生成的「关闭{角色名/唤醒词}」。

        批194 A10：原表写死「关闭贾维斯/关闭凯撒/关闭露娜」，这三枚名在 `data/roles/` 里 0 命中，
        而 `_match_open_role` 让 6 个真实角色全能进（「打开小理」）⇒ 能进不能退。
        源与 `_match_open_role` 同为 `self.rt.roles.all()`，⛔ 在此另起一份角色名单。
        """
        words = list(self.ROLE_EXIT_WORDS)
        for role in self.rt.roles.all():
            names = [getattr(role, "name", "")] + list(getattr(role, "wake_words", []) or [])
            for name in names:
                if name:
                    words.append(f"关闭{name}")
        return list(dict.fromkeys(words))

    def _match_audiobook_cmd(self, text: str) -> str | None:
        """电子书口令＝**整段相等**：按标点/空白切段，某段剥掉尾随语气词后整段等于关键词才算。

        ⛔ 再用子串命中（第二轮小爱审计 A9）：旧形把「继续开灯」读成 resume（想开灯，电子书被
        恢复了）、「往后翻」「往前」读成 next/prev，且这一支在播放中**优先于唤醒词**，被它
        `return` 掉的那句话管家全程不答＝「答非所问」。呼格形态（「豆包，暂停」）仍算命中＝⛔ 造新病灶。
        命中归属⛔ 由表序决定：一段只可能整段等于一枚关键词（五族关键词互不相等）。
        """
        for seg in _AB_BOUNDARY.split((text or "").strip()):
            core = seg.strip().rstrip(_AB_MODAL)
            if not core:
                continue
            for action, words in AUDIOBOOK_COMMANDS:
                if core in words:
                    return action
        return None
