"""AF 主动询问语音环（MQTT）：`af/ask/pending` → 小爱念题 → 收口头回答 → `af/ask/answer`。

线协议逐字来自 AF 已上线代码（`af_mqtt_bridge.py` v1.28.3，2026-11-08 AF-PM 交接单）：

- pending：QoS1、retain=false；载荷 `{ask_id, room(HA area_id), prompt, source, priority,
  timeout_remaining}`。`retain` 会让小爱重连后重复问一遍 ⇒ ⛔ retained。
- answer：QoS1 建议、retain=false；载荷 `{ask_id, answer: "yes"|"no", raw_text}`。
  只允许两值；带参数的回答（如「开到 24 度」）整条拒收并留痕，⛔ 猜、⛔ 截断。
- 识别不出／静默 ⇒ **不发**（AF 侧超时兜底自己走，宁缺勿错发）；超过 `timeout_remaining`
  的晚到回答直接丢弃；同一 `ask_id` 只念一遍；同房间串行（先到先问，MVP ⛔ 并发）。

边界：本模块只做「念题 + 收回答 + 分类 + 发布会」，⛔ 触碰 AF 侧任何代码/主题命名。
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from butler.bus.topics import PUB_AF_ASK_ANSWER
from butler.logging_setup import get_logger
from butler.param import soft_num

logger = get_logger("butler.bus.af_ask")

#: 单条问句最长等多久（秒）——只是兜底上界；真正上界取载荷里的 `timeout_remaining`。
MAX_WAIT_S = 600.0
#: 同一 ask_id 的去重记忆窗（秒）。AF 重发/重连补发都在此窗口内被吃掉。
SEEN_TTL_S = 3600.0
#: prompt 长度上限（与收件箱 speak 通道同口径），超长截断只影响念出来的长度，不影响 ask_id。
MAX_PROMPT_LEN = 500
#: 回答原话长度上限（超过这个长度不可能是 MVP 的「要/不要」）
MAX_ANSWER_LEN = 20

_YES_TOKENS = ("要", "好", "是", "开", "行", "可以", "需要", "嗯", "对", "ok", "yes", "yeah")
_NO_TOKENS = ("不要", "不用", "不需要", "不行", "不是", "不", "否", "别", "关", "no", "n")
#: 带参数的回答：数字/中文数字/温度单位/百分比 ⇒ 整条拒收（MVP 只支持两值）。
_PARAM_RE = re.compile(r"[0-9０-９一二三四五六七八九十百千万]|度|℃|温度|百分|%|摄氏")
_PUNCT = "，。！？、,.!?;；:：~～ \t\r\n\"'“”‘’"


def classify_answer(text: str) -> tuple[str | None, str]:
    """把用户原话分类成 `("yes"|"no"|None, reason)`。

    reason 取值：empty / too_long / param / unclassified / ok。
    口径（AF 交接 §3.2 C5）：两值、⛔ 猜、⛔ 截断；带参数的回答明确拒收。
    """
    t = str(text or "").strip().strip(_PUNCT)
    if not t:
        return None, "empty"
    if len(t) > MAX_ANSWER_LEN:
        return None, "too_long"
    if _PARAM_RE.search(t):
        return None, "param"
    low = t.lower()
    # 先判否：「不要」里含「要」、「不行」里含「行」——顺序反了会把否定读成肯定。
    if any(tok in low for tok in _NO_TOKENS):
        return "no", "ok"
    if any(tok in low for tok in _YES_TOKENS):
        return "yes", "ok"
    return None, "unclassified"


@dataclass
class _Ask:
    ask_id: str
    room: str
    room_key: str
    prompt: str
    deadline: float
    done: asyncio.Event = field(default_factory=asyncio.Event)
    device_ids: list = field(default_factory=list)


class AfAskManager:
    """同房间串行 + ask_id 去重 + 超时静默的 ask 语音环。"""

    def __init__(self, rt):
        self.rt = rt
        self._seen: dict[str, float] = {}
        self._queues: dict[str, list[_Ask]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._active: dict[str, _Ask] = {}
        self._closed = False

    # ── 入口：MQTT pending ──────────────────────────────────────────
    async def handle_pending(self, payload) -> None:
        """收到 `af/ask/pending`：校验 → 去重 → 入房间队列（⛔ 在这里等答案，避免堵 MQTT 消费协程）。"""
        ask = self._validate(payload)
        if ask is None:
            return
        if self._is_duplicate(ask.ask_id):
            logger.info("AF_ASK_DEDUPED ask=%s（同一 ask_id 只念一遍）", ask.ask_id)
            return
        self._remember(ask.ask_id)
        self._enqueue(ask)

    def _validate(self, payload) -> _Ask | None:
        if not isinstance(payload, dict):
            logger.error("ADM_ERR_PAYLOAD_INVALID af/ask/pending 载荷非对象")
            return None
        ask_id = str(payload.get("ask_id") or "").strip()
        if not ask_id or len(ask_id) > 64:
            logger.error("ADM_ERR_PAYLOAD_INVALID af/ask/pending ask_id 缺失或超 64 字符")
            return None
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            logger.error("ADM_ERR_PAYLOAD_INVALID af/ask/pending prompt 为空 ask=%s", ask_id)
            return None
        room = str(payload.get("room") or "").strip()
        if not room:
            logger.error("ADM_ERR_PAYLOAD_INVALID af/ask/pending room 为空 ask=%s", ask_id)
            return None
        timeout = soft_num(payload.get("timeout_remaining"), "timeout_remaining",
                           default=None, integer=False)
        if timeout is None or timeout <= 0:
            logger.warning("AF_ASK_EXPIRED ask=%s timeout_remaining=%r，⛔ 念题（AF 已走兜底）",
                           ask_id, payload.get("timeout_remaining"))
            return None
        deadline = time.monotonic() + min(timeout, MAX_WAIT_S)
        return _Ask(ask_id=ask_id, room=room, room_key=self._room_key(room),
                    prompt=prompt[:MAX_PROMPT_LEN], deadline=deadline)

    def _is_duplicate(self, ask_id: str) -> bool:
        self._prune_seen()
        return ask_id in self._seen

    def _remember(self, ask_id: str) -> None:
        self._seen[ask_id] = time.monotonic()

    def _prune_seen(self) -> None:
        now = time.monotonic()
        expired = [k for k, ts in self._seen.items() if now - ts > SEEN_TTL_S]
        for k in expired:
            self._seen.pop(k, None)

    # ── 同房间串行队列 ─────────────────────────────────────────────
    def _enqueue(self, ask: _Ask) -> None:
        self._queues.setdefault(ask.room_key, []).append(ask)
        worker = self._workers.get(ask.room_key)
        if worker is None or worker.done():
            self._workers[ask.room_key] = asyncio.ensure_future(self._drain(ask.room_key))

    async def _drain(self, room_key: str) -> None:
        try:
            while True:
                queue = self._queues.get(room_key) or []
                if not queue:
                    self._queues.pop(room_key, None)
                    return
                await self._serve(queue.pop(0))
        finally:
            self._workers.pop(room_key, None)

    async def _serve(self, ask: _Ask) -> None:
        self._active[ask.ask_id] = ask
        try:
            spoken = await self._speak(ask)
            if spoken is None:
                logger.error("AF_ASK_UNSENT ask=%s room=%s 原因=没有设备出声（⛔ 继续等回答）",
                             ask.ask_id, ask.room)
                return
            # 念完之后把音箱切进聆听态（联调 §3.4：念完问题后用户零唤醒词直接答）
            await self._arm_wake(ask, spoken)
            remaining = ask.deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("AF_ASK_TIMEOUT ask=%s room=%s 念完已过期，⛔ 等回答", ask.ask_id, ask.room)
                return
            logger.info("AF_ASK_LISTENING ask=%s room=%s remaining=%.1fs",
                        ask.ask_id, ask.room, remaining)
            try:
                await asyncio.wait_for(ask.done.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                # 用户没回答：什么都不发，AF 侧超时兜底，本条问句作废。
                logger.info("AF_ASK_TIMEOUT ask=%s room=%s 无人回答，⛔ 发答案", ask.ask_id, ask.room)
        finally:
            await self._stop_media(ask)
            self._active.pop(ask.ask_id, None)
            if not ask.done.is_set():
                ask.done.set()

    async def _speak(self, ask: _Ask) -> dict | None:
        dialog = getattr(self.rt, "dialog", None)
        roles = getattr(self.rt, "roles", None)
        if dialog is None or roles is None:
            logger.error("AF_ASK_UNSENT ask=%s 原因=runtime 未就绪（dialog/roles 缺失）", ask.ask_id)
            return None
        role = roles.get("butler") if hasattr(roles, "get") else None
        if role is None:
            logger.error("AF_ASK_UNSENT ask=%s 原因=角色 butler 取不到", ask.ask_id)
            return None
        try:
            res = await dialog.speak_as_role(role, ask.prompt, ask.room, member="系统")
        except Exception as e:
            logger.error("AF_ASK_UNSENT ask=%s 原因=speak_as_role 抛错：%s", ask.ask_id, e)
            return None
        if not (res or {}).get("spoken"):
            logger.error("AF_ASK_UNSENT ask=%s 原因=无设备出声 回执=%.200s", ask.ask_id, res)
            return None
        logger.info("AF_ASK_SPOKEN ask=%s room=%s devices=%.200s",
                    ask.ask_id, ask.room, res.get("devices"))
        ask.device_ids = [d.get("device", "") for d in (res.get("devices") or [])
                          if isinstance(d, dict) and d.get("ok") and d.get("device")]
        return res

    @staticmethod
    def _speak_secs(prompt: str, dialog=None) -> float:
        """估算这条 prompt 念多久：优先用 dialog 的现成估算（约 3.1 字/秒）。"""
        est = getattr(dialog, "_est_speak_secs", None)
        if callable(est):
            try:
                return est(prompt)
            except Exception:
                pass
        return max(2.0, len(prompt or "") * 0.32)

    async def _stop_media(self, ask: _Ask) -> None:
        """问句结束（答完/超时/未播）后，把设备端这次 TTS 停掉并清出播放队列。

        2026-11-10 循环声根因：`player_play_operation stop` 只停当前这条，X08A 的播放队列里
        还留着这条 TTS；用户随后一句「打开」让小爱恢复上次媒体时，会把播报当音乐再放一遍。
        `ha.stop_xiaomi_playback` = stop + 关循环 + 清队列 三连，复播路径才断。
        """
        ha = getattr(self.rt, "ha", None)
        if ha is None:
            return
        for did in list(ask.device_ids or []):
            dev = self._device(did)
            try:
                entity = getattr(dev, "ha_player_entity", "") if dev is not None else ""
                if not entity:
                    logger.info("AF_ASK_MEDIA_STOP_SKIPPED ask=%s device=%s 原因=无播放实体",
                                ask.ask_id, did)
                    continue
                aid = await ha.get_xiaoai_id(entity)
                if aid:
                    await ha.stop_xiaomi_playback(aid)
                    logger.info("AF_ASK_MEDIA_STOPPED ask=%s device=%s", ask.ask_id, did)
                else:
                    await ha.call_service("media_player", "media_stop", {"entity_id": entity})
                    logger.info("AF_ASK_MEDIA_STOPPED(ha) ask=%s device=%s entity=%s",
                                ask.ask_id, did, entity)
            except Exception as e:
                logger.warning("AF_ASK_MEDIA_STOP_FAILED ask=%s device=%s err=%s", ask.ask_id, did, e)

    async def _arm_wake(self, ask: _Ask, spoken: dict) -> None:
        """念完后按 `wake_button` 把音箱切进聆听态（2026-11-10 联调 §3.4）。

        AF 的「主动」＝自动化触发小爱唤醒：念完问题必须落在聆听态，用户才能零唤醒词作答。
        ⛔ 静默降级：没有唤醒按钮／按压失败一律点名 `AF_ASK_WAKE_UNSUPPORTED`
        （回退方案须经出资人报 AF-PM 裁定）。
        """
        dialog = getattr(self.rt, "dialog", None)
        delay = self._speak_secs(ask.prompt, dialog) + 1.5
        remaining = ask.deadline - time.monotonic()
        if remaining <= 1.0:
            logger.warning("AF_ASK_WAKE_SKIPPED ask=%s 剩余 %.1fs，不足等念完再唤醒", ask.ask_id, remaining)
            return
        await asyncio.sleep(min(delay, max(0.0, remaining - 1.0)))
        pressed = 0
        for item in spoken.get("devices") or []:
            if not isinstance(item, dict) or not item.get("ok"):
                continue
            dev = self._device(item.get("device", ""))
            if dev is None:
                logger.warning("AF_ASK_WAKE_UNSUPPORTED ask=%s device=%s 原因=登记表里没有该设备",
                               ask.ask_id, item.get("device", ""))
                continue
            wake = getattr(dev, "wake_button", "") or ""
            if not wake:
                logger.warning("AF_ASK_WAKE_UNSUPPORTED ask=%s device=%s 原因=wake_button 未配置"
                               "（回退方案需报 AF-PM 裁定，⛔ 静默降级）", ask.ask_id, dev.id)
                continue
            ha = getattr(self.rt, "ha", None)
            if ha is None:
                logger.warning("AF_ASK_WAKE_UNSUPPORTED ask=%s device=%s 原因=HA 未就绪", ask.ask_id, dev.id)
                continue
            try:
                await ha.call_service("button", "press", {"entity_id": wake})
                pressed += 1
                logger.info("AF_ASK_WAKE_PRESSED ask=%s device=%s entity=%s", ask.ask_id, dev.id, wake)
            except Exception as e:
                logger.warning("AF_ASK_WAKE_UNSUPPORTED ask=%s device=%s 原因=按压失败：%s",
                               ask.ask_id, dev.id, e)
            self._clear_echo(dev.id)
        if pressed == 0:
            logger.warning("AF_ASK_WAKE_UNSUPPORTED ask=%s 整条没有一台按到唤醒按钮"
                           "（用户可能无法零唤醒词作答；回退方案需报 AF-PM 裁定）", ask.ask_id)
        else:
            logger.info("AF_ASK_LISTEN_ARMED ask=%s pressed=%d", ask.ask_id, pressed)

    def _device(self, dev_id: str):
        devices = getattr(self.rt, "devices", None)
        if devices is None or not dev_id:
            return None
        try:
            return devices.get(dev_id)
        except Exception:
            return None

    @staticmethod
    def _clear_echo(dev_id: str) -> None:
        """清掉该设备的回声窗：唤醒之后用户说的「要」不能被自家 TTS 的抑制窗吞掉。"""
        try:
            from butler.core.xiaomi_ear import XiaomiEar
            XiaomiEar.clear_echo(dev_id)
        except Exception:
            pass

    # ── 入口：耳朵转写 ─────────────────────────────────────────────
    async def on_transcript(self, device_id: str, room: str, text: str) -> bool:
        """耳朵收到一句转写：若本房间有挂起 ask 且未过期，分类并发布答案。

        返回 True＝这是对 ask 的回答（调用方⛔ 再走正常对话）；False＝与 ask 无关。
        """
        if not self._active:
            return False
        now = time.monotonic()
        room_key = self._room_key(room) if room else ""
        for ask in list(self._active.values()):
            if ask.room_key != room_key:
                continue
            if now > ask.deadline:
                logger.warning("AF_ASK_LATE_DROP ask=%s room=%s 回答晚到（超过 timeout_remaining）",
                               ask.ask_id, ask.room)
                self._active.pop(ask.ask_id, None)
                ask.done.set()
                return False
            answer, why = classify_answer(text)
            if answer is None:
                if why == "param":
                    # 带参数：整条拒收（AF 走超时兜底），⛔ 猜、⛔ 截断。
                    logger.warning("AF_ASK_REJECTED ask=%s room=%s 原因=param（带参数整条拒收）text_len=%d",
                                   ask.ask_id, ask.room, len(str(text or "")))
                    self._active.pop(ask.ask_id, None)
                    ask.done.set()
                    return True
                # 听不清／噪声／小爱自己的「我在」：吞掉这句、继续等——
                # ⛔ 判成拒收把窗口关掉（那样用户再答「要」也接不到了）。
                logger.info("AF_ASK_UNCLASSIFIED ask=%s room=%s 原因=%s text_len=%d，继续等回答",
                            ask.ask_id, ask.room, why, len(str(text or "")))
                return True
            payload = {"ask_id": ask.ask_id, "answer": answer, "raw_text": str(text or "")}
            ok = self._publish(payload)
            logger.info("AF_ASK_ANSWER_SENT ask=%s room=%s answer=%s pub=%s",
                        ask.ask_id, ask.room, answer, ok)
            self._active.pop(ask.ask_id, None)
            ask.done.set()
            return True
        return False

    def _publish(self, payload: dict) -> bool:
        mqtt = getattr(self.rt, "mqtt", None)
        if mqtt is None:
            logger.error("AF_ASK_ANSWER_UNSENT ask=%s 原因=mqtt 未就绪", payload.get("ask_id"))
            return False
        try:
            mqtt.publish(PUB_AF_ASK_ANSWER, payload, qos=1, retain=False)
            return True
        except Exception as e:
            logger.error("AF_ASK_ANSWER_UNSENT ask=%s 原因=%s", payload.get("ask_id"), e)
            return False

    # ── 装配/收尾 ─────────────────────────────────────────────────
    def _room_key(self, room: str) -> str:
        """room 文本 → 规范键（HA area_id 优先，退化到去空白的原文）。"""
        raw = str(room or "").strip()
        try:
            from butler.areas import resolve_room
            mapping: dict[str, str] = {}
            reg = getattr(self.rt, "devices", None)
            if reg is not None and hasattr(reg, "all"):
                mapping = {d.area_id: d.room for d in reg.all() if d.area_id and d.room}
            resolved = resolve_room(raw, mapping) if mapping else resolve_room(raw)
            return resolved or raw
        except Exception:
            return raw

    def close(self) -> None:
        self._closed = True
        for worker in list(self._workers.values()):
            worker.cancel()
        self._workers.clear()
        for ask in list(self._active.values()):
            ask.done.set()
        self._active.clear()
