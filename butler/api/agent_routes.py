"""Agent HTTP 入口：供 Node-RED / autoflow 以同步方式调用 butler 大脑。

请求：POST /api/agent/chat  {"text": "...", "member": "Kevin", "history": [...]}
响应：{"ok": true, "reply": "...", "member": "...", "trace_id": "..."}

轨迹查询：
  GET  /api/agent/traces?limit=50&member=Kevin&status=ok
  GET  /api/agent/traces/{trace_id}
"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.agent_trace import AgentTracer
from butler.api.deps import err, guard, ok
from butler.runtime import get_runtime


async def chat(request):
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    text = str(body.get("text", "")).strip()
    if not text:
        return err("text required")
    member = str(body.get("member", ""))
    source_in = str(body.get("source", "webui"))
    history = body.get("history") or []
    agent = get_runtime().agent
    if agent is None:
        return err("agent not ready")
    reply = await agent.run(text, member, history, source=source_in)
    return ok({"reply": reply, "member": member, "trace_id": agent.last_trace_id})


async def list_traces(request):
    g = guard(request)
    if g:
        return g
    try:
        limit = int(request.query_params.get("limit", 50))
        limit = max(1, min(limit, 200))
    except ValueError:
        limit = 50
    member = str(request.query_params.get("member", ""))
    status = str(request.query_params.get("status", ""))
    rows = AgentTracer.list_traces(limit=limit, member=member, status=status)
    return ok({"traces": rows, "count": len(rows)})


async def get_trace(request):
    g = guard(request)
    if g:
        return g
    trace_id = request.path_params.get("trace_id", "")
    if not trace_id:
        return err("trace_id required")
    row = AgentTracer.get_trace(trace_id)
    if row is None:
        return err("trace not found", code=404)
    return ok(row)


async def list_fast_routes(request):
    """列出当前所有快速路由规则。"""
    g = guard(request)
    if g:
        return g
    agent = get_runtime().agent
    if agent is None:
        return err("agent not ready")
    return ok({"routes": agent.fast_routes.list_routes()})


async def analyze_fast_routes(request):
    """分析 LLM 轨迹，生成候选快速路由规则。"""
    g = guard(request)
    if g:
        return g
    from butler.core.self_evolve import analyze_llm_traces
    try:
        min_count = int(request.query_params.get("min_count", 3))
    except ValueError:
        min_count = 3
    candidates = analyze_llm_traces(min_count=min_count)
    return ok({"candidates": candidates, "count": len(candidates)})


async def add_fast_route(request):
    """添加新的快速路由规则。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    agent = get_runtime().agent
    if agent is None:
        return err("agent not ready")
    agent.fast_routes.add_route(body)
    return ok({"ok": True})


async def toggle_fast_route(request):
    """启用/禁用规则。"""
    g = guard(request)
    if g:
        return g
    route_id = request.path_params.get("route_id", "")
    try:
        body = await request.json()
        enabled = bool(body.get("enabled", True))
    except Exception:
        enabled = True
    agent = get_runtime().agent
    if agent is None:
        return err("agent not ready")
    ok_flag = agent.fast_routes.toggle_route(route_id, enabled)
    return ok({"ok": ok_flag})


async def delete_fast_route(request):
    """删除规则。"""
    g = guard(request)
    if g:
        return g
    route_id = request.path_params.get("route_id", "")
    agent = get_runtime().agent
    if agent is None:
        return err("agent not ready")
    ok_flag = agent.fast_routes.delete_route(route_id)
    return ok({"ok": ok_flag})


async def list_aliases(request):
    """列出所有设备别名。"""
    g = guard(request)
    if g:
        return g
    from butler.core.aliases import get_alias_store
    store = get_alias_store()
    return ok({"aliases": store.list_aliases()})


async def delete_alias(request):
    """删除别名。"""
    g = guard(request)
    if g:
        return g
    alias = request.path_params.get("alias", "")
    from butler.core.aliases import get_alias_store
    store = get_alias_store()
    store.delete_alias(alias)
    return ok({"deleted": alias})


async def analyze_failures(request):
    """分析失败模式。"""
    g = guard(request)
    if g:
        return g
    from butler.core.failure_learn import analyze_failures
    days = int(request.query_params.get("days", 7))
    data = analyze_failures(days=days)
    return ok(data)


async def infer_scenes(request):
    """推断场景模式。"""
    g = guard(request)
    if g:
        return g
    from butler.core.scene_infer import infer_scenes
    days = int(request.query_params.get("days", 7))
    data = infer_scenes(days=days)
    return ok(data)


