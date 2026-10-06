"""Notify Router 单例：全局唯一的通知路由实例。"""
from __future__ import annotations

from butler.logging_setup import get_logger
from butler.notify.router import NotifyRouter

logger = get_logger("butler.notify.singleton")

_router: NotifyRouter | None = None


def init_router(bark=None, tts=None, ha=None, tv=None, app=None, tts_queue=None) -> NotifyRouter:
    """初始化全局通知路由。在应用启动时调用一次。"""
    global _router
    if _router is not None:
        logger.warning("NotifyRouter already initialized, skipping")
        return _router
    _router = NotifyRouter(
        bark=bark,
        ha=ha,
        tv=tv,
        app=app,
        tts_queue=tts_queue,
    )
    logger.info("NotifyRouter initialized")
    return _router


def get_router() -> NotifyRouter | None:
    """获取全局通知路由实例。未初始化时返回 None。"""
    return _router
