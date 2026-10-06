"""TTS 路由：统一播报（支持角色/房间/设备）、试听合成、引擎信息。
音频文件由 app 静态挂载 /tts/ 提供。
"""
from __future__ import annotations

from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.api.tts")

# 角色音色映射（edge-tts 音色名）。角色独占，选了即锁定。
ROLE_VOICES = {
    "豆包管家": "zh-CN-YunyangNeural",   # 云扬（新闻男声；edge-tts 无晓晓在此值上）
    "管家": "zh-CN-YunyangNeural",
    "butler": "zh-CN-YunyangNeural",
    "小爱": "zh-CN-XiaoyiNeural",           # 晓伊（活泼少女，家庭设备管理员）
    "xiaoai": "zh-CN-XiaoyiNeural",
    "小甜菜": "zh-CN-XiaohanNeural",         # 晓涵（开发助理，温柔女声）
    "xiaotiancai": "zh-CN-XiaohanNeural",
    "lidicn": "zh-CN-YunxiNeural",          # 云希（青年男声）
    "kevin": "zh-CN-YunyangNeural",         # 云扬（新闻男声）
    "emily": "zh-CN-XiaomoNeural",           # 晓墨（儿童女声）
    "jarvis": "zh-CN-YunyangNeural",        # 云扬（书房贾维斯，新闻男声）
}

# 角色播报前缀（如【豆包管家】）
ROLE_PREFIX = {
    "豆包管家": "【豆包管家】",
    "管家": "【豆包管家】",
    "小爱": "【小爱】",
    "xiaoai": "【小爱】",
    "小甜菜": "【小甜菜】",
    "xiaotiancai": "【小甜菜】",
}

# 角色头像（Bark 通知图标，PNG 格式，Bark 不支持 SVG）
ROLE_ICONS = {
    "豆包管家": "/avatars/butler.png",
    "管家": "/avatars/butler.png",
    "butler": "/avatars/butler.png",
    "小爱": "/avatars/xiaoai.png",
    "xiaoai": "/avatars/xiaoai.png",
    "小甜菜": "/avatars/xiaotiancai.png",
    "xiaotiancai": "/avatars/xiaotiancai.png",
    "lidicn": "/avatars/lidicn.png",
    "kevin": "/avatars/kevin.png",
    "emily": "/avatars/emily.png",
}

# 角色→用户映射（用于房间级 TTS 路由）
# 系统角色（豆包管家/小爱/小甜菜）没有对应用户，不做定位路由
ROLE_TO_USER = {
    "lidicn": "lidicn",
    "kevin": "kevin",
    "emily": "emily",
}

# 房间级 TTS 路由：定位置信度阈值
PRESENCE_CONFIDENCE_THRESHOLD = 0.6


def _resolve_voice(role: str = "", voice: str | None = None) -> str | None:
    """根据角色/显式音色确定最终音色。优先级：voice > role > None。"""
    if voice:
        return voice
    if role:
        return ROLE_VOICES.get(role)
    return None


def _find_device_by_room(rt, room: str) -> object | None:
    """按房间名查找第一个启用的播放设备。"""
    if not rt.devices:
        return None
    room_norm = room.strip()
    for dev in rt.devices.all():
        if dev.enabled and dev.room.strip() == room_norm:
            return dev
    return None


def _resolve_room_by_presence(rt, role: str) -> str | None:
    """基于角色对应用户的实时定位，返回目标房间名。

    Returns:
        房间名（如 "客厅"/"书房"），定位不可用时返回 None
    """
    user_id = ROLE_TO_USER.get(role)
    if not user_id:
        return None
    engine = getattr(rt, "presence_engine", None)
    if engine is None:
        return None
    presence = engine.get_user_presence(user_id)
    if presence is None or not presence.room:
        return None
    if presence.confidence < PRESENCE_CONFIDENCE_THRESHOLD:
        logger.debug("tts route: user=%s room=%s confidence=%.2f < %.2f, skip presence routing",
                     user_id, presence.room, presence.confidence, PRESENCE_CONFIDENCE_THRESHOLD)
        return None
    # presence.room 是 room_id（如 "living_room"），需要转成中文房间名
    room_name = engine.get_room_name(presence.room) or presence.room
    logger.info("tts route: role=%s user=%s → room=%s (conf=%.2f)",
                role, user_id, room_name, presence.confidence)
    return room_name


