"""人员状态 API：当前用户位置、场景推断、活动时间线

【未接线半成品 · DCD 20261008-DB孤儿资产处置 裁乙】
- 未注册：butler/app.py 零挂载点，GET /api/people/status 现网 404
- 无消费方：前端/脚本 0 调用
- 数据腿缺：perception_engine 全仓无赋值落点
- 形制分叉：全仓唯一 fastapi 路由（其余 34 枚 starlette）；⛔ 为其加依赖
真上线前先按 starlette 重写并接 perception_engine。
"""
from __future__ import annotations

import time
from fastapi import APIRouter, Request

from butler.logging_setup import get_logger
from butler.api.deps import ok

logger = get_logger("butler.api.people")

router = APIRouter()


@router.get("/api/people/status")
async def people_status(request: Request):
    """人员状态：谁在家、在哪房间、当前活动"""
    rt = request.app.state.runtime

    # 1. 当前用户位置（从 user_location）
    try:
        from butler.core.user_location import get_current_room
        current_room = get_current_room()
    except Exception:
        current_room = "未知"

    # 2. 场景推断（从 perception_engine）
    try:
        pe = rt.perception_engine
        matches = pe.check()
        current_scene = matches[0]["scene"] if matches else "未识别"
        scene_infer = matches[0]["infer"] if matches else ""
    except Exception as e:
        logger.warning("perception check failed: %s", e)
        current_scene = "未识别"
        scene_infer = ""

    # 3. 最近事件（从 event_stream）
    try:
        es = rt.event_stream
        recent_events = es.get_recent(seconds=3600)  # 最近1小时
    except Exception:
        recent_events = []

    return ok({
        "current_room": current_room,
        "current_scene": current_scene,
        "scene_infer": scene_infer,
        "recent_events": recent_events[-20:],  # 最近20条
        "updated_at": time.time(),
    })
