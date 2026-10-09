# -*- coding: utf-8 -*-
"""§9 任务2：AF 自动化上报事件（af/automation/fired|failed）的消费腿。

契约表 §1.2 行 57-58：fired {trace_id, ts, automation_id, ref}；failed 多带 error。
DB＝表达中枢：收到即告知用户（Bark 优先），失败事件把 `error` 原文带进正文。
fail-closed：非对象或缺 trace_id ⇒ 丢弃 + ADM_ERR_PAYLOAD_INVALID 日志，⛔ 拿缺件事件当成功播报。
"""
from typing import Callable, Optional

from butler.logging_setup import get_logger
from homesdk.adm.errors import ADM_ERR_INTERNAL, ADM_ERR_PAYLOAD_INVALID

logger = get_logger("butler.bus.af_events")

OUTCOME_NOTIFIED = "notified"
OUTCOME_DROPPED = "dropped_invalid"
OUTCOME_NOTIFY_FAILED = "notify_failed"


def _default_notify(title: str, body: str) -> None:
    from butler.notifier.router import get_router
    nr = get_router()
    if nr is None or nr.bark is None:
        raise RuntimeError("notify router unavailable")
    nr.bark.send(title, body)


def handle_af_automation_event(topic: str, payload,
                               notify: Optional[Callable[[str, str], None]] = None) -> str:
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
        send(title, body)
    except Exception as e:
        logger.warning("%s af_events notify failed topic=%s trace=%s err=%s",
                       ADM_ERR_INTERNAL, topic, trace_id, e)
        return OUTCOME_NOTIFY_FAILED
    logger.info("AF_EVENT_NOTIFIED topic=%s trace=%s automation=%s", topic, trace_id, automation_id)
    return OUTCOME_NOTIFIED
