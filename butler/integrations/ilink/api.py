"""iLink 微信集成 API。

提供 WebUI 接口：
- GET  /api/ilink/status      查看微信集成状态
- POST /api/ilink/login       开始扫码登录（返回二维码 URL）
- POST /api/ilink/check       检查扫码状态（长轮询）
- POST /api/ilink/logout      退出登录
- POST /api/ilink/start       启动消息桥接
- POST /api/ilink/stop        停止消息桥接
- POST /api/ilink/send        手动发送消息（测试用）
"""
from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import ok, err, guard
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.ilink.api")


def _get_ilink():
    """从 runtime 获取 iLink 客户端和桥接器。"""
    rt = get_runtime()
    client = getattr(rt, "ilink_client", None)
    bridge = getattr(rt, "ilink_bridge", None)
    return rt, client, bridge


async def ilink_status(request: Request):
    """查看微信集成状态。"""
    g = guard(request)
    if g:
        return g
    rt, client, bridge = _get_ilink()
    if client is None:
        return ok({"enabled": False, "status": "not_initialized", "message": "iLink 微信集成未启用"})

    status = {
        "enabled": True,
        "logged_in": client.is_logged_in,
        "bot_id": client.bot_id,
        "nickname": client._session.get("nickname", ""),
        "bridge_running": bridge.is_running if bridge else False,
        "active_users": len(bridge._history) if bridge else 0,
    }
    return ok(status)


async def ilink_login(request: Request):
    """开始扫码登录（返回二维码 URL）。"""
    g = guard(request)
    if g:
        return g
    rt, client, bridge = _get_ilink()
    if client is None:
        return err("iLink 微信集成未启用", 503)

    if client.is_logged_in:
        return ok({"already_logged_in": True, "bot_id": client.bot_id})

    result = await client.get_qrcode()
    if result.get("ok"):
        return ok({
            "qrcode": result["qrcode"],
            "qrcode_img_content": result["qrcode_img_content"],
            "expire_at": result["expire_at"],
            "message": "请用微信扫描二维码登录（打开 qrcode_img_content 链接或生成二维码）",
        })
    return err(f"获取二维码失败: {result.get('error', 'unknown')}", 500)


async def ilink_check(request: Request):
    """检查扫码状态（长轮询，最多等待 25 秒）。"""
    g = guard(request)
    if g:
        return g
    rt, client, bridge = _get_ilink()
    if client is None:
        return err("iLink 微信集成未启用", 503)

    try:
        body = await request.json()
    except Exception:
        body = {}
    qrcode = body.get("qrcode", "") or request.query_params.get("qrcode", "")
    if not qrcode:
        return err("缺少 qrcode 参数", 400)

    result = await client.check_qrcode_status(qrcode)
    if result.get("ok"):
        status = result.get("status", "waiting")
        response = {"status": status}
        if status == "confirmed":
            response["bot_id"] = result.get("bot_id", "")
            response["message"] = "登录成功"
        elif status == "scanned":
            response["message"] = "已扫码，请在手机上确认"
        elif status == "expired":
            response["message"] = "二维码已过期，请重新获取"
        return ok(response)
    return err(f"检查扫码状态失败: {result.get('error', 'unknown')}", 500)


async def ilink_logout(request: Request):
    """退出登录。"""
    g = guard(request)
    if g:
        return g
    rt, client, bridge = _get_ilink()
    if client is None:
        return err("iLink 微信集成未启用", 503)

    # 先停止桥接
    if bridge and bridge.is_running:
        bridge.stop()
    client.logout()
    return ok({"logged_out": True, "message": "已退出微信登录"})


async def ilink_start(request: Request):
    """启动消息桥接。"""
    g = guard(request)
    if g:
        return g
    rt, client, bridge = _get_ilink()
    if client is None or bridge is None:
        return err("iLink 微信集成未启用", 503)
    if not client.is_logged_in:
        return err("请先扫码登录微信", 400)
    if bridge.is_running:
        return ok({"already_running": True, "message": "消息桥接已在运行"})

    bridge.start()
    return ok({"started": True, "message": "微信消息桥接已启动"})


async def ilink_stop(request: Request):
    """停止消息桥接。"""
    g = guard(request)
    if g:
        return g
    rt, client, bridge = _get_ilink()
    if bridge is None:
        return err("iLink 微信集成未启用", 503)
    if not bridge.is_running:
        return ok({"already_stopped": True, "message": "消息桥接未在运行"})

    bridge.stop()
    return ok({"stopped": True, "message": "微信消息桥接已停止"})


async def ilink_send(request: Request):
    """手动发送消息（测试用）。"""
    g = guard(request)
    if g:
        return g
    rt, client, bridge = _get_ilink()
    if client is None:
        return err("iLink 微信集成未启用", 503)
    if not client.is_logged_in:
        return err("请先扫码登录微信", 400)

    try:
        body = await request.json()
    except Exception:
        return err("请求体必须是 JSON", 400)

    to_user = body.get("to_user", "")
    text = body.get("text", "")
    if not to_user or not text:
        return err("缺少 to_user 或 text 参数", 400)

    result = await client.send_message(to_user, text)
    if result.get("ok"):
        return ok({"sent": True, "message_id": result.get("message_id", "")})
    return err(f"发送失败: {result.get('error', 'unknown')}", 500)


def routes():
    return [
        Route("/api/ilink/status", ilink_status, methods=["GET"]),
        Route("/api/ilink/login", ilink_login, methods=["POST"]),
        Route("/api/ilink/check", ilink_check, methods=["POST"]),
        Route("/api/ilink/logout", ilink_logout, methods=["POST"]),
        Route("/api/ilink/start", ilink_start, methods=["POST"]),
        Route("/api/ilink/stop", ilink_stop, methods=["POST"]),
        Route("/api/ilink/send", ilink_send, methods=["POST"]),
    ]
