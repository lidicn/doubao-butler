"""AF↔DB ask 桥：轮询 AutoForge 挂起 ask → TTS 播报 → 用户回答注回。

工作流：
1. 后台线程每 5 秒 GET AF /api/asks/pending
2. 发现新 ask → 按 room 找小爱设备 → TTS 播报 prompt
3. 存待答表 {ask_id: {room, prompt, prompt_ts}}
4. dialog.on_wakeup 收到用户语音回答时，检查待答表，POST AF /api/asks/answer
5. 格13（I4 2026-09-30）口径：AF 的 200 只代表"答案文件已写进 answer_inbox"
   （af_api.api_asks_answer 返回 {"ok": true, "inbox": <path>}），**不代表 runtime 已消费**。
   消费的唯一可见信号是该 ask 从 /api/asks/pending 消失（af_live._read_inbox 成功才 unlink 文件）。
   ⇒ 投递后 ask 留在待答表（⛔ 立刻 pop：否则下一轮 poll 会把它当新 ask 重新播报），
     AF 撤下才记 CONSUMED；超过 _ACK_WARN_S 仍在 pending ⇒ 点名一次 AF_ANSWER_UNCONSUMED。

配置（data/config.json 或环境变量）：
- AUTOFORGE_BASE_URL: AF API 地址（默认 http://127.0.0.1:8787）
- AUTOFORGE_POLL_INTERVAL: 轮询间隔（秒，默认 5）
- AUTOFORGE_API_TOKEN: AF 写权限令牌
- AUTOFORGE_INBOX_KEY: 与 AF 共享的 HMAC 签名密钥（DCD 20260929 P3 必须启用）
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger("butler.af_bridge")

# 待答表：ask_id → {room, prompt, prompt_ts}
_pending: dict[str, dict[str, Any]] = {}
_base_url = "http://192.168.2.200:8787"
_api_token = ""
_inbox_key = ""
_poll_interval = 5.0
_answer_window = 900.0  # 真人回答窗口（秒），由 init 按配置覆盖
_ACK_WARN_S = 60.0      # 投递后过这么久该 ask 仍在 AF pending ⇒ 点名一次
_delivered_total = 0    # 格13：POST 200 且 ok=true 的次数（＝文件已落 AF 收件箱）
_consumed_total = 0     # 格13：AF 把 ask 从 pending 撤下的次数（＝runtime 真消费）
_unconsumed_total = 0   # 格13：投了却始终没被消费的次数（假绿的解药，必须可数）
_running = False
_loop_task: asyncio.Task | None = None
_rt: Any = None  # butler runtime 引用（TTS 用）


def _sign_answer(ask_id: str, text: str, room: str, answer: dict | None) -> str:
    """与 AF af_live.py:_expect_sig 完全一致的 HMAC-SHA256 签名。

    口径：sig = hmac_sha256(key, f"{ask_id}|{text}|{room}|{answer_json}")
    answer_json = json.dumps(answer or {}, sort_keys=True, ensure_ascii=False)
    """
    answer_json = json.dumps(answer or {}, sort_keys=True, ensure_ascii=False)
    msg = f"{ask_id}|{text}|{room}|{answer_json}".encode("utf-8")
    return hmac.new(_inbox_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def init(rt: Any, base_url: str = "", poll_interval: float = 5.0,
         api_token: str = "", inbox_key: str = "",
         answer_window_s: float = 0.0) -> None:
    """在 app 启动时调用，注入 runtime 和配置。"""
    global _rt, _base_url, _poll_interval, _api_token, _inbox_key, _answer_window
    _rt = rt
    if base_url:
        _base_url = base_url.rstrip("/")
    _poll_interval = poll_interval
    _api_token = api_token
    _inbox_key = inbox_key
    if answer_window_s and answer_window_s > 0:
        _answer_window = float(answer_window_s)
    logger.info("af_bridge init: base=%s interval=%.1fs token=%s inbox_key=%s answer_window=%.0fs",
                _base_url, _poll_interval,
                "set" if _api_token else "none",
                "set" if _inbox_key else "none", _answer_window)


async def start() -> None:
    """启动后台轮询线程。"""
    global _running, _loop_task
    if _running:
        return
    _running = True
    _loop_task = asyncio.create_task(_poll_loop())
    logger.info("af_bridge polling started")


async def stop() -> None:
    global _running
    _running = False
    if _loop_task:
        _loop_task.cancel()
    logger.info("af_bridge stopped")


async def _poll_loop() -> None:
    while _running:
        try:
            await _poll_once()
        except Exception as e:
            logger.warning("af_bridge poll error: %s", e)
        await asyncio.sleep(_poll_interval)


async def _poll_once() -> None:
    global _consumed_total, _unconsumed_total
    now = time.time()
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{_base_url}/api/asks/pending", timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status >= 400:
                body = (await r.text())[:200]
                raise RuntimeError(f"GET asks/pending HTTP {r.status}: {body}")
            data = await r.json()

    asks = data.get("asks", [])
    known_ids = set(_pending.keys())
    new_ids = {a["ask_id"] for a in asks}

    # 新 ask → 播报
    for ask in asks:
        aid = ask["ask_id"]
        if aid not in known_ids:
            _pending[aid] = {
                "room": ask.get("room", ""),
                "prompt": ask.get("prompt", ""),
                "prompt_ts": time.time(),
            }
            await _announce(ask)

    # 已解决的 ask（AF 那边 answer 后 pending 里就没了）
    for gone in known_ids - new_ids:
        gone_info = _pending.get(gone) or {}
        if gone_info.get("delivered_ts"):
            _consumed_total += 1
            logger.info("af_bridge: answer CONSUMED ask=%s waited=%.0fs（AF 已撤下 pending）",
                        gone, now - gone_info["delivered_ts"])
        _pending.pop(gone, None)

    # 投过答案但该 ask 仍挂在 AF pending ⇒ 读侧没消费（缺 key／sig 不符），点名一次，⛔ 静默
    for aid, info in _pending.items():
        dts = info.get("delivered_ts")
        if dts and (now - dts) >= _ACK_WARN_S and not info.get("ack_warned"):
            info["ack_warned"] = True
            _unconsumed_total += 1
            logger.error("AF_ANSWER_UNCONSUMED ask=%s inbox=%s delivered=%.0fs_ago "
                         "ask 仍在 AF pending ⇒ 读侧未消费（嫌疑：sync 缺 AUTOFORGE_INBOX_KEY 或 sig 不符）",
                         aid, info.get("inbox", ""), now - dts)


# 播报出口的角色/成员口径：与 cron_task 的系统播报同一角色（管家）；
# member 非空 ⇒ 落库与 Bark 兜底标题有来源，⛔ 空串。
_ANNOUNCE_ROLE_ID = "butler"
_ANNOUNCE_MEMBER = "系统"


async def _announce(ask: dict[str, Any]) -> None:
    """按 room 走管家统一的按房间出口播报 prompt（审计 D-02 + B-02 复验后重写）。

    旧实现的两条腿都不是真 API：`devices.by_room` 在 DeviceRegistry 上不存在
    （`hasattr` 恒 False ⇒ room_devs 恒 [] ⇒ for 循环体从没被走到），而
    `tts.speak(prompt, device=dev)` 的 `device` 也不是那个签名的参数名（只有 `device_id`）。
    ⇒ 整段是死码：AF 的 ask 一句都没念给用户听过，日志里却没有一句"没播出去"。

    现改用 `dialog.speak_as_role`（内部 `devices.resolve(role.output_devices, room)`、
    回声抑制、无设备时 Bark 兜底，返回 `{"spoken": bool, "devices": [...]}`），
    与 cron_task 的 xiaomi_speak 同一条出口。播不出去一律 error 级点名 AF_ANNOUNCE_UNSENT；
    ⛔ 静默、⛔ 外抛（调用点在轮询里 await，抛出去会把整条 ask 轮询带崩）。
    """
    room = ask.get("room", "")
    prompt = ask.get("prompt", "")
    aid = ask.get("ask_id", "")
    logger.info("af_bridge announce: room=%s prompt=%s", room, prompt)

    if not _rt:
        logger.error("AF_ANNOUNCE_UNSENT ask=%s 原因=runtime 未就绪（ask 没念出去，轮询继续）", aid)
        return

    dialog = getattr(_rt, "dialog", None)
    roles = getattr(_rt, "roles", None)
    if dialog is None:
        logger.error("AF_ANNOUNCE_UNSENT ask=%s 原因=dialog 未就绪", aid)
        return
    if roles is None:
        logger.error("AF_ANNOUNCE_UNSENT ask=%s 原因=roles 未就绪", aid)
        return
    role = roles.get(_ANNOUNCE_ROLE_ID)
    if role is None:
        logger.error("AF_ANNOUNCE_UNSENT ask=%s 原因=角色 %s 取不到", aid, _ANNOUNCE_ROLE_ID)
        return

    try:
        res = await dialog.speak_as_role(role, prompt, room, member=_ANNOUNCE_MEMBER)
    except Exception as e:
        logger.error("AF_ANNOUNCE_UNSENT ask=%s 原因=speak_as_role 抛错：%s", aid, e)
        return
    if not (res or {}).get("spoken"):
        logger.error("AF_ANNOUNCE_UNSENT ask=%s 原因=无设备出声 回执=%.200s", aid, res)
        return
    logger.info("af_bridge: announced ask=%s room=%s devices=%.200s", aid, room, res.get("devices"))


async def on_user_reply(room: str, text: str) -> bool:
    """dialog.on_wakeup 调用：检查是否有挂起 ask 等待回答。

    返回 True 表示这是对 AF ask 的回答（dialog 不需要再走正常对话）。
    """
    global _delivered_total, _unconsumed_total
    # 找同房间待答 ask
    for aid, info in list(_pending.items()):
        if info["room"] and info["room"] != room:
            continue
        # 超时检查（AF 侧有 on_timeout，但这里也防一下）
        if time.time() - info["prompt_ts"] > _answer_window:
            if info.get("delivered_ts"):
                _unconsumed_total += 1
                logger.error("AF_ANSWER_UNCONSUMED ask=%s inbox=%s 应答窗到期仍未被 AF 撤下（答案被吃）",
                             aid, info.get("inbox", ""))
            _pending.pop(aid, None)
            continue

        if info.get("delivered_ts"):
            logger.warning("af_bridge: ask %s 已投递(inbox=%s)未被消费，忽略重复应答 text=%r",
                           aid, info.get("inbox", ""), text[:40])
            return True
        logger.info("af_bridge: user replied to ask %s: %s", aid, text)
        try:
            headers = {}
            if _api_token:
                headers["Authorization"] = f"Bearer {_api_token}"
            answer: dict[str, Any] = {}
            body: dict[str, Any] = {"ask_id": aid, "text": text, "room": room, "answer": answer}
            if _inbox_key:
                body["sig"] = _sign_answer(aid, text, room, answer)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_base_url}/api/asks/answer",
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    if r.status >= 400:
                        body_text = (await r.text())[:200]
                        logger.error("af_bridge answer REJECTED HTTP %s (token=%s): %s",
                                     r.status, "set" if _api_token else "MISSING", body_text)
                        return False
                    resp = await r.json()
                    # 200 只代表 AF 把文件写进了 answer_inbox；⛔ 拿它当"答案已生效"（格13/I3）
                    if not isinstance(resp, dict) or resp.get("ok") is not True:
                        logger.error("AF_ANSWER_NOT_ACCEPTED ask=%s http=%s resp=%.200s"
                                     "（200 但 ok 不为 true ⇒ 连投递都不算）",
                                     aid, r.status, str(resp))
                        return False

                    if resp.get("channel_error"):
                        # DCD 2026-10-02 §三③：AF HTTP 200 但写通道坏了 ⇒ 告警 + ⛔ 标记已答
                        # （一记 delivered_ts 就会被下面「已投递」分支永久吞掉＝假绿）
                        logger.error("AF_CHANNEL_ERROR ask=%s inbox=%s resp=%.200s"
                                     "（200 但 AF 写通道失败 ⇒ 不记 delivered_ts，用户可重答）",
                                     aid, str(resp.get("inbox", "")), str(resp))
                        return False
            info["delivered_ts"] = time.time()
            info["inbox"] = str(resp.get("inbox", ""))
            _delivered_total += 1
            logger.info("af_bridge: answer DELIVERED ask=%s inbox=%s（投递≠消费，AF 撤下才算）",
                        aid, info["inbox"])
            return True
        except Exception as e:
            logger.warning("af_bridge answer failed: %s", e)
            return False

    return False


def pending_count() -> int:
    return len(_pending)