async def _speak_to_device(rt, dev, text: str, voice: str | None, tts_backend: str = "edge-tts") -> dict:
    """播放到指定设备。小米音箱：管家合成音频→取消静音→小米云端直连播放（音色可控）。"""
    if dev.type == "xiaomi":
        # 0. 小爱引擎：直接让音箱朗读（不需要合成音频，无循环问题，用音箱默认音色）
        if tts_backend == "xiaoai":
            player_entity = dev.ha_player_entity
            if not rt.ha or not player_entity:
                return {"spoken": False, "reason": "ha_or_player_unavailable", "device": dev.id}
            result = await rt.ha.intelligent_speaker(player_entity, text)
            ok_spoken = result.startswith("ok")
            return {"spoken": ok_spoken, "device": dev.id, "room": dev.room,
                    "channel": "xiaoai_intelligent_speaker", "backend": "xiaoai",
                    "note": "小爱音箱直读，默认音色"}

        # 1. 管家合成音频（角色音色可控）
        res = await rt.tts.synthesize(text, voice=voice, backend=tts_backend)
        if res is None:
            return {"spoken": False, "reason": "tts_unavailable", "device": dev.id}

        player_entity = dev.ha_player_entity
        if not rt.ha or not player_entity:
            return {"spoken": False, "reason": "ha_or_player_unavailable", "device": dev.id}

        # 2. 从 media_player 实体属性取 xiaoai_id（UUID 格式）
        xiaoai_id = await rt.ha.get_xiaoai_id(player_entity)
        if not xiaoai_id:
            logger.warning("xiaoai_id not found for %s (%s), fallback to HA play_media", dev.id, player_entity)
            # 兜底：HA media_player 路径
            await rt.ha.call_service("media_player", "volume_mute",
                                      {"entity_id": player_entity, "is_volume_muted": False})
            play_result = await rt.ha.tts_play_url(res.public_url, player_entity)
            return {"spoken": play_result.startswith("ok"), "device": dev.id, "room": dev.room,
                    "channel": "ha_play_media_fallback", "url": res.public_url, "provider": res.provider}

        # 3. 小米云端直连播放（X08A 等只认 player_play_music，固件级单曲循环）
        xmethod = getattr(dev, "xiaomi_play", None) or "url"
        play_result = await rt.ha.play_xiaomi_url_once(xiaoai_id, res.public_url, method=xmethod)
        if play_result.get("code", -1) != 0:
            logger.warning("xiaomi direct to %s failed (code=%s), fallback to HA play_media",
                           dev.id, play_result.get("code"))
            # 兜底：HA media_player 路径
            await rt.ha.call_service("media_player", "volume_mute",
                                      {"entity_id": player_entity, "is_volume_muted": False})
            fb_result = await rt.ha.tts_play_url(res.public_url, player_entity)
            return {"spoken": fb_result.startswith("ok"), "device": dev.id, "room": dev.room,
                    "channel": "ha_play_media_fallback", "url": res.public_url,
                    "provider": res.provider, "voice": voice,
                    "note": "xiaomi direct failed, using HA fallback"}

        # 4. 精确停止：轮询 position/duration，在 duration-800ms 时发 player_play_operation stop
        if xmethod == "music":
            estimated_sec = max(2.0, len(text) / 4.0 + 1.0)
            try:
                rt.ha.schedule_xiaomi_stop(xiaoai_id, estimated_sec)
            except Exception as e:
                logger.warning("schedule_xiaomi_stop failed: %s", e)

        return {"spoken": True, "device": dev.id, "room": dev.room,
                "channel": "xiaomi_direct", "url": res.public_url,
                "provider": res.provider, "voice": voice, "xiaoai_id": xiaoai_id[:8] + "..."}

    if dev.type == "tv":
        # 电视：管家合成音频后播放
        res = await rt.tts.synthesize(text, voice=voice, backend=tts_backend)
        if res is None:
            return {"spoken": False, "reason": "tts_unavailable", "device": dev.id}
        rt.tv.play_url(res.public_url)
        return {"spoken": True, "device": dev.id, "room": dev.room, "channel": "tv_play_url",
                "url": res.public_url, "provider": res.provider}

    return {"spoken": False, "reason": f"unsupported_device_type:{dev.type}", "device": dev.id}


