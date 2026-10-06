"""决策请示 API：创建/列表/投票/关闭。P0-3 增强：Bark 推送 + 回调机制。"""
from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.core import decision_store
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.decision_api")

# 批24（表行 8 RUF006 那族）：loop 对被调度任务只持弱引用⇒fire-and-forget 的 task 必须存进
# 这张强引用注册表，跑完由 done_callback 自己摘掉（同批18 dialog._idle_tasks／批21 cron_task）。
_BG_TASKS: set = set()

def _spawn(coro) -> None:
    """起一条后台任务并持强引用（跑完自动摘除）。"""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)

# 看板决策页面 URL（用户点击 Bark 通知后打开）
DASHBOARD_DECISION_URL = "/#decision"


def _build_bark_body(dec: dict) -> str:
    """构建 Bark 推送正文：描述 + 选项列表。"""
    parts = []
    if dec.get("description"):
        parts.append(dec["description"])
    options = dec.get("options", [])
    if options:
        opt_strs = [f"{chr(65+i)}. {opt}" for i, opt in enumerate(options)]
        parts.append("\n" + "\n".join(opt_strs))
    parts.append(f"\n决策ID: {dec['id']}")
    return "".join(parts)


async def _push_decision_to_bark(dec: dict) -> bool:
    """创建决策后推送到 Bark。"""
    rt = get_runtime()
    if not rt.bark:
        logger.debug("bark not configured, skip push for decision %s", dec["id"])
        return False
    channel = dec.get("channel", "bark")
    if channel not in ("bark", "both"):
        return False
    try:
        body = _build_bark_body(dec)
        level = "timeSensitive" if dec.get("priority") == "urgent" else "active"
        # 看板 URL：用户点击后打开看板决策页
        dashboard_url = getattr(rt.settings, "public_url", "") or ""
        url = f"{dashboard_url}{DASHBOARD_DECISION_URL}?id={dec['id']}" if dashboard_url else None
        result = await rt.bark.push(
            body=body,
            title=f"【决策请示】{dec['title']}",
            level=level,
            group="decision",
            url=url,
        )
        logger.info("decision %s pushed to bark: %s", dec["id"], result)
        return result
    except Exception as e:
        logger.warning("decision %s bark push failed: %s", dec["id"], e)
        return False


async def _callback_decision(dec: dict) -> bool:
    """用户投票后回调请求方（重试 3 次）。"""
    callback_url = dec.get("callback_url")
    if not callback_url:
        return False
    try:
        import httpx
        payload = {
            "decision_id": dec["id"],
            "selected": dec.get("choice_text"),
            "choice_index": dec.get("choice"),
            "title": dec["title"],
            "status": dec["status"],
        }
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(callback_url, json=payload)
                    if resp.status_code < 400:
                        logger.info("decision %s callback success (attempt %d)", dec["id"], attempt + 1)
                        return True
                    logger.warning("decision %s callback HTTP %d (attempt %d)",
                                   dec["id"], resp.status_code, attempt + 1)
            except Exception as e:
                logger.warning("decision %s callback error (attempt %d): %s",
                               dec["id"], attempt + 1, e)
            await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s 退避
        logger.error("decision %s callback failed after 3 attempts", dec["id"])
        return False
    except ImportError:
        logger.warning("httpx not available, skip callback for decision %s", dec["id"])
        return False


async def decision_list(request: Request):
    g = guard(request)
    if g:
        return g
    status = request.query_params.get("status") or None
    limit = int(request.query_params.get("limit", 50))
    offset = int(request.query_params.get("offset", 0))
    decisions = decision_store.list_decisions(status=status, limit=limit, offset=offset)
    pending = decision_store.get_pending_count()
    return ok({"decisions": decisions, "pending_count": pending})


async def decision_detail(request: Request):
    g = guard(request)
    if g:
        return g
    dec_id = request.path_params.get("id", "")
    dec = decision_store.get_decision(dec_id)
    if not dec:
        return err("decision not found", 404)
    return ok(dec)


async def decision_vote(request: Request):
    """用户投票（PWA 调用）。投票后自动回调请求方。"""
    g = guard(request)
    if g:
        return g
    dec_id = request.path_params.get("id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    choice = body.get("choice")
    if choice is None:
        return err("choice is required", 400)
    try:
        dec = decision_store.vote_decision(dec_id, int(choice))
        logger.info("decision %s voted: %s", dec_id, dec.get("choice_text"))
        # 异步执行回调（不阻塞响应）
        if dec.get("callback_url"):
            _spawn(_callback_decision(dec))
        return ok(dec)
    except KeyError:
        return err("decision not found", 404)
    except ValueError as e:
        return err(str(e), 400)


async def decision_create(request: Request):
    """PM 创建决策（管家工具调用，也可 API 直接调用）。创建后自动推送 Bark。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = str(body.get("title", "")).strip()
    description = str(body.get("description", "")).strip()
    options = body.get("options") or []
    priority = str(body.get("priority", "normal")).strip()
    callback_url = body.get("callback_url") or None
    channel = str(body.get("channel", "bark")).strip()
    if not title:
        return err("title is required", 400)
    if not isinstance(options, list) or len(options) < 2:
        return err("at least 2 options required", 400)
    try:
        dec = decision_store.create_decision(
            title, description, options, priority,
            callback_url=callback_url, channel=channel,
        )
        # 异步推送 Bark（不阻塞响应）
        _spawn(_push_decision_to_bark(dec))
        return ok(dec)
    except ValueError as e:
        return err(str(e), 400)


async def decision_cancel(request: Request):
    """取消决策（PM 操作）。"""
    g = guard(request)
    if g:
        return g
    dec_id = request.path_params.get("id", "")
    try:
        dec = decision_store.close_decision(dec_id)
        return ok(dec)
    except Exception as e:
        return err(str(e), 400)


def routes():
    return [
        Route("/api/decision/list", decision_list, methods=["GET"]),
        Route("/api/decision/create", decision_create, methods=["POST"]),
        Route("/api/decision/{id}", decision_detail, methods=["GET"]),
        Route("/api/decision/{id}/vote", decision_vote, methods=["POST"]),
        Route("/api/decision/{id}/cancel", decision_cancel, methods=["POST"]),
    ]
