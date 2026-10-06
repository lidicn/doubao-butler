"""MCP HTTP 传输端点（v1.8 P0-7）。

薄 MCP 的 HTTP 传输实现：
- POST /api/mcp：接收 JSON-RPC 请求，返回 JSON-RPC 响应
- 鉴权：复用管家现有 Bearer Token 体系（WO-ME-206 后浏览器侧改走登录会话 cookie）
- 审计：每次 MCP 调用记录到触发审计（source_type=mcp）

不实现：SSE 流式推送、会话管理（薄 MCP，无状态）。
后续版本可按需添加 Streamable HTTP 完整支持。
"""
from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from butler.api.deps import ok, err, guard
from butler.logging_setup import get_logger
from butler.mcp.server import MCPServer

logger = get_logger("butler.mcp.http")

# 全局 MCP Server 实例（延迟初始化）
_mcp_server: MCPServer | None = None


def get_mcp_server() -> MCPServer:
    """获取全局 MCP Server 实例（延迟初始化）。"""
    global _mcp_server
    if _mcp_server is None:
        _mcp_server = MCPServer()
        logger.info("MCP server initialized (thin MCP, 6 tools + 3 resources)")
    return _mcp_server


async def mcp_endpoint(request: Request):
    """MCP HTTP 端点（POST /api/mcp）。

    接收 JSON-RPC 请求，处理后返回 JSON-RPC 响应。
    鉴权：复用管家现有 Bearer Token。
    """
    g = guard(request)
    if g:
        return g

    try:
        body = await request.json()
    except Exception:
        return err("Invalid JSON body", 400)

    if not isinstance(body, dict):
        return err("Request body must be a JSON object", 400)

    method = body.get("method", "")
    req_id = body.get("id")

    # 日志记录（不记录完整 arguments，避免敏感信息）
    logger.info("MCP request: method=%s id=%s", method, req_id)

    server = get_mcp_server()
    response = await server.handle_request(body)

    # notification 不需要响应
    if response is None:
        return JSONResponse({}, status_code=202)

    return JSONResponse(response)


async def mcp_capabilities(request: Request):
    """MCP 能力发现端点（GET /api/mcp/capabilities）。

    简化版能力发现，不需要完整 JSON-RPC 握手即可查看可用工具和资源。
    用于外部 agent 快速了解管家 MCP 能力。
    """
    g = guard(request)
    if g:
        return g

    from butler.mcp.schema_gen import get_tools, get_resources

    return ok({
        "server": "doubao-butler",
        "version": "1.8.0",
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
        },
        "tools": get_tools(),
        "resources": get_resources(),
        "transport": "http",
        "endpoint": "/api/mcp",
        "auth": "Bearer Token (same as butler API)",
    })


def routes():
    return [
        Route("/api/mcp", mcp_endpoint, methods=["POST"]),
        Route("/api/mcp/capabilities", mcp_capabilities, methods=["GET"]),
    ]
