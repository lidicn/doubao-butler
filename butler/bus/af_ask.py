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
            if not spoken:
                logger.error("AF_ASK_UNSENT ask=%s room=%s 原因=没有设备出声（⛔ 继续等回答）",
                             ask.ask_id, ask.room)
                return
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
            self._active.pop(ask.ask_id, None)
            if not ask.done.is_set():
                ask.done.set()

    async def _speak(self, ask: _Ask) -> bool:
        dialog = getattr(self.rt, "dialog", None)
        roles = getattr(self.rt, "roles", None)
        if dialog is None or roles is None:
            logger.error("AF_ASK_UNSENT ask=%s 原因=runtime 未就绪（dialog/roles 缺失）", ask.ask_id)
            return False
        role = roles.get("butler") if hasattr(roles, "get") else None
        if role is None:
            logger.error("AF_ASK_UNSENT ask=%s 原因=角色 butler 取不到", ask.ask_id)
            return False
        try:
            res = await dialog.speak_as_role(role, ask.prompt, ask.room, member="系统")
        except Exception as e:
            logger.error("AF_ASK_UNSENT ask=%s 原因=speak_as_role 抛错：%s", ask.ask_id, e)
            return False
        if not (res or {}).get("spoken"):
            logger.error("AF_ASK_UNSENT ask=%s 原因=无设备出声 回执=%.200s", ask.ask_id, res)
            return False
        logger.info("AF_ASK_SPOKEN ask=%s room=%s devices=%.200s",
                    ask.ask_id, ask.room, res.get("devices"))
        return True

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
                # 带参数/听不清/复述问句：整条拒收（AF 走超时兜底），⛔ 猜、⛔ 截断。
                logger.warning("AF_ASK_REJECTED ask=%s room=%s 原因=%s text_len=%d",
                               ask.ask_id, ask.room, why, len(str(text or "")))
                self._active.pop(ask.ask_id, None)
                ask.done.set()
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
            if reg is not None:
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
