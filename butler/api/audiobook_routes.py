"""电子书播放 API 路由。

GET  /api/audiobook/books          书架列表
GET  /api/audiobook/status         当前播放状态
POST /api/audiobook/play           播放 {book_name, device_id?}
POST /api/audiobook/pause          暂停
POST /api/audiobook/resume         继续
POST /api/audiobook/stop           停止
POST /api/audiobook/next           下一章
POST /api/audiobook/prev           上一章
GET  /api/audiobook/progress       所有书籍进度
GET  /api/audiobook/progress/{id}  单本书进度
"""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from butler.api.deps import check_auth, err, ok
from butler.logging_setup import get_logger

logger = get_logger("butler.api.audiobook_routes")


def routes():
    return [
        Route("/api/audiobook/books", _books, methods=["GET"]),
        Route("/api/audiobook/status", _status, methods=["GET"]),
        Route("/api/audiobook/play", _play, methods=["POST"]),
        Route("/api/audiobook/pause", _pause, methods=["POST"]),
        Route("/api/audiobook/resume", _resume, methods=["POST"]),
        Route("/api/audiobook/stop", _stop, methods=["POST"]),
        Route("/api/audiobook/next", _next, methods=["POST"]),
        Route("/api/audiobook/prev", _prev, methods=["POST"]),
        Route("/api/audiobook/progress", _progress_list, methods=["GET"]),
    ]


def _mgr(request: Request):
    return request.app.state.audiobook_mgr


async def _books(request: Request):
    if not check_auth(request):
        return err("未登录", 401)
    mgr = _mgr(request)
    books = mgr.scan_library()
    # 附带进度
    for b in books:
        prog = mgr.load_progress(b["id"])
        b["progress"] = prog.to_dict() if prog else None
    return ok({"books": books})


async def _status(request: Request):
    if not check_auth(request):
        return err("未登录", 401)
    mgr = _mgr(request)
    return ok(mgr.status())


async def _play(request: Request):
    if not check_auth(request):
        return err("未登录", 401)
    body = await request.json()
    book_name = (body.get("book_name") or "").strip()
    device_id = (body.get("device_id") or "").strip()
    if not book_name:
        return err("缺少 book_name")
    mgr = _mgr(request)
    result = await mgr.play(book_name, device_id)
    return ok(result) if result.get("ok") else err(result.get("error", "播放失败"))


async def _pause(request: Request):
    if not check_auth(request):
        return err("未登录", 401)
    mgr = _mgr(request)
    result = await mgr.pause()
    return ok(result) if result.get("ok") else err(result.get("error", "暂停失败"))


async def _resume(request: Request):
    if not check_auth(request):
        return err("未登录", 401)
    mgr = _mgr(request)
    result = await mgr.resume()
    return ok(result) if result.get("ok") else err(result.get("error", "继续失败"))


async def _stop(request: Request):
    if not check_auth(request):
        return err("未登录", 401)
    mgr = _mgr(request)
    result = await mgr.stop()
    return ok(result) if result.get("ok") else err(result.get("error", "停止失败"))


async def _next(request: Request):
    if not check_auth(request):
        return err("未登录", 401)
    mgr = _mgr(request)
    result = await mgr.next_chapter()
    return ok(result) if result.get("ok") else err(result.get("error", "切换失败"))


async def _prev(request: Request):
    if not check_auth(request):
        return err("未登录", 401)
    mgr = _mgr(request)
    result = await mgr.prev_chapter()
    return ok(result) if result.get("ok") else err(result.get("error", "切换失败"))


async def _progress_list(request: Request):
    if not check_auth(request):
        return err("未登录", 401)
    mgr = _mgr(request)
    prog_dir = mgr.progress_dir
    results = []
    if prog_dir.exists():
        for f in prog_dir.glob("*.json"):
            try:
                results.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception as e:
                # P2-9 ②类（批42 组10）：一枚坏进度文件让整页少一条进度，⛔ 无声＝书架与盘上对不上没人知道
                logger.warning("audiobook progress entry unparsable [%s]: %s", f.name, e)
    return ok({"progress": results})


import json  # noqa: E402  (放在文件末尾避免循环导入问题)
