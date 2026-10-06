"""通知中心 API：列表/标记已读/未读数。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.core import notification_store
from butler.logging_setup import get_logger

logger = get_logger("butler.notification_api")


async def notification_list(request: Request):
    g = guard(request)
    if g:
        return g
    limit = int(request.query_params.get("limit", 50))
    offset = int(request.query_params.get("offset", 0))
    unread_only = request.query_params.get("unread") == "1"
    items = notification_store.list_notifications(limit=limit, offset=offset, unread_only=unread_only)
    unread = notification_store.get_unread_count()
    return ok({"items": items, "unread_count": unread})


async def notification_read(request: Request):
    g = guard(request)
    if g:
        return g
    nid = int(request.path_params.get("id", 0))
    notification_store.mark_read(nid)
    return ok({"id": nid, "is_read": True})


async def notification_read_all(request: Request):
    g = guard(request)
    if g:
        return g
    count = notification_store.mark_all_read()
    return ok({"marked": count})


def routes():
    return [
        Route("/api/notification/list", notification_list, methods=["GET"]),
        Route("/api/notification/{id}/read", notification_read, methods=["POST"]),
        Route("/api/notification/read_all", notification_read_all, methods=["POST"]),
    ]