async def speak(request):
    """统一 TTS 播报接口。支持角色音色、房间/设备指定、基于定位的房间级路由。

    Body:
      text (str, required): 播报文本
      role (str, optional): 角色名（豆包管家/小爱/lidicn/kevin/emily），决定音色和前缀
      room (str, optional): 目标房间（客厅/书房/主卧等），查找该房间第一个启用设备
      device_id (str, optional): 直接指定设备 ID（优先级高于 room）
      voice (str, optional): 显式指定 edge-tts 音色（优先级最高）
      title (str, optional): 播报前缀（如【豆包管家】），默认按角色自动添加
      no_prefix (bool, optional): 不添加角色前缀（默认 false）
      no_presence_route (bool, optional): 禁用基于定位的房间级路由（默认 false）
    """
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")

    text = str(body.get("text", "")).strip()
    if not text:
        return err("text required")

    role = str(body.get("role", "")).strip()
    room = str(body.get("room", "")).strip()
    device_id = str(body.get("device_id", "")).strip()
    voice = body.get("voice") or None
    title = str(body.get("title", "")).strip()
    no_prefix = bool(body.get("no_prefix", False))
    no_presence_route = bool(body.get("no_presence_route", False))
    backend = str(body.get("backend", "")).strip() or None

    rt = get_runtime()

    # 1. 确定音色
    final_voice = _resolve_voice(role, voice)

    # 2. 组装播报文本（加角色前缀）
    speak_text = text
    if not no_prefix:
        prefix = title or ROLE_PREFIX.get(role, "")
        if prefix and not text.startswith(prefix):
            speak_text = f"{prefix}{text}"

    # 3. 确定目标设备
    target_dev = None
    routing_source = "explicit"
    if device_id and rt.devices:
        target_dev = rt.devices.get(device_id)
        if target_dev and not target_dev.enabled:
            return ok({"spoken": False, "reason": "device_disabled", "device_id": device_id})
    elif room and rt.devices:
        target_dev = _find_device_by_room(rt, room)
        routing_source = "room_param"
    elif role and not no_presence_route:
        # P0-2 房间级 TTS 路由：基于角色对应用户的实时定位
        presence_room = _resolve_room_by_presence(rt, role)
        if presence_room:
            target_dev = _find_device_by_room(rt, presence_room)
            if target_dev:
                routing_source = "presence"
                room = presence_room

    # 4. 确定角色引擎（小爱角色用 xiaoai 引擎直读）
    role_backend = backend or "edge-tts"
    if role and rt.roles:
        # 按名称或 ID 匹配角色
        role_obj = rt.roles.get(role)
        if not role_obj:
            for r in rt.roles.all():
                if r.name == role or r.id == role:
                    role_obj = r
                    break
        if role_obj and getattr(role_obj, "tts_backend", None) == "xiaoai":
            role_backend = "xiaoai"

    # 5. 播放前 PushGuard 风控检查（v1.8 新增）
    pg = getattr(rt, "push_guard", None)
    if pg is not None:
        tts_decision = pg.check_tts(
            text=speak_text,
            device=device_id or (target_dev.id if target_dev else ""),
            room=room or "",
            priority="warning" if role == "小爱" else "info",
        )
        if tts_decision.action == "drop":
            logger.info("tts dropped by push_guard: %s (%s)", speak_text[:30], tts_decision.reason)
            return ok({"spoken": False, "reason": "push_guard_drop", "detail": tts_decision.reason})

    # 6. 播放
    if target_dev:
        result = await _speak_to_device(rt, target_dev, speak_text, final_voice, tts_backend=role_backend)
        # R2-04 fix: 播报完成后出队 + 释放锁
        if pg is not None:
            pg.tts_pop()
            pg.tts_unlock()
        result["role"] = role or None
        result["voice"] = final_voice
        result["routing_source"] = routing_source
        if routing_source == "presence":
            result["presence_room"] = room
        return ok(result)

    # 6. 未指定设备 → 默认播放到电视（兼容旧行为）
    res = await rt.tts.synthesize(speak_text, voice=final_voice, backend=role_backend)
    if pg is not None:
        pg.tts_pop()
        pg.tts_unlock()
    if res is None:
        return ok({"spoken": False, "reason": "tts_unavailable", "role": role or None})
    rt.tv.play_url(res.public_url)
    return ok({
        "spoken": True,
        "role": role or None,
        "voice": final_voice,
        "channel": "tv_default",
        "routing_source": "default_tv",
        "url": res.public_url,
        "provider": res.provider,
        "cached": res.cached,
    })


