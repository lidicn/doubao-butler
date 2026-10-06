"""推送风控 API：状态查询、重置、审计日志。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import ok, err, guard
from butler.runtime import get_runtime


async def guard_status(request: Request):
    """获取推送风控状态。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    pg = getattr(rt, "push_guard", None)
    if pg is None:
        return err("push_guard not initialized", 503)
    return ok(pg.get_status())


async def guard_reset(request: Request):
    """重置风控状态。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    pg = getattr(rt, "push_guard", None)
    if pg is None:
        return err("push_guard not initialized", 503)
    try:
        body = await request.json()
    except Exception:
        body = {}
    channel = (body.get("channel") or "").strip()
    result = pg.reset(channel)
    return ok(result)


async def guard_audit(request: Request):
    """查询风控审计日志。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    pg = getattr(rt, "push_guard", None)
    if pg is None:
        return err("push_guard not initialized", 503)
    limit = int(request.query_params.get("limit", 50))
    group = request.query_params.get("group", "")
    action = request.query_params.get("action", "")
    logs = pg.get_audit(limit=limit, group=group, action=action)
    return ok({"logs": logs, "count": len(logs)})


def routes():
    return [
        Route("/api/guard/status", guard_status, methods=["GET"]),
        Route("/api/guard/reset", guard_reset, methods=["POST"]),
        Route("/api/guard/audit", guard_audit, methods=["GET"]),
    ]
