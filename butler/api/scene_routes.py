"""场景模板：一键执行多设备联动（v1.4）。"""
from __future__ import annotations

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.scene")

# 设备映射（与 doubao_webhook.py 保持一致）
DEVICE_MAP = {
    "显示器挂灯": "light.yeelink_cn_555003624_lamp22_s_2",
    "书房空调": "climate.lumi_cn_84159632_v2",
    "客厅主灯": "light.philips_cn_248631262_cbulb_s_2_light",
    "客厅电视": "media_player.xiaomi_rmh1_6103_play_control",
    "客厅风扇": "fan.dmaker_cn_245712731_p9_s_2_fan",
    "主卧室空调": "climate.lumi_cn_124561701_mcn02",
    "房间床头灯": "light.yeelink_cn_434411305_bslamp2_s_2_light",
}

# 预置场景模板
SCENES = {
    "movie": {
        "name": "观影模式",
        "icon": "🎬",
        "desc": "关客厅主灯，开电视，关书房空调",
        "steps": [
            {"device": "客厅主灯", "action": "关闭"},
            {"device": "客厅电视", "action": "打开"},
            {"device": "书房空调", "action": "关闭"},
        ],
    },
    "sleep": {
        "name": "睡眠模式",
        "icon": "😴",
        "desc": "关所有灯、空调、电视",
        "steps": [
            {"device": "客厅主灯", "action": "关闭"},
            {"device": "显示器挂灯", "action": "关闭"},
            {"device": "房间床头灯", "action": "关闭"},
            {"device": "客厅电视", "action": "关闭"},
            {"device": "书房空调", "action": "关闭"},
            {"device": "主卧室空调", "action": "关闭"},
        ],
    },
    "morning": {
        "name": "晨起模式",
        "icon": "🌅",
        "desc": "开客厅电视切CCTV1，开客厅灯",
        "steps": [
            {"device": "客厅主灯", "action": "打开"},
            {"device": "客厅电视", "action": "打开"},
        ],
    },
    "away": {
        "name": "离家模式",
        "icon": "🚪",
        "desc": "关所有设备",
        "steps": [
            {"device": "客厅主灯", "action": "关闭"},
            {"device": "显示器挂灯", "action": "关闭"},
            {"device": "房间床头灯", "action": "关闭"},
            {"device": "客厅电视", "action": "关闭"},
            {"device": "书房空调", "action": "关闭"},
            {"device": "主卧室空调", "action": "关闭"},
            {"device": "客厅风扇", "action": "关闭"},
        ],
    },
}


async def _ha_call(client: httpx.AsyncClient, rt, service: str, payload: dict) -> bool:
    try:
        headers = {"Authorization": f"Bearer {rt.settings.ha_token}"}
        r = await client.post(f"{rt.settings.ha_url}/api/services/{service}",
                              json=payload, headers=headers, timeout=10)
        return r.status_code < 400
    except Exception as e:
        logger.warning("HA call failed: %s", e)
        return False


async def _execute_step(client: httpx.AsyncClient, rt, step: dict) -> dict:
    device = step.get("device", "")
    action = step.get("action", "")
    entity_id = DEVICE_MAP.get(device)
    if not entity_id:
        return {"device": device, "ok": False, "error": "unknown_device"}

    ok_flag = False
    try:
        if action == "打开":
            if entity_id.startswith("light."):
                ok_flag = await _ha_call(client, rt, "light/turn_on", {"entity_id": entity_id})
            elif entity_id.startswith("climate."):
                ok_flag = await _ha_call(client, rt, "climate/set_hvac_mode",
                                          {"entity_id": entity_id, "hvac_mode": "cool"})
            elif entity_id.startswith("media_player."):
                ok_flag = await _ha_call(client, rt, "media_player/turn_on", {"entity_id": entity_id})
            elif entity_id.startswith("fan."):
                ok_flag = await _ha_call(client, rt, "fan/turn_on", {"entity_id": entity_id})
        elif action == "关闭":
            if entity_id.startswith("light."):
                ok_flag = await _ha_call(client, rt, "light/turn_off", {"entity_id": entity_id})
            elif entity_id.startswith("climate."):
                ok_flag = await _ha_call(client, rt, "climate/set_hvac_mode",
                                         {"entity_id": entity_id, "hvac_mode": "off"})
            elif entity_id.startswith("media_player."):
                ok_flag = await _ha_call(client, rt, "media_player/turn_off", {"entity_id": entity_id})
            elif entity_id.startswith("fan."):
                ok_flag = await _ha_call(client, rt, "fan/turn_off", {"entity_id": entity_id})
    except Exception as e:
        return {"device": device, "action": action, "ok": False, "error": str(e)}

    return {"device": device, "action": action, "ok": ok_flag}


async def scene_list(request: Request):
    """GET /api/scenes  列出所有预置场景。"""
    g = guard(request)
    if g:
        return g
    return ok({"scenes": [{"id": k, **v} for k, v in SCENES.items()]})


async def scene_run(request: Request):
    """POST /api/scenes/{scene_id}/run  执行场景。"""
    g = guard(request)
    if g:
        return g
    scene_id = request.path_params.get("scene_id", "")
    scene = SCENES.get(scene_id)
    if not scene:
        return err("scene not found", 404)

    rt = get_runtime()
    results = []
    async with httpx.AsyncClient(timeout=15) as client:
        for step in scene["steps"]:
            r = await _execute_step(client, rt, step)
            results.append(r)

    success = all(r["ok"] for r in results)
    logger.info("scene %s executed: %d/%d ok", scene_id,
                sum(1 for r in results if r["ok"]), len(results))
    return ok({"scene": scene_id, "name": scene["name"], "success": success, "results": results})


def routes():
    return [
        Route("/api/scenes", scene_list, methods=["GET"]),
        Route("/api/scenes/{scene_id}/run", scene_run, methods=["POST"]),
    ]
