"""豆包多模态问候（兼容端点）。

逻辑已迁移为技能实例 `hello`（data/skills/hello.json，引擎 camera_vlm / ma_analyze）。
本路由保留为编排层兼容入口：内部转 runner.run("hello", force=True)，
响应字段（tv_online/person/nickname/scene/greeting/analyze_ok/status）保持不变，
现有 Node-RED flow 无需改动。
"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.hello")


async def hello(request: Request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    runner = getattr(rt, "runner", None)
    if runner is None:
        return err("runner not ready", 503)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    res = await runner.run("hello", source="api", payload=body, force=True)
    meta = res.get("meta") or {}
    return ok({
        "tv_online": bool(meta.get("tv_online")),
        "person": str(meta.get("person") or ""),
        "nickname": str(meta.get("nickname") or ""),
        "scene": str(meta.get("scene") or ""),
        "greeting": str(res.get("text") or ""),
        "analyze_ok": bool(meta.get("analyze_ok")),
        "status": res.get("status", "ok"),
        "skill_id": res.get("skill_id", "hello"),
    })


def routes():
    return [Route("/api/hello", hello, methods=["POST"])]
