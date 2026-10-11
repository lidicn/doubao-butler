"""系统路由：健康检查、状态总览、版本。"""
from __future__ import annotations

from starlette.routing import Route

from butler.api.deps import guard, ok
from butler.runtime import get_runtime
from butler.store import ledger_freshness


def _mqtt_connected(rt) -> bool:
    """MQTT 活着的唯一定义：对象在 + connected 事件已置位。health/status 两腿共用（⛔ 各写一遍）。"""
    return bool(rt and rt.mqtt and rt.mqtt.connected.is_set())


def _scheduler_up(rt) -> bool:
    """调度器活着的唯一定义：lifespan 挂上的 `scheduler`（梯到 `_sched`，与 triggers/health_api.py 同形）。"""
    if (getattr(rt, "scheduler", None) or getattr(rt, "_sched", None)) is None:
        return False
    # V37：注册阶段少了作业＝调度器「在」但缺腿，⛔ 面板开全绿
    return not getattr(rt, "scheduler_job_failures", None)


def _core_components(rt) -> dict:
    comps = {"mqtt": _mqtt_connected(rt), "scheduler": _scheduler_up(rt)}
    # 契约 v2.0 §A/B：adm 对端离线 → degraded + ADM_ERR_PEER_OFFLINE
    peers = getattr(rt, "adm_peers", None)
    if isinstance(peers, dict):
        for peer_topic, online in peers.items():
            peer_name = peer_topic.split("/")[-2] if "/" in peer_topic else peer_topic
            comps[f"adm_{peer_name}"] = bool(online)
    return comps


async def health(request):
    # 状态码恒 200：Dockerfile HEALTHCHECK 只认 200，翻码＝容器 unhealthy（翻码是裁点，台账 §38-6）。
    # 但 body ⛔ 说谎：V33 报的就是「MQTT 断了、调度器没起来，监控一片绿」。
    rt = get_runtime()
    comps = _core_components(rt)
    return ok({"online": True,
               "degraded": not all(comps.values()),
               "components": comps})


async def status(request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    s = rt.settings
    mqtt_ok = _mqtt_connected(rt)
    tv_ok = False
    if rt.tv:
        try:
            tv_ok = await rt.tv.health()
        except Exception:
            tv_ok = False
    mem = getattr(rt, "memory", None)
    return ok(
        {
            "state": rt.state.snapshot().__dict__ if rt.state else None,
            "connections": {
                "mqtt": mqtt_ok,
                "tv": tv_ok,
                "llm": bool(s.doubao_api_key),
                "memory_agent": bool(s.memory_agent_token),
                # O-3：四个鉴权面各自的配置位（只报有无，⛔ 值）。改 .env 后在此核对。
                "memory_agent_surfaces": {
                    "mcp": bool(s.memory_agent_token),
                    "butler": bool(s.memory_agent_butler_token),
                    "app": bool(s.memory_agent_app_token),
                    "basic": bool(s.memory_agent_user and s.memory_agent_pass),
                },
                # 上一行是"配置里有 token"，这一行才是"熔断没开闸"（WO-ADM-001 R-32 健康位）
                "memory_agent_healthy": bool(getattr(mem, "is_healthy", False)),
                "ha": bool(s.ha_token),
            },
            "members": [m.__dict__ for m in s.persona.members],
            "scheduler_job_failures": getattr(rt, "scheduler_job_failures", None),
            "ledgers": ledger_freshness.snapshot(rt),
        }
    )


async def version(request):
    import butler

    return ok({"version": butler.__version__, "name": "豆包管家"})


def routes():
    return [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/status", status, methods=["GET"]),
        Route("/api/version", version, methods=["GET"]),
    ]
