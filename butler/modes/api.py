"""模式引擎 API：当前模式/切换模式/行为规则/历史。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.modes.engine import MODES, MODE_NAMES
from butler.runtime import get_runtime

logger = get_logger("butler.modes.api")


async def mode_current(request: Request):
    """获取当前模式。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    engine = getattr(rt, "mode_engine", None)
    if engine is None:
        return err("mode engine not initialized", 503)
    return ok(engine.get_state())


async def mode_switch(request: Request):
    """切换模式。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = str(body.get("mode", "")).strip().lower()
    source = str(body.get("source", "api")).strip()
    if not mode:
        return err("mode is required", 400)
    if mode not in MODES:
        return err(f"invalid mode: {mode}, must be one of {list(MODES)}", 400)

    rt = get_runtime()
    engine = getattr(rt, "mode_engine", None)
    if engine is None:
        return err("mode engine not initialized", 503)

    try:
        switched = engine.switch(mode, source)
        return ok({
            "switched": switched,
            "current": engine.get_state(),
        })
    except ValueError as e:
        return err(str(e), 400)


async def mode_rules(request: Request):
    """获取所有模式的行为规则。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    engine = getattr(rt, "mode_engine", None)
    if engine is None:
        return err("mode engine not initialized", 503)
    return ok({
        "modes": [{"id": m, "name": MODE_NAMES[m]} for m in MODES],
        "rules": engine.get_all_rules(),
        "current": engine.current,
    })


async def mode_history(request: Request):
    """模式切换历史（复用 timeseries）。"""
    g = guard(request)
    if g:
        return g
    from butler.timeseries import store as ts
    mode = request.query_params.get("mode") or None
    limit = int(request.query_params.get("limit", 50))
    offset = int(request.query_params.get("offset", 0))
    transitions = ts.get_mode_transitions(mode=mode, limit=limit, offset=offset)
    return ok({"transitions": transitions, "count": len(transitions)})


def routes():
    return [
        Route("/api/modes/current", mode_current, methods=["GET"]),
        Route("/api/modes/switch", mode_switch, methods=["POST"]),
        Route("/api/modes/rules", mode_rules, methods=["GET"]),
        Route("/api/modes/history", mode_history, methods=["GET"]),
    ]
