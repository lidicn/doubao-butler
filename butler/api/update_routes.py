"""WebUI 自更新（批197）：容器侧只做三件事——读预览、写请求标记、报状态。

现读拓扑（`workorders/readings/1011/b197_topology_nas.txt`，`stamp_utc=2026-10-11T03:33:08Z`）：
容器挂载面只有 `butler/`、`data/`、`tts/`，`/app` 里⛔ `.git`，容器内 `which git` 为空
⇒ 容器⛔ 跑 git、⛔ 碰 docker。取码、快进判定、备份、语法检查、重启全在宿主 cron
（`scripts/host_check_update.sh` 每 5 分钟写预览，`scripts/host_update.sh` 每分钟看标记）。

2026-11-04 变更：
- 去掉「输入当前版本号二次确认」——`update_apply` 不再要求 `confirm_version`（出资人令）。
- 把读预览/读状态/写标记抽成 `build_check/build_status/write_request_marker`，供 WebUI 路由
  与 **MCP 工具（system_update*，开发者 agent 通道）** 共用同一份实现（单一真源）。

三枚文件一律过 `_path()`→`data_paths.data_file()`（惰性单点解析，批164 D5 同族），⛔ 模块级容器路径死值。
"""
from __future__ import annotations

import json
import os
import time

from starlette.routing import Route

from butler import __version__, update_gate
from butler.api.deps import err, guard, ok
from butler.core import data_paths
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.update")

PREVIEW_NAME = ".update_available.json"
MARKER_NAME = ".update_request.json"
RESULT_NAME = ".update_result.json"


def _path(name: str):
    """路径每次现取（⛔ 模块级容器路径字面量＝两个物理文件那条老路）。"""
    return data_paths.data_file(name)


def _int(v, default: int = 0) -> int:
    try:
        return int(v or default)
    except (TypeError, ValueError):
        return default


# ── 可复用实现（WebUI 路由 与 MCP 工具共用，单一真源） ──────────────────

def build_check(s) -> dict:
    """预览＋「现在点会不会被拒」的预判（预判与宿主执行共用 `update_gate`，⛔ 两套口径）。"""
    st = update_gate.read_state(_path(PREVIEW_NAME))
    prev = st["data"] or {}
    if not st["present"]:
        action, reason = "refuse", "宿主检查还没跑过（预览件不存在）：先装 cron 或等一轮检查"
    elif st["data"] is None:
        action, reason = "refuse", "预览件读不出来（%s）" % (st["error"] or "unknown")
    else:
        action, reason = update_gate.decide_action(
            enabled=bool(s.self_update_enabled),
            dirty=_int(prev.get("dirty")),
            fast_forward=bool(prev.get("fast_forward")),
            ref=str(prev.get("ref") or s.update_branch),
        )
    return {
        "enabled": bool(s.self_update_enabled),
        "preview_present": st["present"],
        "preview_error": st["error"],
        "has_update": bool(prev.get("has_update")) if st["present"] else False,
        "local_commit": prev.get("local_commit", ""),
        "remote_commit": prev.get("remote_commit", ""),
        "behind": _int(prev.get("behind")),
        "dirty": _int(prev.get("dirty")),
        "fast_forward": bool(prev.get("fast_forward")),
        "changelog": prev.get("changelog", []),
        "checked_at": prev.get("checked_at", ""),
        "repo_url": s.update_repo_url,
        "branch": s.update_branch,
        "version": __version__,
        "decision": {"action": action, "reason": reason},
    }


def build_status() -> dict:
    """把「落盘」与「生效」分开报——只落码没重启时⛔ 说已生效。"""
    marker = update_gate.read_state(_path(MARKER_NAME))
    res = update_gate.read_state(_path(RESULT_NAME))
    data = res["data"] or {}
    state, pending, error = "idle", False, data.get("error") or ""
    if res["present"] and res["data"] is None:
        state, error = "unreadable", res["error"] or ""
    elif data:
        if not data.get("ok"):
            state = "failed"
        elif data.get("action") == "apply_only":
            state, pending = "applied_pending_restart", True
        else:
            state = "applied"
    elif marker["present"]:
        state = "queued"
    return {
        "state": state,
        "pending_restart": pending,
        "error": error,
        "request_present": marker["present"],
        "result_present": res["present"],
        "from": data.get("from", ""),
        "to": data.get("to", ""),
        "to_tag": data.get("to_tag", ""),
        "changelog": data.get("changelog", []),
        "finished_at": data.get("finished_at", ""),
    }


def write_request_marker(ref: str, requested_by: str = "webui") -> dict:
    """原子写升级请求标记（宿主 cron 每分钟读它；⛔ 让它读到半截 JSON）。"""
    marker = _path(MARKER_NAME)
    payload = {
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ref": ref,
        "from_version": __version__,
        "requested_by": requested_by,
    }
    tmp = marker.with_name(marker.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, marker)
    logger.info("self_update_requested: ref=%s from_version=%s by=%s", ref, __version__, requested_by)
    return payload


def validate_ref(s, ref: str) -> str | None:
    """校验目标 ref（发布分支或版本 tag）。返回错误串，None=通过。"""
    if ref != s.update_branch and not update_gate.is_release_ref(ref):
        return "目标 ref 只允许发布分支（%s）或版本 tag（v<主>.<次>），收到 %r" % (s.update_branch, ref)
    return None


# ── WebUI 路由 ────────────────────────────────────────────────────────

async def update_check(request):
    g = guard(request)
    if g:
        return g
    return ok(build_check(get_runtime().settings))


async def update_apply(request):
    """写请求标记；执行归宿主 cron，本函数⛔ 动码、⛔ 重启。已去掉版本号二次确认（2026-11-04）。"""
    g = guard(request)
    if g:
        return g
    s = get_runtime().settings
    try:
        body = await request.json()
    except Exception:
        return err("需要 JSON 请求体")
    if not s.self_update_enabled:
        return err("自更新未启用（SELF_UPDATE_ENABLED=false）", 403)
    ref = str(body.get("ref") or s.update_branch).strip()
    why = validate_ref(s, ref)
    if why:
        return err(why)
    write_request_marker(ref, requested_by="webui")
    resp = ok({"accepted": True, "ref": ref, "executed_by": "host_cron",
               "next": "宿主 cron 一分钟一轮；进度看 GET /api/system/update/status"})
    resp.status_code = 202
    return resp


async def update_status(request):
    g = guard(request)
    if g:
        return g
    return ok(build_status())


def routes():
    return [
        Route("/api/system/update/check", update_check, methods=["GET"]),
        Route("/api/system/update", update_apply, methods=["POST"]),
        Route("/api/system/update/status", update_status, methods=["GET"]),
    ]
