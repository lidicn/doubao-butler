"""时序数据 API：模式切换/设备异常/家电运行时长 查询。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.timeseries import store as ts

logger = get_logger("butler.timeseries.api")


async def mode_list(request: Request):
    """模式切换历史。"""
    g = guard(request)
    if g:
        return g
    mode = request.query_params.get("mode") or None
    limit = int(request.query_params.get("limit", 100))
    offset = int(request.query_params.get("offset", 0))
    transitions = ts.get_mode_transitions(mode=mode, limit=limit, offset=offset)
    current = ts.get_current_mode()
    return ok({"transitions": transitions, "current_mode": current, "count": len(transitions)})


async def mode_record(request: Request):
    """手动记录模式切换（通常由 trigger 规则层自动调用）。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = str(body.get("mode", "")).strip()
    action = str(body.get("action", "")).strip()
    source = str(body.get("source", "api")).strip()
    if not mode or not action:
        return err("mode and action are required", 400)
    try:
        rec_id = ts.record_mode_transition(mode, action, source)
        return ok({"id": rec_id, "mode": mode, "action": action})
    except ValueError as e:
        return err(str(e), 400)


async def anomaly_list(request: Request):
    """设备异常事件列表。"""
    g = guard(request)
    if g:
        return g
    resolved_param = request.query_params.get("resolved")
    resolved = None if resolved_param is None else resolved_param.lower() in ("1", "true", "yes")
    severity = request.query_params.get("severity") or None
    limit = int(request.query_params.get("limit", 100))
    offset = int(request.query_params.get("offset", 0))
    anomalies = ts.get_device_anomalies(resolved=resolved, severity=severity, limit=limit, offset=offset)
    unresolved_count = len(ts.get_device_anomalies(resolved=False, limit=1000))
    return ok({"anomalies": anomalies, "unresolved_count": unresolved_count, "count": len(anomalies)})


async def anomaly_resolve(request: Request):
    """标记异常为已解决。"""
    g = guard(request)
    if g:
        return g
    anomaly_id = int(request.path_params.get("id", 0))
    ts.resolve_anomaly(anomaly_id)
    return ok({"id": anomaly_id, "resolved": True})


async def appliance_runtime(request: Request):
    """家电运行时长查询。"""
    g = guard(request)
    if g:
        return g
    entity_id = request.query_params.get("entity_id", "").strip()
    days = int(request.query_params.get("days", 7))
    if not entity_id:
        return err("entity_id is required", 400)
    records = ts.get_appliance_runtime(entity_id, days=days)
    total = ts.get_appliance_total_runtime(entity_id, days=days)
    return ok({
        "entity_id": entity_id,
        "days": days,
        "total_minutes": round(total, 1),
        "total_hours": round(total / 60, 2),
        "records": records,
        "count": len(records),
    })


def routes():
    return [
        Route("/api/timeseries/modes", mode_list, methods=["GET"]),
        Route("/api/timeseries/modes", mode_record, methods=["POST"]),
        Route("/api/timeseries/anomalies", anomaly_list, methods=["GET"]),
        Route("/api/timeseries/anomalies/{id}/resolve", anomaly_resolve, methods=["POST"]),
        Route("/api/timeseries/appliance/runtime", appliance_runtime, methods=["GET"]),
    ]
