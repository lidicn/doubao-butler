"""时序数据引擎：初始化存储 + 注册 API 路由。

v1.7 P0-2：管家侧时序数据存储（模式切换/设备异常/家电运行）。
行为习惯学习复用 MA member-schedule，不在本模块实现。
"""
from __future__ import annotations

from butler.logging_setup import get_logger

logger = get_logger("butler.timeseries")


class TimeSeriesEngine:
    """时序数据引擎。初始化时建表，提供 API 路由。"""

    def __init__(self):
        # 触发建表
        from butler.timeseries import store
        store.get_conn()
        logger.info("TimeSeriesEngine initialized")

    def routes(self):
        from butler.timeseries.api import routes as _routes
        return _routes()
