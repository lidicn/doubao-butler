"""Persona API 路由"""
from starlette.requests import Request
from starlette.routing import Route
from butler.api.deps import ok, guard
from butler.runtime import get_runtime


async def get_persona(request: Request):
    """获取当前人格"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    persona = getattr(rt, "persona", None)
    if persona is None:
        return ok({"available": False, "reason": "persona not initialized"})
    
    soul = getattr(persona, "soul", "")
    user = getattr(persona, "user", "")
    memory = getattr(persona, "memory", "")
    
    return ok({
        "available": True,
        "soul": soul[:500] if soul else "",
        "user": user[:500] if user else "",
        "memory": memory[:500] if memory else "",
    })


def routes():
    return [
        Route("/api/persona/", endpoint=get_persona, methods=["GET"]),
    ]
