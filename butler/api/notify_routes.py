"""推送通知路由：手动推送 TTS + 弹窗通知到 TV 端（MQTT cmd/notify）。

载荷组装：优先合成 TTS 拿到 tts_url 一并下发；头像按推送成员映射 /avatars/{member}.png，
缺失时由静态路由回退到默认管家头像，电视端可安全加载。
"""
from __future__ import annotations

import asyncio
import io
import re
from pathlib import Path

from PIL import Image
from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.runtime import get_runtime
from butler.store import repo
from butler.notify.singleton import get_router as get_notify_router
from butler.logging_setup import get_logger

logger = get_logger("butler.notify")

from butler.notify.router import ALLOWED_TYPES as _ALLOWED_TYPES
_AVA_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


async def notify(request):
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    title = str(body.get("title", "")).strip()
    content = str(body.get("content", "")).strip()
    if not title or not content:
        return err("title 与 content 必填")
    member = str(body.get("member", "")).strip()
    ntype = str(body.get("type", "info")).strip().lower()
    if ntype not in _ALLOWED_TYPES:
        ntype = "info"
    important = bool(body.get("important", False))
    with_tts = bool(body.get("tts", True))
    volume = int(body.get("volume", 80))
    pause_media = bool(body.get("pause_media", False))
    voice = body.get("voice") or None
    duration = int(body.get("duration", 8000)) if not important else 0

    rt = get_runtime()
    tts_url = ""
    status = "ok"
    if with_tts and content:
        try:
            res = await rt.tts.synthesize(content, member=member, voice=voice)
            if res:
                tts_url = res.public_url
        except Exception as e:
            logger.warning("notify tts failed: %s", e)
    avatar_url = f"{rt.settings.base_url.rstrip('/')}/avatars/{(member or 'doubao')}.png"

    payload = {
        "title": title,
        "content": content,
        "type": ntype,
        "duration": duration,
        "important": important,
        "tts_url": tts_url,
        "tts_volume": volume,
        "pause_media": pause_media,
        "avatar_url": avatar_url,
    }
    router = get_notify_router()
    if router is None:
        status = "fail"
        logger.warning("notify publish skipped: router unavailable")
    else:
        try:
            res = await router.notify("tv", content, tv_payload=payload)
            if not res.results["tv"].ok:
                status = "fail"
                logger.warning("notify publish failed: %s", res.results["tv"].error)
        except Exception as e:
            status = "fail"
            logger.warning("notify publish failed: %s", e)

    await asyncio.to_thread(
        repo.add_notify,
        member or "doubao", title, content, type=ntype, important=important,
        duration=duration, with_tts=with_tts, volume=volume, pause_media=pause_media,
        status=status, tts_url=tts_url,
    )
    return ok({"status": status, "tts_url": tts_url, "avatar_url": avatar_url})


async def notify_history(request):
    g = guard(request)
    if g:
        return g
    limit = int(request.query_params.get("limit", "20"))
    rows = await asyncio.to_thread(repo.recent_notifies, limit)
    return ok({"items": rows})


async def notify_delete(request):
    g = guard(request)
    if g:
        return g
    nid = int(request.path_params.get("id", "0"))
    ok_del = await asyncio.to_thread(repo.delete_notify, nid)
    return ok({"deleted": ok_del})


async def avatar_upload(request: Request):
    """上传头像：POST /api/avatars/{name}，multipart 单文件，保存为 data/avatars/{name}.png。"""
    g = guard(request)
    if g:
        return g
    name = request.path_params.get("name", "")
    if not _AVA_RE.match(name):
        return err("invalid name")
    try:
        form = await request.form()
    except Exception as e:
        logger.warning("avatar upload parse form error: %s", e)
        return err("invalid form")
    file = form.get("file")
    if not file or not getattr(file, "filename", ""):
        return err("no file")
    try:
        data = await file.read()
        if len(data) > 4 * 1024 * 1024:
            return err("image too large")
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
        img.thumbnail((512, 512), Image.LANCZOS)
    except Exception as e:
        logger.warning("avatar upload invalid image: %s", e)
        return err("invalid image")
    s = get_runtime().settings
    out_dir = Path(s.data_dir) / "avatars"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.png"
    try:
        img.save(out_path, "PNG")
    except Exception as e:
        logger.warning("avatar save failed: %s", e)
        return err("save failed")
    url = f"{s.base_url.rstrip('/')}/avatars/{name}.png"
    logger.info("avatar uploaded name=%s path=%s", name, out_path)
    return ok({"name": name, "url": url})


async def avatar_list(request: Request):
    """列出已上传头像。"""
    g = guard(request)
    if g:
        return g
    s = get_runtime().settings
    d = Path(s.data_dir) / "avatars"
    items = []
    if d.exists():
        for f in sorted(d.glob("*.png")):
            items.append({"name": f.stem, "url": f"{s.base_url.rstrip('/')}/avatars/{f.stem}.png"})
    return ok({"items": items})


def routes():
    return [
        Route("/api/notify", notify, methods=["POST"]),
        Route("/api/notify/history", notify_history, methods=["GET"]),
        Route("/api/notify/{id:int}", notify_delete, methods=["DELETE"]),
        Route("/api/avatars", avatar_list, methods=["GET"]),
        Route("/api/avatars/{name}", avatar_upload, methods=["POST"]),
    ]
