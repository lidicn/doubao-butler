"""看板 PWA API：用户位置（房间）+ 开发助理播报 + 决策请示。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.core import user_location
from butler.logging_setup import get_logger

logger = get_logger("butler.dashboard_api")


async def get_rooms(request: Request):
    g = guard(request)
    if g:
        return g
    """获取可用房间列表。"""
    return ok({"rooms": user_location.get_rooms()})


async def get_location(request: Request):
    g = guard(request)
    if g:
        return g
    """获取当前房间。"""
    return ok({"room": user_location.get_current_room()})


async def set_location(request: Request):
    """设置当前房间。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    room = str(body.get("room", "")).strip()
    if not room:
        return err("room is required", 400)
    try:
        user_location.set_current_room(room)
        return ok({"room": room})
    except ValueError as e:
        return err(str(e), 400)


async def dev_speak(request: Request):
    """开发助理播报（指定音色 + 当前房间小爱）。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text", "")).strip()
    room = body.get("room") or None
    if not text:
        return err("text is required", 400)
    try:
        result = await user_location.dev_assistant_speak(text, room=room)
        return ok(result)
    except Exception as e:
        logger.exception("dev_speak failed")
        return err(str(e), 500)


def routes():
    return [
        Route("/api/dashboard/rooms", get_rooms, methods=["GET"]),
        Route("/api/dashboard/location", get_location, methods=["GET"]),
        Route("/api/dashboard/location", set_location, methods=["POST"]),
        Route("/api/dashboard/dev_speak", dev_speak, methods=["POST"]),
    ]