async def try_tts(request):
    """旧版试听接口（兼容）：合成后播放到电视。建议改用 /api/tts/speak。"""
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    text = str(body.get("text", "")).strip()
    member = str(body.get("member", ""))
    voice = body.get("voice") or None
    if not text:
        return err("text required")
    rt = get_runtime()
    res = await rt.tts.synthesize(text, member=member, voice=voice)
    if res is None:
        return ok({"spoken": False, "reason": "tts_unavailable"})
    rt.tv.play_url(res.public_url)
    return ok({"spoken": True, "url": res.public_url, "provider": res.provider, "cached": res.cached})


async def voices(request):
    """诊断端点：对候选音色逐一合成，定位「一个声音不响」的根因。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    try:
        body = await request.json()
    except Exception:
        body = {}
    requested = body.get("voices") if isinstance(body, dict) else None
    if isinstance(requested, str):
        requested = [requested]
    if not isinstance(requested, list):
        requested = []
    candidates: list[str] = [str(x) for x in requested if str(x).strip()]
    s = rt.settings
    for v in (s.tts_edge_voice, s.tts_kokoro_voice):
        if v and v not in candidates:
            candidates.append(v)
    common = [
        "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunxiNeural",
        "zh-CN-YunyangNeural", "zh-CN-XiaomoNeural", "zh-CN-XiaohanNeural",
        "zh-CN-YunjianNeural", "zh-CN-XiaoxuanNeural",
    ]
    for v in common:
        if v not in candidates:
            candidates.append(v)
    results = await rt.tts.test_voices(candidates)
    ok_voices = [r["voice"] for r in results if r.get("ok")]
    bad_voices = [r["voice"] for r in results if not r.get("ok")]
    return ok({
        "tested": results,
        "ok_voices": ok_voices,
        "bad_voices": bad_voices,
        "common_zh_neural": common,
        "role_voices": ROLE_VOICES,
    })


async def engines(request):
    g = guard(request)
    if g:
        return g
    s = get_runtime().settings
    return ok({
        "primary": s.tts_primary,
        "edge_voice": s.tts_edge_voice,
        "kokoro_voice": s.tts_kokoro_voice,
        "speed": s.tts_speed,
        "volume": s.tts_volume,
        "cache": s.tts_cache_enabled,
        "role_voices": ROLE_VOICES,
    })



async def queue_status(request):
    """v2.5: TTS 队列状态查询"""
    from butler.tts.singleton import get_queue
    q = get_queue()
    if q is None:
        return err(503, "TTS queue not initialized")
    return ok(q.status())



async def queue_enqueue(request):
    """v2.5: 往 TTS 队列塞一条测试消息"""
    from butler.tts.singleton import get_queue
    q = get_queue()
    if q is None:
        return err(503, "TTS queue not initialized")
    body = await request.json()
    text = body.get("text", "队列测试")
    priority = body.get("priority", 3)
    device_id = body.get("device_id")
    room = body.get("room", "")
    backend = body.get("backend")
    voice = body.get("voice")
    r = q.enqueue(text, priority=priority, device_id=device_id, room=room,
                  backend=backend, voice=voice, override_quiet=True)
    return ok({"accepted": r.accepted, "reason": r.reason, "size": q.status()["size"]})

def routes():
    return [
        Route("/api/tts/speak", speak, methods=["POST"]),
        Route("/api/tts/try", try_tts, methods=["POST"]),
        Route("/api/tts/voices", voices, methods=["POST"]),
        Route("/api/tts/engines", engines, methods=["GET"]),
        Route("/api/tts/queue/status", queue_status, methods=["GET"]),
        Route("/api/tts/queue/enqueue", queue_enqueue, methods=["POST"]),
    ]
