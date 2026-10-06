"""HA 设备管理 API：查询 HA 设备/实体，用于设备管理界面和按钮触发器匹配。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.runtime import get_runtime


# 常用设备域（前端默认展示这些）
_COMMON_DOMAINS = [
    "light", "switch", "media_player", "climate", "cover", "fan",
    "vacuum", "lock", "camera", "water_heater", "humidifier",
    "binary_sensor", "sensor", "button", "number", "select",
]


async def ha_devices(request: Request):
    """获取 HA 设备列表（按域分组）。
    query 参数：
      domain: 只返回指定域（如 light）
      search: 按 friendly_name 模糊搜索
      online_only: 只返回在线设备（state != unavailable/unknown）
    """
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    ha = getattr(rt, "ha", None)
    if ha is None:
        return err("HA not configured", 503)

    params = request.query_params
    domain_filter = (params.get("domain") or "").strip()
    search = (params.get("search") or "").strip().lower()
    online_only = params.get("online_only") == "true"

    try:
        states = await ha.get_states()
    except Exception as e:
        return err(f"HA connection failed: {e}", 502)

    # 按域分组
    by_domain: dict[str, list[dict]] = {}
    for st in states:
        eid = st.get("entity_id", "")
        if not eid or "." not in eid:
            continue
        domain = eid.split(".")[0]
        if domain_filter and domain != domain_filter:
            continue
        attrs = st.get("attributes") or {}
        name = attrs.get("friendly_name") or eid
        if search and search not in name.lower():
            continue
        state = (st.get("state") or "").lower()
        if online_only and state in ("unavailable", "unknown"):
            continue

        device = {
            "entity_id": eid,
            "name": name,
            "state": st.get("state"),
            "domain": domain,
            "device_class": attrs.get("device_class"),
            "unit_of_measurement": attrs.get("unit_of_measurement"),
            "last_changed": st.get("last_changed"),
        }
        by_domain.setdefault(domain, []).append(device)

    # 按域名称排序，每个域内按 name 排序
    result = {}
    for d in sorted(by_domain.keys()):
        result[d] = sorted(by_domain[d], key=lambda x: x["name"])

    total = sum(len(v) for v in result.values())
    return ok({
        "domains": result,
        "total": total,
        "domain_count": len(result),
        "common_domains": _COMMON_DOMAINS,
    })


async def ha_entity_match(request: Request):
    """按钮触发器 friendly_name 匹配：搜索 HA 实体，返回最匹配的 entity_id。
    query 参数：
      name: friendly_name 关键词（支持模糊匹配）
      domain: 限定域（可选）
    """
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    ha = getattr(rt, "ha", None)
    if ha is None:
        return err("HA not configured", 503)

    params = request.query_params
    name = (params.get("name") or "").strip()
    domain = (params.get("domain") or "").strip()

    if not name:
        return err("name parameter required")

    try:
        states = await ha.get_states()
    except Exception as e:
        return err(f"HA connection failed: {e}", 502)

    # 精确匹配优先，然后模糊匹配
    exact = []
    fuzzy = []
    for st in states:
        eid = st.get("entity_id", "")
        if not eid or "." not in eid:
            continue
        d = eid.split(".")[0]
        if domain and d != domain:
            continue
        attrs = st.get("attributes") or {}
        fname = attrs.get("friendly_name") or eid
        if name == fname:
            exact.append({"entity_id": eid, "name": fname, "domain": d, "match": "exact"})
        elif name.lower() in fname.lower():
            fuzzy.append({"entity_id": eid, "name": fname, "domain": d, "match": "fuzzy"})

    matches = exact + fuzzy[:10]
    return ok({
        "matches": matches,
        "total": len(matches),
        "best_match": matches[0] if matches else None,
    })


async def ha_domains(request: Request):
    """获取 HA 所有域列表（用于前端过滤下拉）。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    ha = getattr(rt, "ha", None)
    if ha is None:
        return err("HA not configured", 503)

    try:
        states = await ha.get_states()
    except Exception as e:
        return err(f"HA connection failed: {e}", 502)

    domains: dict[str, int] = {}
    for st in states:
        eid = st.get("entity_id", "")
        if "." in eid:
            d = eid.split(".")[0]
            domains[d] = domains.get(d, 0) + 1

    sorted_domains = [{"domain": d, "count": c} for d, c in sorted(domains.items(), key=lambda x: -x[1])]
    return ok({"domains": sorted_domains, "total": len(sorted_domains)})


def routes():
    return [
        Route("/api/ha/devices", ha_devices, methods=["GET"]),
        Route("/api/ha/entity-match", ha_entity_match, methods=["GET"]),
        Route("/api/ha/domains", ha_domains, methods=["GET"]),
    ]
