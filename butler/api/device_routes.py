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
    return ok({"device": dev.to_dict()})


async def delete_device(request):
    g = guard(request)
    if g:
        return g
    did = request.path_params.get("id", "")
    rt = get_runtime()
    if rt.devices.delete(did):
        return ok({"deleted": did})
    return err("device not found", 404)


def routes():
    return [
        Route("/api/devices", list_devices, methods=["GET"]),
        Route("/api/devices", upsert_device, methods=["POST"]),
        Route("/api/devices/{id}", delete_device, methods=["DELETE"]),
    ]
