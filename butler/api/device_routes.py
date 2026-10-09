"""设备登记表 API：设备列表 / 新增或修改 / 删除（供角色设备路由与「任意小爱」使用）。"""
from __future__ import annotations

from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.runtime import get_runtime


async def list_devices(request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    return ok({"devices": [d.to_dict() for d in rt.devices.all()]})


async def upsert_device(request):
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
        dev = rt.devices.upsert(body)
    except ValueError as e:
        return err(str(e))
    # 批71 D3：坏档时 upsert 的落盘被拒绝（内存编辑照常生效、盘上现场保住），⛔ 对 WebUI 报成功
    if getattr(rt.devices, "unreadable", False):
        return err("devices.json unreadable, save refused to avoid clobbering it", 500)
    return ok({"device": dev.to_dict()})


async def delete_device(request):
    g = guard(request)
    if g:
        return g
    did = request.path_params.get("id", "")
    rt = get_runtime()
    if not rt.devices.delete(did):
        return err("device not found", 404)
    if getattr(rt.devices, "unreadable", False):
        # 同上：内存里删掉了，但整份覆盖被拒——如实回报，⛔ 谎称删除已持久化
        return err("devices.json unreadable, delete not persisted", 500)
    return ok({"deleted": did})


def routes():
    return [
        Route("/api/devices", list_devices, methods=["GET"]),
        Route("/api/devices", upsert_device, methods=["POST"]),
        Route("/api/devices/{id}", delete_device, methods=["DELETE"]),
    ]
