"""触发层健康监控 API（v1.8 P0-5）。"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

import asyncio
import logging
import time

from butler.api.deps import ok, err, guard
from butler.runtime import get_runtime

logger = logging.getLogger("butler.triggers.health")

# 批24（表行 8 RUF006 那族）：loop 对被调度任务只持弱引用⇒fire-and-forget 的 task 必须存进
# 这张强引用注册表，跑完由 done_callback 自己摘掉（同批18 dialog._idle_tasks／批21 cron_task）。
_BG_TASKS: set = set()


async def trigger_health(request: Request):
    """获取所有触发源健康状态。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    registry = getattr(rt, "trigger_registry", None)
    if registry is None:
        return err("trigger_registry not initialized", 503)
    return ok(registry.get_health_report())


async def trigger_list(request: Request):
    """列出所有触发源。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    registry = getattr(rt, "trigger_registry", None)
    if registry is None:
        return err("trigger_registry not initialized", 503)
    source_type = request.query_params.get("type", "")
    if source_type:
        sources = registry.list_by_type(source_type)
    else:
        sources = registry.list_all()
    return ok({"sources": [s.to_dict() for s in sources], "count": len(sources)})


async def trigger_pause(request: Request):
    """暂停指定触发源。"""
    g = guard(request)
    if g:
        return g
    source_id = request.path_params.get("source_id", "")
    rt = get_runtime()
    registry = getattr(rt, "trigger_registry", None)
    if registry is None:
        return err("trigger_registry not initialized", 503)

    # 对于 scheduler 类型，同时暂停 APScheduler job
    sched = getattr(rt, "scheduler", None) or getattr(rt, "_sched", None)
    if sched and source_id.startswith("sched:"):
        job_id = source_id.replace("sched:", "")
        try:
            sched.pause_job(job_id)
        except Exception as e:
            logger.debug("pause scheduler job failed: %s", e)

    result = registry.pause(source_id)
    if result:
        return ok({"paused": source_id, "ok": True})
    return err(f"source not found: {source_id}", 404)


async def trigger_resume(request: Request):
    """恢复指定触发源。"""
    g = guard(request)
    if g:
        return g
    source_id = request.path_params.get("source_id", "")
    rt = get_runtime()
    registry = getattr(rt, "trigger_registry", None)
    if registry is None:
        return err("trigger_registry not initialized", 503)

    # 对于 scheduler 类型，同时恢复 APScheduler job
    sched = getattr(rt, "scheduler", None) or getattr(rt, "_sched", None)
    if sched and source_id.startswith("sched:"):
        job_id = source_id.replace("sched:", "")
        try:
            sched.resume_job(job_id)
        except Exception as e:
            logger.debug("resume scheduler job failed: %s", e)

    result = registry.resume(source_id)
    if result:
        return ok({"resumed": source_id, "ok": True})
    return err(f"source not found: {source_id}", 404)


async def trigger_manual_run(request: Request):
    """手动触发指定触发源（测试用）。"""
    g = guard(request)
    if g:
        return g
    source_id = request.path_params.get("source_id", "")
    rt = get_runtime()
    registry = getattr(rt, "trigger_registry", None)
    if registry is None:
        return err("trigger_registry not initialized", 503)

    source = registry.get(source_id)
    if not source:
        return err(f"source not found: {source_id}", 404)

    # 对于 scheduler 类型，手动触发 APScheduler job
    sched = getattr(rt, "scheduler", None) or getattr(rt, "_sched", None)
    if sched and source_id.startswith("sched:"):
        job_id = source_id.replace("sched:", "")
        try:
            sched.modify_job(job_id, next_run_time=None)
            # 立即执行一次
            job = sched.get_job(job_id)
            if job:
                import asyncio
                if asyncio.iscoroutinefunction(job.func):
                    task = asyncio.create_task(job.func())
                    _BG_TASKS.add(task)
                    task.add_done_callback(_BG_TASKS.discard)
                else:
                    job.func()
                return ok({"triggered": source_id, "ok": True, "note": "manual trigger dispatched"})
        except Exception as e:
            return err(f"manual trigger failed: {e}", 500)

    return err(f"manual run not supported for type: {source.type}", 400)


async def trigger_audit_query(request: Request):
    """触发账本查询：心跳流水（trigger_audit）与执行口径计数（trigger_runs）并列回。

    口径按路线图 §14.5 钉死：`records`/`heartbeat` 是调度心跳，⛔ 用来回答「触发/失败多少次」；
    那两个数看 `execution`（butler.db.trigger_runs，失败数已剥掉隔离态人工终态行）。
    `since_hours` 缺省 0＝不限窗 ⇒ 不传时 `heartbeat` 的数字与格4 改名前逐字一致。
    """
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    registry = getattr(rt, "trigger_registry", None)
    if registry is None or registry.auditor is None:
        return err("trigger auditor not initialized", 503)

    raw_hours = request.query_params.get("since_hours", "") or "0"
    try:
        since_hours = float(raw_hours)
    except ValueError:
        return err("since_hours must be a number", 400)
    if since_hours < 0 or since_hours > 24 * 365:
        return err("since_hours must be within 0..8760", 400)

    source_id = request.query_params.get("source_id", "") or None
    result = request.query_params.get("result", "") or None
    limit = int(request.query_params.get("limit", "100"))
    offset = int(request.query_params.get("offset", "0"))
    limit = min(limit, 500)

    now = time.time()
    since_ts = now - since_hours * 3600.0 if since_hours > 0 else None
    records = registry.auditor.query(
        source_id=source_id, result=result,
        limit=limit, offset=offset,
    )
    heartbeat = registry.auditor.get_stats(source_id=source_id, since_ts=since_ts)
    from butler.store import repo
    execution = await asyncio.to_thread(repo.trigger_run_stats_since, since_ts)
    execution_24h = await asyncio.to_thread(repo.trigger_run_stats_since, now - 86400.0)
    try:
        evaluation = await asyncio.to_thread(repo.trigger_evaluation_stats_since, since_ts)
        evaluation_24h = await asyncio.to_thread(repo.trigger_evaluation_stats_since, now - 86400.0)
    except Exception as e:
        # 第三本账读不到（未迁移 / 库被锁）⇒ 带标签的 read_error，⛔ 把整个 audit 拉成 500
        evaluation = {"table": "trigger_evaluations", "caliber": "evaluation",
                      "read_error": "%s: %s" % (type(e).__name__, str(e)[:160]),
                      "caliber_note": repo.EVALUATION_CALIBER_NOTE}
        evaluation_24h = dict(evaluation, since_ts=now - 86400.0)
    return ok({
        "records": records,
        "count": len(records),
        "window_hours": since_hours,
        "caliber_note": heartbeat.get("caliber_note", ""),
        "heartbeat": heartbeat,
        "execution": execution,
        "execution_last_24h": execution_24h,
        "evaluation": evaluation,
        "evaluation_last_24h": evaluation_24h,
    })


def routes():
    return [
        Route("/api/triggers/health", trigger_health, methods=["GET"]),
        Route("/api/triggers/sources", trigger_list, methods=["GET"]),
        Route("/api/triggers/audit", trigger_audit_query, methods=["GET"]),
        Route("/api/triggers/{source_id}/pause", trigger_pause, methods=["POST"]),
        Route("/api/triggers/{source_id}/resume", trigger_resume, methods=["POST"]),
        Route("/api/triggers/{source_id}/run", trigger_manual_run, methods=["POST"]),
    ]
