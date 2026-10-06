"""Cron Task API 路由（v1.9 Koin Action）。

提供：
- GET /api/cron_task/apis - 列出已注册 API
- POST /api/cron_task/apis - 注册 API
- DELETE /api/cron_task/apis/{api_id} - 注销 API
- GET /api/cron_task/logs - 获取执行日志
- POST /api/cron_task/test/{skill_id} - 手动测试任务
"""
from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.core.cron_validator import validator as cron_validator
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.api.cron_task")


async def list_apis(request: Request):
    """列出已注册 API。"""
    g = guard(request)
    if g:
        return g
    executor = get_runtime().cron_task_executor
    if not executor:
        return err("cron_task executor not initialized")
    return ok(executor.list_apis())


async def register_api(request: Request):
    """注册 API。"""
    g = guard(request)
    if g:
        return g
    executor = get_runtime().cron_task_executor
    if not executor:
        return err("cron_task executor not initialized")
    
    body = await request.json()
    api_id = body.get("api_id")
    url = body.get("url")
    headers = body.get("headers", {})
    timeout = body.get("timeout", 10)
    secret = body.get("secret", "")
    secret_name = body.get("secret_name", "token")
    method = body.get("method", "GET")
    req_body = body.get("body", "")

    if not api_id or not url:
        return err("api_id and url are required")

    executor.register_api(api_id, url, headers, timeout, secret, secret_name, method, req_body)
    return ok({"id": api_id, "url": "(registered)"})


async def unregister_api(request: Request):
    """注销 API。"""
    g = guard(request)
    if g:
        return g
    executor = get_runtime().cron_task_executor
    if not executor:
        return err("cron_task executor not initialized")
    
    api_id = request.path_params.get("api_id")
    executor.unregister_api(api_id)
    return ok()


async def get_logs(request: Request):
    """获取执行日志。"""
    g = guard(request)
    if g:
        return g
    executor = get_runtime().cron_task_executor
    if not executor:
        return err("cron_task executor not initialized")

    skill_id = request.query_params.get("skill_id")
    limit = int(request.query_params.get("limit", 20))
    logs = executor.get_execution_log(skill_id, limit)
    return ok(logs)


async def get_health(request: Request):
    """所有已调度任务的健康度汇总。"""
    g = guard(request)
    if g:
        return g
    executor = get_runtime().cron_task_executor
    if not executor:
        return err("cron_task executor not initialized")
    return ok(executor.get_task_health())


async def get_failure_patterns(request: Request):
    """获取失败模式记录。"""
    g = guard(request)
    if g:
        return g
    executor = get_runtime().cron_task_executor
    if not executor:
        return err("cron_task executor not initialized")
    skill_id = request.query_params.get("skill_id")
    limit = int(request.query_params.get("limit", 50))
    return ok(executor.get_failure_patterns(skill_id, limit))


async def test_task(request: Request):
    """手动测试任务。"""
    g = guard(request)
    if g:
        return g
    executor = get_runtime().cron_task_executor
    if not executor:
        return err("cron_task executor not initialized")
    
    skill_id = request.path_params.get("skill_id")
    # 从技能存储获取技能定义
    runner = get_runtime().runner
    if not runner:
        return err("runner not initialized")
    
    skill = runner.store.get(skill_id)
    if not skill:
        return err(f"skill {skill_id} not found")
    
    result = await executor.execute_task(skill)
    return ok(result)


async def preview_task(request: Request):
    """预览任务（校验 + 生成自然语言描述）。"""
    g = guard(request)
    if g:
        return g
    
    body = await request.json()
    task = body.get("task", {})
    
    # 校验任务
    valid, errors = cron_validator.validate(task)
    
    # 生成预览文本
    preview = cron_validator.generate_preview(task)
    
    return ok({
        "valid": valid,
        "errors": errors,
        "preview": preview,
    })


async def validate_api_url(request: Request):
    """校验 API URL（SSRF 防护检查）。"""
    g = guard(request)
    if g:
        return g
    
    body = await request.json()
    url = body.get("url", "")
    
    valid, error = cron_validator.validate_api_url(url)
    
    return ok({
        "valid": valid,
        "error": error,
    })


async def test_api_direct(request: Request):
    """直接测试已注册 API 连通性（不经过条件/动作）。"""
    g = guard(request)
    if g:
        return g
    executor = get_runtime().cron_task_executor
    if not executor:
        return err("cron_task executor not initialized")
    api_id = request.path_params.get("api_id")
    result = await asyncio.to_thread(executor.test_api, api_id)
    return ok(result)


def routes():
    return [
        Route("/api/cron_task/apis", list_apis, methods=["GET"]),
        Route("/api/cron_task/apis", register_api, methods=["POST"]),
        Route("/api/cron_task/apis/{api_id}", unregister_api, methods=["DELETE"]),
        Route("/api/cron_task/apis/{api_id}/test", test_api_direct, methods=["POST"]),
        Route("/api/cron_task/logs", get_logs, methods=["GET"]),
        Route("/api/cron_task/health", get_health, methods=["GET"]),
        Route("/api/cron_task/failures", get_failure_patterns, methods=["GET"]),
        Route("/api/cron_task/test/{skill_id}", test_task, methods=["POST"]),
        Route("/api/cron_task/preview", preview_task, methods=["POST"]),
        Route("/api/cron_task/validate_api", validate_api_url, methods=["POST"]),
    ]
