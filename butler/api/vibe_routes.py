"""Vibe Coding 路由（占位，待实现）。"""
from starlette.routing import Route
from starlette.responses import JSONResponse


async def _health(request):
    return JSONResponse({"ok": True, "msg": "vibe routes placeholder"})


def routes():
    return [
        Route("/api/vibe/health", _health),
    ]
