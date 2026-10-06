"""对话路由：对话测试、历史、状态机快照。"""
from __future__ import annotations

import asyncio

from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.runtime import get_runtime
from butler.store import repo


async def test(request):
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    member = str(body.get("member", "")).strip()
    text = str(body.get("text", "")).strip()
    if not text:
        return err("text required")
    rt = get_runtime()
    res = await rt.dialog.test_speak(member, text)
    return ok(res)


async def recent(request):
    g = guard(request)
    if g:
        return g
    limit = int(request.query_params.get("limit", "50"))
    turns = await asyncio.to_thread(repo.recent_turns, limit)
    return ok({"turns": turns})


async def state(request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    return ok({"state": rt.state.snapshot().__dict__} if rt.state else {})


async def wakeup(request):
    """按角色唤醒入口（Phase 6）：NR 耳朵转发 {role?, room, message, member?}。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    if not isinstance(body, dict):
        return err("object required")
    role_id = (body.get("role") or "").strip() or None
    room = str(body.get("room") or "").strip()
    message = str(body.get("message") or "").strip()
    member = str(body.get("member") or "").strip()
    if not message:
        return err("message required")
    res = await get_runtime().dialog.on_wakeup(role_id, room, message, member)
    if res.get("ok"):
        return ok(res)
    return err(res.get("message") or res.get("error") or "wakeup failed", 400)


def routes():
    return [
        Route("/api/dialog/test", test, methods=["POST"]),
        Route("/api/dialog/recent", recent, methods=["GET"]),
        Route("/api/dialog/state", state, methods=["GET"]),
        Route("/api/wakeup", wakeup, methods=["POST"]),
    ]
