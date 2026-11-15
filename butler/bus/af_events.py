# -*- coding: utf-8 -*-
"""§9 任务2：AF 自动化上报事件（af/automation/fired|failed）的消费腿。

契约表 §1.2 行 57-58：fired {trace_id, ts, automation_id, ref}；failed 多带 error。
DB＝表达中枢：收到即告知用户（Bark 优先），失败事件把 `error` 原文带进正文。
fail-closed：非对象或缺 trace_id ⇒ 丢弃 + ADM_ERR_PAYLOAD_INVALID 日志，⛔ 拿缺件事件当成功播报。
"""
import inspect
from typing import Callable, Optional

from butler.logging_setup import get_logger
from homesdk.adm.errors import ADM_ERR_INTERNAL, ADM_ERR_PAYLOAD_INVALID

logger = get_logger("butler.bus.af_events")

OUTCOME_NOTIFIED = "notified"
OUTCOME_DROPPED = "dropped_invalid"
OUTCOME_NOTIFY_FAILED = "notify_failed"


async def _default_notify(title: str, body: str) -> None:
    """走统一通知路由（唯一正确入口：`butler.notify.singleton.get_router`）。

    历史缺陷（批135 引入）：此处误 import 了 `butler.notifier.router.get_router`——该函数
    从未存在（`notifier/router.py` 里只有 `NotificationRouter` 类），全仓其余五个消费点
    都走 `butler.notify.singleton`。后果：订阅通、通知腿 100% 失败且只落 WARNING，
    AF 的 fired/failed Bark 推送等于一直没发。

    调用形按 `NotifyRouter.notify(channel, text, **kwargs)`（async，返回 NotifyResult）：
    ⛔ 不是 `NotificationRouter` 的 `bark.send(...)`（无此方法）。
    """
    from butler.notify.singleton import get_router

    router = get_router()
    if router is None:
        # 不假绿：router 未初始化是「通知没发出去」，交给上层记 notify_failed。
        raise RuntimeError("notify router 未初始化（butler.notify.singleton.get_router() 返回 None）")
    res = await router.notify("bark", body, title=title)
    ch = (getattr(res, "results", None) or {}).get("bark")
    if ch is None or not getattr(ch, "ok", False):
        # 通道未绑定 / 被模式门挡住 / 投递失败：一律按未送达上报，⛔ 当成已通知。
        raise RuntimeError("bark 通道未送达：%s" % (getattr(ch, "error", "not_bound"),))


async def handle_af_automation_event(topic: str, payload,
                                     notify: Optional[Callable] = None) -> str:
    if not isinstance(payload, dict):
        logger.error("%s af_events payload 非对象 topic=%s", ADM_ERR_PAYLOAD_INVALID, topic)
        return OUTCOME_DROPPED
    trace_id = str(payload.get("trace_id", "")).strip()
    if not trace_id:
        logger.error("%s af_events trace_id 缺失 topic=%s", ADM_ERR_PAYLOAD_INVALID, topic)
        return OUTCOME_DROPPED
    automation_id = str(payload.get("automation_id") or "?").strip()[:120]
    ref = str(payload.get("ref") or "").strip()[:200]
    failed = topic.endswith("/failed")
    if failed:
        error = str(payload.get("error") or "未提供 error 原文").strip()[:300]
        title = "自动化失败"
        body = f"{automation_id}（{ref}）失败：{error}｜trace={trace_id}"
    else:
        title = "自动化完成"
        body = f"{automation_id}（{ref}）已执行｜trace={trace_id}"
    send = notify or _default_notify
    try:
        result = send(title, body)
        # 注入的桩可以是同步 callable；生产 `_default_notify` 是 async，这里统一等它完成。
        if inspect.isawaitable(result):
            await result
    except Exception as e:
        logger.warning("%s af_events notify failed topic=%s trace=%s err=%s",
                       ADM_ERR_INTERNAL, topic, trace_id, e)
        return OUTCOME_NOTIFY_FAILED
    logger.info("AF_EVENT_NOTIFIED topic=%s trace=%s automation=%s", topic, trace_id, automation_id)
    return OUTCOME_NOTIFIED
