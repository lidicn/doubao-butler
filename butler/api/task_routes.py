"""任务看板 API：PM 调度 TP/DP 的任务状态跟踪。

端点：
  GET  /api/task/list          任务列表（支持 status/assignee/priority 筛选）
  GET  /api/task/{id}          任务详情（含事件历史）
  GET  /api/task/board         看板视图（按状态分组 + 超时检测）
  POST /api/task/{id}/status   TP/DP 上报状态变更（简单 token 鉴权）
  DELETE /api/task/{id}        PM 删除任务（管理员鉴权）

上报鉴权：Header Authorization: Bearer {task_report_token}（config 中配置，简单固定 token）
"""
from __future__ import annotations

import hmac

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from butler.api.deps import err, guard, note_report_failure, ok, report_locked
from butler.config import get_settings
from butler.logging_setup import get_logger
from butler.store import task_store

logger = get_logger("butler.task_api")


def _check_report_token(request: Request) -> str | None:
    """检查 TP/DP 上报 token。返回 None 表示通过，返回 JSONResponse 表示拒绝。

    WO-ME-215：三条拒绝路径全部计入失败刹车（漏记任何一条都等于给刹车留后门）。
    """
    if report_locked(request):
        return err("too many failed attempts", 429)
    s = get_settings()
    expected = s.task_report_token  # WO-BUT-018: 缺键已在启动期报错；不再用 getattr 默认值兜底成空串
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        note_report_failure(request)
        return err("missing token", 401)
    token = auth[7:].strip()
    if not expected or not token or not hmac.compare_digest(token, expected):
        note_report_failure(request)
        return err("invalid token", 403)
    return None


async def task_list(request: Request):
    """任务列表。"""
    g = guard(request)
    if g:
        return g
    try:
        status = request.query_params.get("status") or None
        assignee = request.query_params.get("assignee") or None
        priority = request.query_params.get("priority") or None
        limit = int(request.query_params.get("limit", 50))
        offset = int(request.query_params.get("offset", 0))
        tasks = task_store.list_tasks(status=status, assignee=assignee,
                                       priority=priority, limit=limit, offset=offset)
        return ok({"tasks": tasks, "count": len(tasks)})
    except Exception as e:
        logger.exception("task_list failed")
        return err(str(e), 500)


async def task_detail(request: Request):
    """任务详情。"""
    g = guard(request)
    if g:
        return g
    task_id = request.path_params.get("id", "")
    task = task_store.get_task(task_id)
    if not task:
        return err("task not found", 404)
    return ok(task)


async def task_board(request: Request):
    """看板视图。"""
    g = guard(request)
    if g:
        return g
    board = task_store.get_board()
    return ok(board)


async def task_update_status(request: Request):
    """TP/DP 上报状态变更（简单 token 鉴权）。"""
    token_err = _check_report_token(request)
    if token_err:
        return token_err
    task_id = request.path_params.get("id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    status = str(body.get("status", "")).strip()
    note = str(body.get("note", "")).strip()
    if not status:
        return err("status is required", 400)
    try:
        task = task_store.update_status(task_id, status, note=note, source="report")
        logger.info("task %s reported status=%s", task_id, status)
        return ok(task)
    except KeyError:
        return err("task not found", 404)
    except ValueError as e:
        return err(f"{e}（可用值：{'/'.join(task_store.VALID_STATUSES)}）", 400)
    except Exception as e:
        logger.exception("task_update_status failed")
        return err(str(e), 500)


async def task_delete(request: Request):
    """PM 删除任务。"""
    g = guard(request)
    if g:
        return g
    task_id = request.path_params.get("id", "")
    try:
        task_store.delete_task(task_id)
        return ok({"deleted": task_id})
    except Exception as e:
        logger.exception("task_delete failed")
        return err(str(e), 500)


def routes():
    return [
        Route("/api/task/list", task_list, methods=["GET"]),
        Route("/api/task/board", task_board, methods=["GET"]),
        Route("/api/task/{id}", task_detail, methods=["GET"]),
        Route("/api/task/{id}/status", task_update_status, methods=["POST"]),
        Route("/api/task/{id}", task_delete, methods=["DELETE"]),
    ]
