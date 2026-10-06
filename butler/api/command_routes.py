"""统一指令中心 API：PM/用户创建指令 → TP/DP 拉取执行 → 状态上报。

解决竞态：所有指令走同一个通道，不再有模拟键盘注入冲突。
"""
from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.routing import Route
from starlette.responses import JSONResponse

from butler.logging_setup import get_logger
from butler.store import command_store as cmd

logger = get_logger("butler.api.command")


def _guard(request: Request):
    from butler.api.deps import guard
    return guard(request)


def _ok(data, **extra):
    from butler.api.deps import ok
    return ok(data, **extra)


def _err(msg, code=400):
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


async def command_create(request: Request):
    """创建指令（PM/用户）。"""
    g = _guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return _err("invalid JSON body")
    target = (body.get("target") or "").strip().upper()
    text = (body.get("text") or "").strip()
    priority = (body.get("priority") or "normal").strip().lower()
    created_by = (body.get("created_by") or "PM").strip()
    meta = body.get("meta") or {}
    if not target or target not in ("TP", "DP", "PM"):
        return _err("target must be TP / DP / PM")
    if not text:
        return _err("text is required")
    result = await asyncio.to_thread(
        cmd.create_command, target, text, priority=priority, created_by=created_by, meta=meta
    )
    return _ok(result)


async def command_pull(request: Request):
    """TP/DP 拉取新指令（pending 状态）。"""
    g = _guard(request)
    if g:
        return g
    target = (request.path_params.get("target") or "").strip().upper()
    limit = int(request.query_params.get("limit", "5"))
    if target not in ("TP", "DP", "PM"):
        return _err("target must be TP / DP / PM")
    items = await asyncio.to_thread(cmd.pull_commands, target, limit=limit)
    return _ok({"items": items, "count": len(items)})


async def command_accept(request: Request):
    """TP/DP 接手指令。"""
    g = _guard(request)
    if g:
        return g
    cmd_id = request.path_params.get("cmd_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    reporter = (body.get("reporter") or "").strip()
    result = await asyncio.to_thread(cmd.accept_command, cmd_id, reporter=reporter)
    if result is None:
        return _err("command not found", 404)
    return _ok(result)


async def command_complete(request: Request):
    """TP/DP 完成指令。"""
    g = _guard(request)
    if g:
        return g
    cmd_id = request.path_params.get("cmd_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    result_text = (body.get("result") or "").strip()
    reporter = (body.get("reporter") or "").strip()
    result = await asyncio.to_thread(cmd.complete_command, cmd_id, result=result_text, reporter=reporter)
    if result is None:
        return _err("command not found", 404)
    return _ok(result)


async def command_fail(request: Request):
    """TP/DP 指令执行失败。"""
    g = _guard(request)
    if g:
        return g
    cmd_id = request.path_params.get("cmd_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    error = (body.get("error") or "").strip()
    reporter = (body.get("reporter") or "").strip()
    result = await asyncio.to_thread(cmd.fail_command, cmd_id, error=error, reporter=reporter)
    if result is None:
        return _err("command not found", 404)
    return _ok(result)


async def command_cancel(request: Request):
    """PM/用户取消指令。"""
    g = _guard(request)
    if g:
        return g
    cmd_id = request.path_params.get("cmd_id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body.get("reason") or "").strip()
    result = await asyncio.to_thread(cmd.cancel_command, cmd_id, reason=reason)
    if result is None:
        return _err("command not found", 404)
    return _ok(result)


async def command_list(request: Request):
    """列出指令（可按 target/status 过滤）。"""
    g = _guard(request)
    if g:
        return g
    target = (request.query_params.get("target") or "").strip().upper()
    status = (request.query_params.get("status") or "").strip().lower()
    limit = int(request.query_params.get("limit", "50"))
    offset = int(request.query_params.get("offset", "0"))
    result = await asyncio.to_thread(
        cmd.list_commands, target=target, status=status, limit=limit, offset=offset
    )
    return _ok(result)


async def command_get(request: Request):
    """查询单个指令详情。"""
    g = _guard(request)
    if g:
        return g
    cmd_id = request.path_params.get("cmd_id", "")
    result = await asyncio.to_thread(cmd.get_command, cmd_id)
    if result is None:
        return _err("command not found", 404)
    events = await asyncio.to_thread(cmd.get_command_events, cmd_id)
    result["events"] = events
    return _ok(result)


async def command_pending_count(request: Request):
    """查询待处理指令数量。"""
    g = _guard(request)
    if g:
        return g
    target = (request.query_params.get("target") or "").strip().upper()
    count = await asyncio.to_thread(cmd.get_pending_count, target)
    return _ok({"pending_count": count, "target": target or "all"})


def routes():
    return [
        Route("/api/commands", command_create, methods=["POST"]),
        Route("/api/commands", command_list, methods=["GET"]),
        Route("/api/commands/pending-count", command_pending_count, methods=["GET"]),
        Route("/api/commands/pull/{target}", command_pull, methods=["GET"]),
        Route("/api/commands/{cmd_id}", command_get, methods=["GET"]),
        Route("/api/commands/{cmd_id}/accept", command_accept, methods=["POST"]),
        Route("/api/commands/{cmd_id}/complete", command_complete, methods=["POST"]),
        Route("/api/commands/{cmd_id}/fail", command_fail, methods=["POST"]),
        Route("/api/commands/{cmd_id}/cancel", command_cancel, methods=["POST"]),
    ]
