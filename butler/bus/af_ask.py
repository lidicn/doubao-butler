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
import os
import re
import time
from dataclasses import dataclass, field

from butler.bus.topics import PUB_AF_ASK_ANSWER
from butler.logging_setup import get_logger
from butler.param import soft_num
from butler.startup_assets import WAKE_DING_NAME
from homesdk.consent import classify_answer as _consent_classify

logger = get_logger("butler.bus.af_ask")

#: 单条问句最长等多久（秒）——只是兜底上界；真正上界取载荷里的 `timeout_remaining`。
MAX_WAIT_S = 600.0
#: 同一 ask_id 的去重记忆窗（秒）。AF 重发/重连补发都在此窗口内被吃掉。
SEEN_TTL_S = 3600.0
#: prompt 长度上限（与收件箱 speak 通道同口径），超长截断只影响念出来的长度，不影响 ask_id。
MAX_PROMPT_LEN = 500
#: 回答原话长度上限（超过这个长度不可能是 MVP 的「要/不要」）
MAX_ANSWER_LEN = 20

#: 唤醒提示音时长（秒）：`startup_assets.FFMPEG_ARGV` 里 `sine=...:duration=0.6` 钉死。
WAKE_DING_DURATION_S = 0.6
#: 提示音播完的余量（秒）：≥ 一个 ding 时长，覆盖云端投递抖动。
WAKE_DING_MARGIN_S = 0.3
#: 报给 `schedule_xiaomi_stop` 的时长（秒）：那条停止腿自带 800ms 提前量（`ha.py:605`）
#: ⇒ 报「时长+0.8」才等价于「叮播完即停」；报 0.6s 会因提前量落在音频中段而被切掉尾巴。
WAKE_DING_STOP_REPORT_S = WAKE_DING_DURATION_S + 0.8

#: 问句播完探测：下发后至少等这么久才允许判定（防读到播放前的空/陈旧态就放音）。
PROMPT_IDLE_FLOOR_S = 1.0
#: 问句播完探测：轮询间隔（与 `ha.schedule_media_stop` 同口径）。
PROMPT_POLL_S = 0.2
#: 问句播完探测：位置判据的 ε（对齐 `ha.py` 的 `pos >= dur - 0.5`）。
PROMPT_END_EPS_S = 0.5


def _settings():
    """配置读取点（测试可整体替换本函数，⛔ 各方法自己 `import get_settings`）。"""
    from butler.config import get_settings
    return get_settings()


