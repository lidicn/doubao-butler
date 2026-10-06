"""Bark 推送路由：供外部应用（小甜菜等）调用管家 Bark 通道推送消息到手机。"""
from __future__ import annotations

from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.integrations.bark import BARK_SOUNDS
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.api.bark")


async def bark_push(request):
    """Bark 推送。供小甜菜/TVPilot/DeskPilot 等外部应用调用。

    Body:
      body (str, required): 推送内容
      title (str, optional): 推送标题，默认"豆包管家"
      subtitle (str, optional): 推送副标题
      level (str, optional): 推送级别：active(默认)/timeSensitive/critical/passive
      sound (str, optional): 铃声，如 alarm/bell/minuet/silence 等
      volume (int, optional): 重要警告音量 0-10（仅 level=critical 时生效）
      role (str, optional): 角色名，自动添加标题前缀和头像
      url (str, optional): 点击通知跳转的 URL
      group (str, optional): 通知分组
      icon (str, optional): 通知图标 URL
      image (str, optional): 通知图片 URL
      badge (int, optional): 推送角标数字
      call (bool, optional): 铃声重复播放（默认 false）
      is_archive (bool, optional): 是否保存到历史记录
      ttl (int, optional): 历史记录有效期（秒）
    """
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")

    text = str(body.get("body", "")).strip()
    if not text:
        return err("body required")

    role = str(body.get("role", "")).strip()
    title = str(body.get("title", "")).strip() or "豆包管家"
    subtitle = str(body.get("subtitle", "")).strip() or None
    level = str(body.get("level", "active")).strip()
    sound = str(body.get("sound", "")).strip() or None
    volume = body.get("volume")
    if volume is not None:
        try:
            volume = int(volume)
        except (ValueError, TypeError):
            volume = None
    url = str(body.get("url", "")).strip() or None
    group = str(body.get("group", "")).strip() or None
    icon = str(body.get("icon", "")).strip() or None
    image = str(body.get("image", "")).strip() or None
    badge = body.get("badge")
    if badge is not None:
        try:
            badge = int(badge)
        except (ValueError, TypeError):
            badge = None
    call = bool(body.get("call", False))
    is_archive = body.get("is_archive")
    if is_archive is not None:
        is_archive = bool(is_archive)
    ttl = body.get("ttl")
    if ttl is not None:
        try:
            ttl = int(ttl)
        except (ValueError, TypeError):
            ttl = None

    # 角色标题前缀
    if role and not title.startswith("【"):
        title = f"【{role}】{title}"

    # 角色头像：未显式指定 icon 时自动用角色默认头像
    from butler.api.tts_routes import ROLE_ICONS
    if not icon and role:
        icon = ROLE_ICONS.get(role)

    # 相对路径转完整 URL（Bark 服务端需要拉取图片）
    if icon and icon.startswith("/"):
        import os
        base = os.environ.get("BUTLER_BASE_URL", "http://192.168.2.200:8095")
        icon = base.rstrip("/") + icon

    rt = get_runtime()
    if rt.bark is None:
        return ok({"pushed": False, "reason": "bark_not_configured"})

    from butler.notify.singleton import get_router as get_notify_router
    router = get_notify_router()
    if router is None:
        return ok({"pushed": False, "reason": "router_unavailable"})

    try:
        res = await router.notify(
            "bark", text, title=title,
            trace_id=str(body.get("trace_id", "")).strip(),
            bark_kwargs=dict(
                subtitle=subtitle, level=level,
                sound=sound, volume=volume,
                icon=icon, image=image, url=url, group=group,
                badge=badge, call=call, is_archive=is_archive, ttl=ttl,
            ),
        )
        success = bool(res.results["bark"].ok)
        return ok({
            "pushed": success,
            "title": title,
            "body": text,
            "level": level,
            "role": role or None,
            "icon": icon,
            "sound": sound,
            "group": group,
        })
    except Exception as e:
        logger.warning("bark push api failed: %s", e)
        return ok({"pushed": False, "reason": str(e)})


async def bark_status(request):
    """Bark 配置状态（不泄露 key）。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    s = rt.settings
    return ok({
        "configured": bool(s.bark_url),
        "has_key": bool(s.bark_key),
        "base_url": s.bark_url[:30] + "..." if s.bark_url else None,
        "available_sounds": BARK_SOUNDS,
    })


def routes():
    return [
        Route("/api/bark/push", bark_push, methods=["POST"]),
        Route("/api/bark/status", bark_status, methods=["GET"]),
    ]
