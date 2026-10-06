"""PWA 对话 API：用户 ↔ PM 直接消息通道，不经 LLM。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.config import get_settings
from butler.core import pwa_chat_store
from butler.logging_setup import get_logger

logger = get_logger("butler.pwa_chat_api")

# DeskPilot PM 消息管道（20260909_003 上线，替代独立 18766 转发服务）
DESKPILOT_PM_SEND_URL = "http://192.168.2.201:8765/api/v1/pm/send"
# WO-BUT-018: token 从 config 读取，不再硬编码


async def pwa_chat_send(request: Request):
    """用户在 PWA 发消息 → 存入消息库 + 通过 DeskPilot PM 管道发送到 PM 对话。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("无效请求体")
    text = (body.get("text") or "").strip()
    if not text:
        return err("消息不能为空")

    # 1. 存入消息库
    msg = pwa_chat_store.add_message("user", text)

    # 2. 通过 DeskPilot PM 消息管道发送到豆包 PM 对话（target=PM）
    forwarded = False
    forward_error = ""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(
                DESKPILOT_PM_SEND_URL,
                json={"target": "PM", "message": text, "press_enter": True},
                headers={"Authorization": f"Bearer {get_settings().deskpilot_api_token}"},
            )
            result = r.json()
            forwarded = result.get("ok", False)
            if not forwarded:
                forward_error = result.get("error") or result.get("message", "unknown")
    except Exception as e:
        forward_error = str(e)
        logger.warning("forward to PM via DeskPilot failed: %s", e)

    return ok({
        "id": msg["id"],
        "forwarded_to_pm": forwarded,
        "forward_error": forward_error
    })


async def pwa_chat_messages(request: Request):
    """获取消息列表（PWA 轮询用）。"""
    g = guard(request)
    if g:
        return g
    since_id = int(request.query_params.get("since", 0))
    limit = int(request.query_params.get("limit", 100))
    items = pwa_chat_store.list_messages(since_id=since_id, limit=limit)
    latest = pwa_chat_store.get_latest_id()
    return ok({"items": items, "latest_id": latest})


async def pwa_chat_reply(request: Request):
    """PM 回复到 PWA（管家工具调用此 API）。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("无效请求体")
    text = (body.get("text") or "").strip()
    if not text:
        return err("回复不能为空")
    msg = pwa_chat_store.add_message("pm", text)
    return ok({"id": msg["id"]})


def routes():
    return [
        Route("/api/pwa_chat/send", pwa_chat_send, methods=["POST"]),
        Route("/api/pwa_chat/messages", pwa_chat_messages, methods=["GET"]),
        Route("/api/pwa_chat/reply", pwa_chat_reply, methods=["POST"]),
    ]