async def list_failure_reports(request):
    """列出历史失败分析报告。"""
    g = guard(request)
    if g:
        return g
    from butler.core.failure_learn import list_failure_reports
    reports = list_failure_reports(limit=10)
    return ok({"reports": reports})


async def list_scene_reports(request):
    """列出历史场景推断报告。"""
    g = guard(request)
    if g:
        return g
    from butler.core.scene_infer import list_scene_reports
    reports = list_scene_reports(limit=10)
    return ok({"reports": reports})


async def events_recent(request):
    """最近 N 秒的事件流（供 MA 分析）。"""
    g = guard(request)
    if g:
        return g
    seconds = int(request.query_params.get("seconds", 600))
    rt = get_runtime()
    es = getattr(rt, "event_stream", None)
    if not es:
        return ok({"events": [], "note": "event_stream not running"})
    events = es.get_recent(seconds=seconds)
    return ok({"events": events, "count": len(events)})


async def state_current(request):
    """当前家庭状态快照。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    es = getattr(rt, "event_stream", None)
    if not es:
        return ok({"state": {}, "note": "event_stream not running"})
    state = es.get_current_state()
    return ok({"state": state, "count": len(state)})


async def perception_analyze(request):
    """分析事件流发现行为模式。"""
    g = guard(request)
    if g:
        return g
    from butler.core.perception_learn import analyze_patterns
    result = analyze_patterns(days=7)
    return ok(result)


async def perception_reports(request):
    """列出历史感知分析报告。"""
    g = guard(request)
    if g:
        return g
    from butler.core.perception_learn import list_perception_reports
    reports = list_perception_reports(limit=10)
    return ok(reports)


async def perception_candidates(request):
    """列出候选规则。"""
    g = guard(request)
    if g:
        return g
    from butler.core.perception_learn import list_candidate_rules
    status = request.query_params.get("status", "pending")
    rules = list_candidate_rules(status=status)
    return ok(rules)


async def perception_update_candidate(request):
    """更新候选规则状态。"""
    g = guard(request)
    if g:
        return g
    from butler.core.perception_learn import update_candidate_rule
    rule_id = int(request.path_params["rule_id"])
    body = await request.json()
    status = body.get("status", "approved")
    ok_ = update_candidate_rule(rule_id, status)
    return ok({"updated": ok_})


async def perception_llm_analyze(request):
    """LLM 深度分析事件流。"""
    g = guard(request)
    if g:
        return g
    from butler.core.perception_learn import llm_analyze_patterns
    result = await llm_analyze_patterns(days=7)
    return ok(result)


def routes():
    return [
        Route("/api/agent/chat", chat, methods=["POST"]),
        Route("/api/agent/traces", list_traces, methods=["GET"]),
        Route("/api/agent/traces/{trace_id}", get_trace, methods=["GET"]),
        Route("/api/agent/fast-routes", list_fast_routes, methods=["GET"]),
        Route("/api/agent/fast-routes/analyze", analyze_fast_routes, methods=["GET"]),
        Route("/api/agent/fast-routes", add_fast_route, methods=["POST"]),
        Route("/api/agent/fast-routes/{route_id}", toggle_fast_route, methods=["POST"]),
        Route("/api/agent/fast-routes/{route_id}", delete_fast_route, methods=["DELETE"]),
        Route("/api/agent/aliases", list_aliases, methods=["GET"]),
        Route("/api/agent/aliases/{alias}", delete_alias, methods=["DELETE"]),
        Route("/api/agent/failures/analyze", analyze_failures, methods=["GET"]),
        Route("/api/agent/failures/reports", list_failure_reports, methods=["GET"]),
        Route("/api/agent/scenes/infer", infer_scenes, methods=["GET"]),
        Route("/api/agent/scenes/reports", list_scene_reports, methods=["GET"]),
        Route("/api/events/recent", events_recent, methods=["GET"]),
        Route("/api/state/current", state_current, methods=["GET"]),
        Route("/api/perception/analyze", perception_analyze, methods=["GET"]),
        Route("/api/perception/llm-analyze", perception_llm_analyze, methods=["POST"]),
        Route("/api/perception/reports", perception_reports, methods=["GET"]),
        Route("/api/perception/candidates", perception_candidates, methods=["GET"]),
        Route("/api/perception/candidates/{rule_id}", perception_update_candidate, methods=["PUT"]),
    ]