# 回答判定唯一权威＝homesdk.consent.classify_answer（⛔ 本仓不再自建词表 / 子串判定）。
# 否定优先、YES 只整句精确、应答词（嗯/哦）不算同意 —— 全部由 homesdk 一处决定。
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
    verdict = _consent_classify(t)  # homesdk 唯一权威：yes / no / unknown
    if verdict == "yes":
        return "yes", "ok"
    if verdict == "no":
        return "no", "ok"
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

    def __init__(self, rt, *, clock=None, sleep=None):
        self.rt = rt
        self._seen: dict[str, float] = {}
        self._queues: dict[str, list[_Ask]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._active: dict[str, _Ask] = {}
        self._closed = False
        # 时钟/睡眠可注入：判据件 D4 用桩时钟验「按压不早于 ding 播完」，⛔ 靠真等。
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep

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
        deadline = self._clock() + min(timeout, MAX_WAIT_S)
        return _Ask(ask_id=ask_id, room=room, room_key=self._room_key(room),
                    prompt=prompt[:MAX_PROMPT_LEN], deadline=deadline)

    def _is_duplicate(self, ask_id: str) -> bool:
        self._prune_seen()
        return ask_id in self._seen

    def _remember(self, ask_id: str) -> None:
        self._seen[ask_id] = self._clock()

    def _prune_seen(self) -> None:
        now = self._clock()
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
            remaining = ask.deadline - self._clock()
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
        """念完后：等问句播完 → 播唤醒提示音并等它播完 → 按 `wake_button` 开窗。

        2026-11-23「唤醒窗口可感知化」：主卧 Sound Pro 的按压唤醒是纯静音开窗（无提示音、
        无灯），用户只能盲猜窗口何时打开。四段顺序（D0 钉着，⛔ 换序）：

        ① `dialog.speak_as_role` 下发即返回、**不等播放完成**（`core/dialog.py:686`）⇒ 必须显式
           等问句播完（`_wait_prompt_finished`）：2026-11-24 起改「轮询设备 media_player 的完成
           信号」，探测不可用才回退旧的固定估时等待。这一等⛔ 删、⛔ 挪到 ding 之后：删了就是
           在问句尾巴上按按钮；挪了会 REPLACE_ALL 打断问句。
        ② 只对「本次真会按压的那批设备」播 `wake_ding.mp3`（与 press 同一道设备门，D5）。
        ③ 等提示音播完：各条「开始 + 0.6s 时长 + 0.3s 余量」，时钟可注入（D4 用桩时钟验）。
        ④ 再 `button.press` 开窗——先开窗后放音，提示音会被刚打开的麦克风自己拾成用户回答。

        ⛔ 静默降级：没有唤醒按钮／提示音缺失或播放失败／按压失败一律点名
        `AF_ASK_WAKE_UNSUPPORTED`（提示音再补一条 `AF_ASK_WAKE_DING_UNSUPPORTED`），
        且提示音失败**不阻塞**按压（D3）。
        """
        dialog = getattr(self.rt, "dialog", None)
        delay = self._speak_secs(ask.prompt, dialog) + 1.5
        remaining = ask.deadline - self._clock()
        if remaining <= 1.0:
            logger.warning("AF_ASK_WAKE_SKIPPED ask=%s 剩余 %.1fs，不足等念完再唤醒", ask.ask_id, remaining)
            return
        cap = min(delay, max(0.0, remaining - 1.0))

        # ② 同一道设备门：先挑出本次真会按压的设备，ding／press／完成探测都只对这批（D5）
        targets = self._wake_targets(ask, spoken)

        # ① 等问句播完：完成信号驱动（2026-11-24 方案 A）；探测不可用则回退原有固定等待（逐字等价）
        if not await self._wait_prompt_finished(ask, targets, cap):
            await self._sleep(min(delay, max(0.0, remaining - 1.0)))

        # ② 播「叮」
        if targets:
            await self._play_wake_ding(ask, targets)

        # ④ 按压开窗
        pressed = 0
        for dev, wake in targets:
            try:
                await self.rt.ha.call_service("button", "press", {"entity_id": wake})
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

    def _wake_targets(self, ask: _Ask, spoken: dict) -> list:
        """挑出「本次真会按压唤醒按钮」的设备：ding 与 press 共用这一道门（D5）。

        无唤醒按钮/设备未登记/HA 未就绪一律点名 `AF_ASK_WAKE_UNSUPPORTED` 并排除——
        ⛔ 对这些设备也播提示音：用户听到「叮」却开不了窗，比没有提示音更误导。
        """
        ha = getattr(self.rt, "ha", None)
        targets: list = []
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
            if ha is None:
                logger.warning("AF_ASK_WAKE_UNSUPPORTED ask=%s device=%s 原因=HA 未就绪", ask.ask_id, dev.id)
                continue
            targets.append((dev, wake))
        return targets

    async def _wait_prompt_finished(self, ask: _Ask, targets: list, cap_secs: float) -> bool:
        """① 等问句播完（2026-11-24 方案 A：完成信号驱动）。

        旧行为＝盲等 `_speak_secs+1.5`（11 字≈5.0s），而设备侧 2.23s 就播完了（03:01:59 的
        `stop@end pos=2129 dev_dur=2232`）⇒ 问句与「叮」之间空等约 2s。本函数轮询念题设备的
        `media_player` 状态，确认播完即返回；`cap_secs` 是旧等待的上限（只可能更早，⛔ 更晚）。

        三道保险：
        - floor：下发后 ≥ `PROMPT_IDLE_FLOOR_S` 才允许判定（防读到播放前的空/陈旧态就放音）；
        - cap：探测持续不可用 ⇒ 最多等到 `cap_secs`（与改造前等值，不晚放）；
        - 回退：无 `ha_player_entity`／HA 不可用／`get_state` 缺失 ⇒ 返回 False，
          由调用方走原有固定等待（逐字等价）。

        多设备：`all()` —— 只要还有一台在播就不算播完（先播完那台的「叮」会插进另一台的问句）。
        返回 True＝本次等待已由本函数完成；False＝调用方需走固定等待。
        """
        ha = getattr(self.rt, "ha", None)
        entities = [getattr(dev, "ha_player_entity", "") or "" for dev, _w in targets]
        entities = [e for e in entities if e]
        probe = getattr(ha, "get_state", None) if ha is not None else None
        if not entities or not callable(probe):
            logger.info("AF_ASK_WAKE_WAIT_FALLBACK ask=%s 原因=%s，回退固定等待 %.2fs",
                        ask.ask_id,
                        "无可用播放实体" if not entities else "HA get_state 不可用",
                        cap_secs)
            return False

        started = self._clock()
        deadline = started + max(0.0, cap_secs)
        floor_until = started + PROMPT_IDLE_FLOOR_S
        polls = 0
        last = {"state": "?", "pos": None, "dur": None}
        while True:
            now = self._clock()
            if now >= deadline:
                logger.info("AF_ASK_WAKE_WAIT_FALLBACK ask=%s 原因=探测达上限 %.2fs poll=%d 最后读数=%s",
                            ask.ask_id, max(0.0, cap_secs), polls, last)
                return True
            await self._sleep(min(PROMPT_POLL_S, deadline - now))
            polls += 1
            if self._clock() <= floor_until:      # floor：这段时间内⛔ 判定
                continue
            states = []
            for ent in entities:
                try:
                    st = await probe(ent)
                except Exception as e:            # ⛔ 让一只设备的读失败打断整条唤醒腿
                    logger.info("AF_ASK_WAKE_WAIT_PROBE_FAILED ask=%s entity=%s err=%s",
                                ask.ask_id, ent, e)
                    st = None
                if isinstance(st, dict):
                    states.append(st)
            if len(states) != len(entities):
                last = {"state": "<不可读>", "pos": None, "dur": None}
                continue
            last = self._prompt_progress(states)
            if all(self._prompt_finished(st) for st in states):
                logger.info("AF_ASK_WAKE_WAIT_COMPLETE ask=%s 耗时=%.2fs poll=%d 读数=%s",
                            ask.ask_id, self._clock() - started, polls, last)
                return True

    @staticmethod
    def _prompt_finished(state: dict) -> bool:
        """单台设备的「问句播完」判定：state≠playing，或 pos ≥ dur-ε（对齐现有停播口径）。"""
        cur = str((state or {}).get("state") or "")
        if not cur:
            return False
        if cur != "playing":
            return True
        attrs = (state or {}).get("attributes") or {}
        # DB138/DB139：载荷取数一律走 `soft_num`（不可解按默认 0 走并留痕），⛔ 裸 float() 铸形。
        pos = soft_num(attrs.get("media_position"), "media_position", default=0.0, integer=False)
        dur = soft_num(attrs.get("media_duration"), "media_duration", default=0.0, integer=False)
        if pos is None or dur is None:
            return False
        return dur > 0 and pos >= dur - PROMPT_END_EPS_S

    @staticmethod
    def _prompt_progress(states: list) -> dict:
        """留痕用的探测读数（取第一台，附台数）。"""
        first = states[0] if states else {}
        attrs = (first or {}).get("attributes") or {}
        return {"state": (first or {}).get("state"),
                "pos": attrs.get("media_position"),
                "dur": attrs.get("media_duration"),
                "n": len(states)}

    async def _play_wake_ding(self, ask: _Ask, targets: list) -> None:
        """③ 对 targets 播 `wake_ding.mp3` 并等到播完；失败只点名，⛔ 阻塞按压（D3）。"""
        try:
            s = _settings()
        except Exception as e:
            logger.warning("AF_ASK_WAKE_DING_UNSUPPORTED ask=%s 原因=配置读取失败：%s", ask.ask_id, e)
            return
        path = os.path.join(str(getattr(s, "tts_dir", "") or ""), WAKE_DING_NAME)
        if not getattr(s, "tts_dir", "") or not os.path.exists(path):
            logger.warning("AF_ASK_WAKE_DING_UNSUPPORTED ask=%s 原因=提示音资产缺失 path=%s",
                           ask.ask_id, path)
            return
        url = str(getattr(s, "base_url", "") or "").rstrip("/") + "/tts/" + WAKE_DING_NAME
        starts: list[float] = []
        for dev, _wake in targets:
            started = self._clock()
            if await self._emit_ding(dev, url):
                starts.append(started)
                logger.info("AF_ASK_WAKE_DING_PLAYED ask=%s device=%s url=%s",
                            ask.ask_id, dev.id, url)
            else:
                logger.warning("AF_ASK_WAKE_DING_UNSUPPORTED ask=%s device=%s 原因=播放失败",
                               ask.ask_id, getattr(dev, "id", ""))
        if not starts:
            return
        # 等「最后一条」播完：按各自开始时刻算；⛔ 从全部下发完再硬等（首条会提前）。
        done_at = max(st + WAKE_DING_DURATION_S + WAKE_DING_MARGIN_S for st in starts)
        wait = done_at - self._clock()
        if wait > 0:
            await self._sleep(wait)

    async def _emit_ding(self, dev, url: str) -> bool:
        """播提示音：照抄念题同一条小爱腿（`core/dialog.py:625-651`，D6 钉着）。

        有 xiaoai 云端直连优先 `player_play_url`（music 固件走 `player_play_music` 并补关循环），
        否则回退 HA `media_player.play_media`。⛔ 另造第二套播放通道。
        """
        ha = getattr(self.rt, "ha", None)
        entity = getattr(dev, "ha_player_entity", "") or ""
        if ha is None or not entity:
            return False
        dev_id = getattr(dev, "id", "")
        try:
            s = _settings()
            if getattr(s, "xiaomi_service_token", "") and getattr(s, "xiaomi_user_id", ""):
                aid = await ha.get_xiaoai_id(entity)
                if aid:
                    xmethod = getattr(dev, "xiaomi_play", None) or "url"
                    rj = await ha.play_xiaomi_url_once(aid, url, method=xmethod) or {}
                    ok = str(rj.get("code")) == "0"
                    if ok and xmethod == "music":
                        ha.schedule_xiaomi_stop(aid, WAKE_DING_STOP_REPORT_S)
                    if not ok:
                        logger.warning("AF_ASK_WAKE_DING_UNSUPPORTED device=%s 原因=小爱直连失败 code=%s",
                                       dev_id, rj.get("code"))
                    return ok
            r = await ha.tts_play_url(url, entity)
            if str(r).startswith("ok"):
                await ha.schedule_media_stop(entity, WAKE_DING_DURATION_S)
                return True
            logger.warning("AF_ASK_WAKE_DING_UNSUPPORTED device=%s 原因=HA 播放失败 detail=%s",
                           dev_id, r)
            return False
        except Exception as e:
            logger.warning("AF_ASK_WAKE_DING_UNSUPPORTED device=%s 原因=播放异常：%s", dev_id, e)
            return False

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
        now = self._clock()
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
