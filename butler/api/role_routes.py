"""角色（Persona）API：列表 / 新增或修改 / 删除 / 试跑。

试跑：用角色音色合成一句话，推到其 output_devices 里的第一个设备，验证「音色 + 设备」链路。
"""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.role_routes")


async def list_roles(request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    return ok({"roles": [r.to_dict() for r in rt.roles.all()]})


async def upsert_role(request):
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    if not isinstance(body, dict):
        return err("object required")
    rt = get_runtime()
    try:
        role = rt.roles.upsert(body)
    except ValueError as e:
        return err(str(e))
    return ok({"role": role.to_dict()})


async def delete_role(request):
    g = guard(request)
    if g:
        return g
    rid = request.path_params.get("id", "")
    rt = get_runtime()
    try:
        if rt.roles.delete(rid):
            return ok({"deleted": rid})
    except ValueError as e:
        return err(str(e))
    return err("role not found", 404)


async def try_role(request: Request):
    g = guard(request)
    if g:
        return g
    rid = request.path_params.get("id", "")
    rt = get_runtime()
    role = rt.roles.get(rid)
    if role is None:
        return err("role not found", 404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str((body or {}).get("text", "你好，我是" + role.name + "，今天也要照顾好自己哦。")).strip()
    if not text:
        return err("text required")
    res = await rt.tts.synthesize(text, voice=role.voice, backend=role.tts_backend)
    out = []
    for dev in rt.devices.resolve(role.output_devices):
        try:
            if dev.type == "tv":
                if res:
                    rt.tv.play_url(res.public_url)
                    out.append({"device": dev.id, "type": "tv", "ok": True, "url": res.public_url})
                else:
                    out.append({"device": dev.id, "type": "tv", "ok": False, "reason": "tts_none"})
            elif dev.type == "xiaomi":
                if dev.play_mode == "tts_speak" and dev.ha_player_entity:
                    # 用 butler 自己的 edge-tts URL 在小爱出声（保留角色音色）
                    tts_res = res or await rt.tts.synthesize(text, voice=role.voice, backend=role.tts_backend)
                    logger.info("role try xiaomi device=%s player=%s token_set=%s uid_set=%s", dev.id, dev.ha_player_entity, bool(rt.settings.xiaomi_service_token), bool(rt.settings.xiaomi_user_id))
                    if tts_res:
                        # 优先：直连小爱云端 player_play_url（一次性投射，不循环）
                        if rt.settings.xiaomi_service_token and rt.settings.xiaomi_user_id:
                            aid = await rt.ha.get_xiaoai_id(dev.ha_player_entity)
                            logger.info("role try xiaomi xiaoai_id=%s for %s", aid, dev.ha_player_entity)
                            if aid:
                                xmethod = getattr(dev, "xiaomi_play", None) or "url"
                                rj = await rt.ha.play_xiaomi_url_once(aid, tts_res.public_url, method=xmethod)
                                logger.info("role try xiaomi direct result device=%s code=%s detail=%s", dev.id, rj.get("code"), rj.get("message", rj))
                                if xmethod == "music":
                                    rt.ha.schedule_xiaomi_stop(aid, rt.dialog._est_speak_secs(text) if rt.dialog else 8.0)
                                out.append({"device": dev.id, "type": "xiaomi", "mode": "tts_speak_direct", "ok": str(rj.get("code")) == "0", "detail": f"xiaomi_direct:{rj.get('message', rj)}", "url": tts_res.public_url, "play": xmethod})
                            else:
                                r = await rt.ha.tts_play_url(tts_res.public_url, dev.ha_player_entity)
                                out.append({"device": dev.id, "type": "xiaomi", "mode": "tts_speak", "ok": r.startswith("ok"), "detail": r, "url": tts_res.public_url})
                                await rt.ha.schedule_media_stop(dev.ha_player_entity, rt.dialog._est_speak_secs(text) if rt.dialog else 8.0)
                        else:
                            r = await rt.ha.tts_play_url(tts_res.public_url, dev.ha_player_entity)
                            out.append({"device": dev.id, "type": "xiaomi", "mode": "tts_speak", "ok": r.startswith("ok"), "detail": r, "url": tts_res.public_url})
                            await rt.ha.schedule_media_stop(dev.ha_player_entity, rt.dialog._est_speak_secs(text) if rt.dialog else 8.0)
                    else:
                        r = await rt.ha.notify_message(text, dev.ha_entity or None)
                        logger.info("role try xiaomi tts failed, notify fallback device=%s detail=%s", dev.id, r)
                        out.append({"device": dev.id, "type": "xiaomi", "mode": "notify_fallback", "ok": r == "ok", "detail": r})
                else:
                    r = await rt.ha.notify_message(text, dev.ha_entity or None)
                    out.append({"device": dev.id, "type": "xiaomi", "mode": "notify_text", "ok": r == "ok", "detail": r})
        except Exception as e:
            out.append({"device": dev.id, "type": dev.type, "ok": False, "error": str(e)[:200]})
    # 小爱出声会产生回声，预先设置抑制窗口，避免 WebUI 试跑/试听触发自问自答循环
    if any(d.type == "xiaomi" for d in rt.devices.resolve(role.output_devices)):
        rt.dialog.suppress_echo(text)
    return ok({"role": rid, "spoken": res is not None, "provider": res.provider if res else None, "dispatched": out})


async def push_role(request):
    """v0.5: 主动推送一条消息到指定角色的豆包app对话（测试通道）。
    body: {"scene": "场景描述", "direct_text": "可选精确文本"}
    """
    g = guard(request)
    if g:
        return g
    rid = request.path_params.get("id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    scene = body.get("scene") or body.get("text") or "测试推送"
    direct = body.get("direct_text")
    rt = get_runtime()
    if rt.notifier is None:
        return err("notifier not ready")
    reply = await rt.notifier.push(rid, scene, direct_text=direct)
    conv_id = rt.role_state.get(rid) if rt.role_state else None
    return ok({"role": rid, "reply": reply, "conversation_id": conv_id})


async def push_image_role(request):
    """v2.1: 推送图片到指定角色对话。
    body: {"image_url": "http://...", "prompt": "可选分析提示词"}
    """
    g = guard(request)
    if g:
        return g
    rid = request.path_params.get("id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    image_url = body.get("image_url", "")
    prompt = body.get("prompt", "请描述这个画面")
    if not image_url:
        return err("image_url 不能为空")
    rt = get_runtime()
    if rt.notifier is None:
        return err("notifier not ready")
    text = await rt.notifier.push_image(rid, image_url, prompt)
    conv_id = rt.role_state.get(rid) if rt.role_state else None
    return ok({"role": rid, "analysis": text, "conversation_id": conv_id})


def routes():
    return [
        Route("/api/roles", list_roles, methods=["GET"]),
        Route("/api/roles", upsert_role, methods=["POST"]),
        Route("/api/roles/{id}", delete_role, methods=["DELETE"]),
        Route("/api/roles/{id}/try", try_role, methods=["POST"]),
        Route("/api/roles/{id}/push", push_role, methods=["POST"]),
        Route("/api/roles/{id}/push_image", push_image_role, methods=["POST"]),
    ]
