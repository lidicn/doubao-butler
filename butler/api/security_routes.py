"""Security Monitor API 路由"""
import time
from starlette.requests import Request
from starlette.routing import Route
from butler.api.deps import ok, guard
from butler.runtime import get_runtime


def _get_or_init_monitor():
    """获取或懒初始化 SecurityMonitor（仅创建对象，不调 check()，不推进冷却）。"""
    rt = get_runtime()
    monitor = getattr(rt, "_security_monitor", None)
    if monitor is None:
        from butler.core.security_monitor import SecurityMonitor
        rt._security_monitor = SecurityMonitor(rt)
        monitor = rt._security_monitor
    return monitor


async def get_security_status(request: Request):
    """获取安全监控状态（只读：读快照，不调 check()）。"""
    g = guard(request)
    if g:
        return g
    monitor = _get_or_init_monitor()
    if monitor is None:
        return ok({"available": False, "reason": "security_monitor not initialized"})

    last_check_time = getattr(monitor, "_last_check_time", 0.0)
    last_result = getattr(monitor, "_last_check_result", [])
    cooldown = getattr(monitor, "_last_alert", {})
    return ok({
        "available": True,
        "last_check_time": last_check_time,
        "last_check_age_sec": round(time.time() - last_check_time, 1) if last_check_time else None,
        "last_check_alerts_count": len(last_result),
        "cooldown_active_count": len(cooldown),
    })


async def get_security_alerts(request: Request):
    """获取最近一次安全检查的告警列表（只读快照，不调 check()）。"""
    g = guard(request)
    if g:
        return g
    monitor = _get_or_init_monitor()
    if monitor is None:
        return ok({"alerts": [], "available": False})

    last_result = getattr(monitor, "_last_check_result", [])
    last_check_time = getattr(monitor, "_last_check_time", 0.0)
    return ok({
        "alerts": last_result,
        "available": True,
        "last_check_time": last_check_time,
        "source": "snapshot (read-only, from 15-min job)",
    })


def routes():
    return [
        Route("/api/security/status", endpoint=get_security_status, methods=["GET"]),
        Route("/api/security/alerts", endpoint=get_security_alerts, methods=["GET"]),
    ]
