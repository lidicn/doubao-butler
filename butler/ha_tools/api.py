"""HA 动态实体发现 API：索引/发现/状态/动作。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.ha_tools.api")


async def entity_index(request: Request):
    """HA 实体索引（区域/域/设备类统计，~500 tokens）。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    discovery = getattr(rt, "ha_discovery", None)
    if discovery is None:
        return err("ha discovery not initialized", 503)
    index = await discovery.get_entity_index()
    return ok(index)


async def entity_discover(request: Request):
    """按需发现实体：按域/区域/设备类/名称/状态过滤。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    discovery = getattr(rt, "ha_discovery", None)
    if discovery is None:
        return err("ha discovery not initialized", 503)

    domain = request.query_params.get("domain") or None
    area = request.query_params.get("area") or None
    device_class = request.query_params.get("device_class") or None
    name_contains = request.query_params.get("name_contains") or None
    state = request.query_params.get("state") or None
    limit = int(request.query_params.get("limit", 50))

    entities = await discovery.discover_entities(
        domain=domain, area=area, device_class=device_class,
        name_contains=name_contains, state=state, limit=limit,
    )
    return ok({"entities": entities, "count": len(entities)})


async def entity_states(request: Request):
    """查询指定实体的完整状态。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    entity_ids = body.get("entity_ids") or []
    if not entity_ids:
        # 也支持 query 参数
        ids_param = request.query_params.get("entity_ids", "")
        entity_ids = [x.strip() for x in ids_param.split(",") if x.strip()]

    if not entity_ids:
        return err("entity_ids is required", 400)

    rt = get_runtime()
    discovery = getattr(rt, "ha_discovery", None)
    if discovery is None:
        return err("ha discovery not initialized", 503)

    states = await discovery.get_entity_states(entity_ids)
    return ok({"states": states, "count": len(states)})


async def entity_action(request: Request):
    """执行 HA 动作（服务调用）。P0-5 白名单检查。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    entity_id = str(body.get("entity_id", "")).strip()
    service = str(body.get("service", "")).strip()
    domain = body.get("domain") or None
    data = body.get("data") or {}

    if not entity_id or not service:
        return err("entity_id and service are required", 400)

    rt = get_runtime()
    discovery = getattr(rt, "ha_discovery", None)
    if discovery is None:
        return err("ha discovery not initialized", 503)

    result = await discovery.execute_action(entity_id, service, domain=domain, data=data)
    if result.get("ok"):
        return ok(result)
    status = 403 if result.get("blocked") else 500
    return err(result.get("error", "action failed"), status)


# ---- P0-5 白名单 API ----

async def whitelist_list(request: Request):
    """查询 HA 实体白名单。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    wl = getattr(rt, "ha_whitelist", None)
    if wl is None:
        return err("ha whitelist not initialized", 503)
    category = request.query_params.get("category") or None
    enabled_param = request.query_params.get("enabled")
    enabled = None if enabled_param is None else enabled_param.lower() in ("1", "true")
    limit = int(request.query_params.get("limit", 200))
    offset = int(request.query_params.get("offset", 0))
    entities = wl.list(category=category, enabled=enabled, limit=limit, offset=offset)
    stats = wl.count()
    return ok({"entities": entities, "count": len(entities), "stats": stats})


async def whitelist_add(request: Request):
    """添加实体到白名单。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    entity_id = str(body.get("entity_id", "")).strip()
    name = str(body.get("name", "")).strip()
    category = str(body.get("category", "general")).strip()
    if not entity_id:
        return err("entity_id is required", 400)
    if category not in ("general", "security", "climate", "media", "other"):
        return err("category must be general/security/climate/media/other", 400)

    rt = get_runtime()
    wl = getattr(rt, "ha_whitelist", None)
    if wl is None:
        return err("ha whitelist not initialized", 503)

    wl.add(entity_id, name=name, category=category)
    return ok({"entity_id": entity_id, "name": name, "category": category, "added": True})


async def whitelist_remove(request: Request):
    """从白名单移除实体。"""
    g = guard(request)
    if g:
        return g
    entity_id = request.path_params.get("entity_id", "")
    if not entity_id:
        return err("entity_id is required", 400)
    rt = get_runtime()
    wl = getattr(rt, "ha_whitelist", None)
    if wl is None:
        return err("ha whitelist not initialized", 503)
    wl.remove(entity_id)
    return ok({"entity_id": entity_id, "removed": True})


async def whitelist_check(request: Request):
    """检查实体是否允许控制。"""
    g = guard(request)
    if g:
        return g
    entity_id = request.query_params.get("entity_id", "").strip()
    if not entity_id:
        return err("entity_id is required", 400)
    rt = get_runtime()
    wl = getattr(rt, "ha_whitelist", None)
    if wl is None:
        return err("ha whitelist not initialized", 503)
    current_mode = rt.mode_engine.current if hasattr(rt, "mode_engine") else "daily"
    allowed, reason = wl.is_allowed(entity_id, mode=current_mode)
    return ok({"entity_id": entity_id, "allowed": allowed, "reason": reason, "current_mode": current_mode})


def routes():
    return [
        Route("/api/ha/entities/index", entity_index, methods=["GET"]),
        Route("/api/ha/entities/discover", entity_discover, methods=["GET"]),
        Route("/api/ha/entities/states", entity_states, methods=["POST", "GET"]),
        Route("/api/ha/entities/action", entity_action, methods=["POST"]),
        # P0-5 白名单
        Route("/api/ha/whitelist", whitelist_list, methods=["GET"]),
        Route("/api/ha/whitelist/add", whitelist_add, methods=["POST"]),
        Route("/api/ha/whitelist/{entity_id}", whitelist_remove, methods=["DELETE"]),
        Route("/api/ha/whitelist/check", whitelist_check, methods=["GET"]),
    ]
