"""多源融合定位引擎 REST API。

GET  /api/presence/snapshot          — 当前定位快照（所有用户+房间）
GET  /api/presence/users             — 所有用户实时位置
GET  /api/presence/users/{user_id}   — 指定用户位置+历史
GET  /api/presence/rooms             — 所有房间状态
GET  /api/presence/rooms/{room_id}   — 指定房间状态
POST /api/presence/events             — 上报定位事件（ArcFace/ESP32/手表）
GET  /api/presence/where/{user_id}   — 自然语言回答用户在哪
"""
from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.presence.api")


async def get_snapshot(request: Request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    engine = rt.presence_engine
    if engine is None:
        return err("presence engine not initialized")
    return ok(engine.snapshot())


async def get_users(request: Request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    engine = rt.presence_engine
    if engine is None:
        return err("presence engine not initialized")
    users = {}
    for uid, u in engine.get_all_presence().items():
        users[uid] = u.to_dict()
    return ok({"users": users, "count": len(users)})


async def get_user(request: Request):
    g = guard(request)
    if g:
        return g
    user_id = request.path_params.get("user_id", "")
    rt = get_runtime()
    engine = rt.presence_engine
    if engine is None:
        return err("presence engine not initialized")
    u = engine.get_user_presence(user_id)
    if u is None:
        return err("user not found", code=404)
    # 读取历史
    history = []
    if rt.presence_store:
        history = rt.presence_store.get_history(user_id, limit=20)
    result = u.to_dict()
    result["history"] = history
    return ok(result)


async def get_rooms(request: Request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    engine = rt.presence_engine
    if engine is None:
        return err("presence engine not initialized")
    rooms = {}
    for rid, r in engine.get_all_rooms().items():
        rooms[rid] = r.to_dict()
    return ok({"rooms": rooms, "count": len(rooms)})


async def get_room(request: Request):
    g = guard(request)
    if g:
        return g
    room_id = request.path_params.get("room_id", "")
    rt = get_runtime()
    engine = rt.presence_engine
    if engine is None:
        return err("presence engine not initialized")
    r = engine.get_room_state(room_id)
    if r is None:
        return err("room not found", code=404)
    return ok(r.to_dict())


async def report_event(request: Request):
    """上报定位事件（ArcFace/ESP32/手表等外部信号源）。

    请求体：
    {
        "type": "arcface_recognized",  // 信号类型
        "user": "lidicn",               // 用户 ID
        "room": "客厅",                  // 房间 ID
        "confidence": 0.9,              // 信号置信度
        "metadata": {}                   // 附加信息
    }
    """
    g = guard(request)
    if g:
        return g
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return err("invalid json")

    signal_type = body.get("type", "")
    user_id = body.get("user", "")
    room = body.get("room", "")
    confidence = float(body.get("confidence", 1.0))
    metadata = body.get("metadata", {})

    if not signal_type or not user_id or not room:
        return err("type, user, room are required")

    rt = get_runtime()
    engine = rt.presence_engine
    if engine is None:
        return err("presence engine not initialized")

    engine.report_signal(signal_type, user_id, room, confidence, metadata)
    logger.info("presence event reported: type=%s user=%s room=%s conf=%.2f",
                signal_type, user_id, room, confidence)
    return ok({"ok": True, "message": "event reported"})


async def where_is_user(request: Request):
    """自然语言回答用户在哪（LLM 增强）。"""
    g = guard(request)
    if g:
        return g
    user_id = request.path_params.get("user_id", "")
    rt = get_runtime()
    engine = rt.presence_engine
    if engine is None:
        return err("presence engine not initialized")

    u = engine.get_user_presence(user_id)
    if u is None:
        return ok({"answer": f"没有找到用户 {user_id} 的位置信息。"})

    room_name = engine.get_room_name(u.room) if u.room else "未知"
    if u.confidence < 0.6:
        answer = f"{user_id} 当前位置不确定，可能在家中某个房间。"
    elif u.room == "away":
        answer = f"{user_id} 当前不在家。"
    else:
        answer = f"{user_id} 当前在{room_name}（置信度 {u.confidence:.0%}）。"

    return ok({"user_id": user_id, "room": u.room, "room_name": room_name,
               "confidence": u.confidence, "answer": answer})


def routes():
    return [
        Route("/api/presence/snapshot", get_snapshot, methods=["GET"]),
        Route("/api/presence/users", get_users, methods=["GET"]),
        Route("/api/presence/users/{user_id}", get_user, methods=["GET"]),
        Route("/api/presence/rooms", get_rooms, methods=["GET"]),
        Route("/api/presence/rooms/{room_id}", get_room, methods=["GET"]),
        Route("/api/presence/events", report_event, methods=["POST"]),
        Route("/api/presence/where/{user_id}", where_is_user, methods=["GET"]),
    ]
