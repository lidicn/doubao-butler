"""主动问询 API：发起/回复/列表/待回复。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.proactive.api")


async def inquiry_ask(request: Request):
    """发起主动问询（通常由 trigger 规则或技能调用，也可 API 直接调用）。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    event_key = str(body.get("event_key", "")).strip()
    title = str(body.get("title", "")).strip()
    question = str(body.get("question", "")).strip()
    options = body.get("options") or []
    context = body.get("context") or {}
    tts_device = body.get("tts_device") or None
    cooldown_hours = int(body.get("cooldown_hours", 24))

    if not event_key or not title or not question:
        return err("event_key, title and question are required", 400)

    rt = get_runtime()
    engine = getattr(rt, "proactive_engine", None)
    if engine is None:
        return err("proactive engine not initialized", 503)

    inquiry = await engine.ask(
        event_key=event_key, title=title, question=question,
        options=options, context=context, tts_device=tts_device,
        cooldown_hours=cooldown_hours,
    )
    if inquiry is None:
        return ok({"skipped": True, "reason": "disabled by mode or already asked"})
    return ok({
        "id": inquiry.id,
        "event_key": inquiry.event_key,
        "title": inquiry.title,
        "status": inquiry.status,
        "expires_at": inquiry.expires_at,
    })


async def inquiry_answer(request: Request):
    """用户回复问询。"""
    g = guard(request)
    if g:
        return g
    inquiry_id = request.path_params.get("id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    answer = str(body.get("answer", "")).strip()
    if not inquiry_id or not answer:
        return err("id and answer are required", 400)

    rt = get_runtime()
    engine = getattr(rt, "proactive_engine", None)
    if engine is None:
        return err("proactive engine not initialized", 503)

    result = await engine.answer(inquiry_id, answer)
    if result.get("ok"):
        return ok(result)
    return err(result.get("error", "answer failed"), 400)


async def inquiry_pending(request: Request):
    """获取待回复的问询。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    engine = getattr(rt, "proactive_engine", None)
    if engine is None:
        return err("proactive engine not initialized", 503)
    limit = int(request.query_params.get("limit", 10))
    pending = engine.get_pending(limit=limit)
    return ok({"pending": pending, "count": len(pending)})


async def inquiry_list(request: Request):
    """问询历史。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    engine = getattr(rt, "proactive_engine", None)
    if engine is None:
        return err("proactive engine not initialized", 503)
    status = request.query_params.get("status") or None
    limit = int(request.query_params.get("limit", 50))
    offset = int(request.query_params.get("offset", 0))
    inquiries = engine.list(status=status, limit=limit, offset=offset)
    return ok({"inquiries": inquiries, "count": len(inquiries)})


# ---- P1-2 场景库 API ----

async def scene_list(request: Request):
    """获取主动问询场景列表。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    engine = getattr(rt, "proactive_engine", None)
    if engine is None:
        return err("proactive engine not initialized", 503)
    enabled_only = request.query_params.get("enabled_only", "true").lower() == "true"
    scenes = engine.get_scenes(enabled_only=enabled_only)
    return ok({"scenes": scenes, "count": len(scenes)})


async def scene_trigger(request: Request):
    """手动触发场景检查（通常由定时任务调用）。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    engine = getattr(rt, "proactive_engine", None)
    if engine is None:
        return err("proactive engine not initialized", 503)
    triggered = await engine.check_scenes()
    return ok({"triggered": triggered, "count": len(triggered)})


async def budget_status(request: Request):
    """v2.8: 各角色主动服务预算快照（只读，供管理面/巡检消费）。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    engine = getattr(rt, "proactive_engine", None)
    if engine is None:
        return err("proactive engine not initialized", 503)
    from butler.config import get_settings
    s = get_settings()
    roles = {"butler", *s.proactive_role_limits.keys()}
    only = request.query_params.get("role_id", "").strip()
    if only:
        roles = {only}
    try:
        for r in engine.list(limit=200):
            if r.get("role_id"):
                roles.add(str(r["role_id"]))
    except Exception as e:
        # P2-9 ②类（批42 组9）：列举一失败 roles 就只剩配置里那几个，⛔ 无声＝面板少列没人知道
        logger.warning("proactive budget roles listing failed: %s", e)
    items = [engine.budget_snapshot(r) for r in sorted(roles)]
    return ok({"items": items, "negative_words": s.proactive_negative_words})


async def inquiry_feedback(request: Request):
    """v2.8: 显式标注一次反馈（negative 即触发降频）。"""
    g = guard(request)
    if g:
        return g
    inquiry_id = request.path_params.get("id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    kind = str(body.get("kind", "")).strip()
    if not inquiry_id or not kind:
        return err("id and kind are required", 400)
    rt = get_runtime()
    engine = getattr(rt, "proactive_engine", None)
    if engine is None:
        return err("proactive engine not initialized", 503)
    result = engine.set_feedback(inquiry_id, kind)
    if result.get("ok"):
        return ok(result)
    return err(result.get("error", "feedback failed"), 400)


def routes():
    return [
        Route("/api/proactive/ask", inquiry_ask, methods=["POST"]),
        Route("/api/proactive/{id}/answer", inquiry_answer, methods=["POST"]),
        Route("/api/proactive/pending", inquiry_pending, methods=["GET"]),
        Route("/api/proactive/list", inquiry_list, methods=["GET"]),
        # P1-2 场景库
        Route("/api/proactive/scenes", scene_list, methods=["GET"]),
        Route("/api/proactive/scenes/trigger", scene_trigger, methods=["POST"]),
        Route("/api/proactive/budget", budget_status, methods=["GET"]),
        Route("/api/proactive/{id}/feedback", inquiry_feedback, methods=["POST"]),
    ]
