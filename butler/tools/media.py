"""媒体播放域（从 butler/core/tools.py 拆分）。"""
from __future__ import annotations

import asyncio
import time

from butler.logging_setup import get_logger

logger = get_logger("butler.tools")

async def _play_music(agent, args: dict) -> str:
    query = args.get("query", "").strip()
    room = args.get("room", "")
    if not query:
        return "想听什么歌？告诉我歌名或歌手。"
    try:
        entity = None
        devices = getattr(agent, "devices", None)
        if devices and room:
            for d in devices.all():
                if room in (d.room or "") or room in (d.name or ""):
                    entity = d.ha_player_entity
                    break
        where = room
        if not entity:
            entity = "media_player.xiaomi_lx06_a137_play_control"
            where = "默认音箱"      # 落默认箱就报默认箱，⛔ 把用户点的房间念给他
            logger.info("play_music 房间=%s 未匹配到设备，落默认音箱 %s", room, entity)
        res = await agent.ha.call_service("media_player", "play_media", {
            "entity_id": entity,
            "media_content_id": query,
            "media_content_type": "music"
        })
        return f"已在{where or '默认房间'}播放「{query}」：{res}"
    except Exception as e:
        return f"播放音乐失败：{e}"


async def _switch_channel(agent, args: dict) -> str:
    """切换客厅电视到指定频道（IPTV）：经 MQTT 给 zap-tv 设备控制层下发意图。
    zap-tv 容器按频道名/频道号匹配 channels.json（键序即 mytv 数字键）并执行 adb 换台，
    换台前自动确保 mytv 在前台、被 LMK 杀自动重试。频道号单一真源在 zap-tv，避免与映射表不一致。"""
    channel = (args.get("channel") or "").strip()
    if not channel:
        return "换到哪个台？告诉我频道名或频道号。"
    tv = getattr(agent, "tv", None)
    if tv is None:
        return "换台失败：电视控制客户端未初始化"
    try:
        ok, result = await tv.zap(channel)
        if ok:
            no = result.get("no")
            return f"已切换到 {result.get('channel') or channel}" + (f"（第{no}台）" if no else "")
        err = result.get("error") or result.get("reason") or "电视未确认"
        return f"换台失败：{err}"
    except Exception as e:
        return f"换台失败：{e}"
