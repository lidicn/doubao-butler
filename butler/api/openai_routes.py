"""OpenAI 兼容端点：让 mi-gpt / autoflow / memory-agent 把 butler 当作一个 LLM 后端来调。

- GET  /v1/models
- POST /v1/chat/completions  （非流式；stream=true 退化为单块 SSE，满足大多客户端）

这样 butler 既是「大脑」，也是对外的 openai 兼容 LLM（原计划 A3）。
mi-gpt 把 BASE_URL 指向本端点、API key 用 butler 的 web_password 即可直接对接小爱音箱。
"""
from __future__ import annotations

import json
import time
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from butler.api.deps import err, guard
from butler.runtime import get_runtime


def _extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "".join(parts)
    return ""


async def models(request):
    g = guard(request)
    if g:
        return g
    agent = get_runtime().agent
    if agent is None:
        return err("agent not ready")
    return JSONResponse({
        "object": "list",
        "data": [{"id": agent.s.new_api_model, "object": "model", "owned_by": "butler"}],
    })


async def chat_completions(request):
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    messages = body.get("messages") or []
    text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            text = _extract_text(m.get("content"))
            break
    if not text:
        return err("no user message")
    stream = bool(body.get("stream"))
    history = [
        {
            "role": ("user" if m["role"] == "user" else "assistant"),
            "content": _extract_text(m.get("content")),
        }
        for m in messages[:-1]
        if m.get("role") in ("user", "assistant")
    ]
    agent = get_runtime().agent
    if agent is None:
        return err("agent not ready")
    reply = await agent.run(text, "", history, source="xiaoai")
    model = agent.s.new_api_model
    if stream:
        async def gen():
            cid = "chatcmpl-" + uuid.uuid4().hex
            yield "data: " + json.dumps({
                "id": cid,
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply}, "finish_reason": None}],
            }, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return JSONResponse({
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })


def routes():
    return [
        Route("/v1/models", models, methods=["GET"]),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    ]
