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


def routes():
    return [
        Route("/api/agent/chat", chat, methods=["POST"]),
        Route("/api/agent/traces", list_traces, methods=["GET"]),
        Route("/api/agent/traces/{trace_id}", get_trace, methods=["GET"]),
    ]
