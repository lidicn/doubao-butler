"""trigger API：CRUD / 执行历史 / 热加载 / 启用禁用 / 参数模板。

v0.7：自我编排的管理面，用户通过对话生成的 trigger 也走这里保存。
v1.4：增加启用/禁用、热加载、参数模板（供 webui 使用）。
"""
from __future__ import annotations

import asyncio
import json

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime
from butler.triggers.schema import validate_trigger

logger = get_logger("butler.api.triggers")


def _engine():
    rt = get_runtime()
    return getattr(rt, "trigger_engine", None)


# 触发器参数模板：供 webui 动态渲染表单
TRIGGER_PARAM_TEMPLATES = {
    "event_types": [
        {"value": "face_detected", "label": "人脸识别", "icon": "camera"},
        {"value": "voice_wake", "label": "语音唤醒", "icon": "mic"},
        {"value": "button_pressed", "label": "按钮事件", "icon": "button"},
        {"value": "device_state", "label": "设备状态变化", "icon": "device"},
        {"value": "scheduled", "label": "定时触发", "icon": "clock"},
        {"value": "heartbeat", "label": "心跳定时", "icon": "heartbeat"},
        {"value": "skill_completed", "label": "技能完成", "icon": "check"},
    ],
    "conditions": {
        "time_range": {
            "type": "string",
            "label": "时间范围",
            "placeholder": "07:00-08:00（留空=不限）",
            "help": "格式 HH:MM-HH:MM",
        },
        "member": {
            "type": "select",
            "label": "指定成员",
            "options": [
                {"value": "", "label": "不限"},
                {"value": "lidicn", "label": "大佬（lidicn）"},
                {"value": "Kevin", "label": "凯文（Kevin）"},
                {"value": "Emily", "label": "爱美丽（Emily）"},
            ],
        },
        "room": {
            "type": "select",
            "label": "指定房间",
            "options": [
                {"value": "", "label": "不限"},
                {"value": "客厅", "label": "客厅"},
                {"value": "书房", "label": "书房"},
                {"value": "主卧室", "label": "主卧室"},
                {"value": "Kevin房间", "label": "Kevin房间"},
                {"value": "Emily房间", "label": "Emily房间"},
                {"value": "厨房", "label": "厨房"},
            ],
        },
        "button_id": {
            "type": "string",
            "label": "按钮ID",
            "placeholder": "food_calorie（仅按钮事件）",
        },
        "text_contains": {
            "type": "array",
            "label": "文本包含关键词",
            "placeholder": "早上好（仅语音事件，可多个）",
        },
        "require_presence": {
            "type": "string",
            "label": "需确认在场成员",
            "placeholder": "Kevin（触发前确认该成员在家）",
        },
    },
    "actions": {
        "skill": {
            "type": "select",
            "label": "执行技能",
            "options_source": "/api/skills",  # webui 动态获取
        },
        "params": {
            "type": "object",
            "label": "技能参数",
            "help": "支持模板变量：{{event.member}} {{event.room}} {{event.text}} {{event.button_id}}",
        },
    },
    "common": {
        "cooldown_sec": {"type": "number", "label": "冷却时间（秒）", "default": 300, "min": 0},
        "priority": {"type": "number", "label": "优先级", "default": 10, "min": 1, "max": 100},
        "continue_on_error": {"type": "boolean", "label": "出错继续执行下一个", "default": True},
        "exclusive": {"type": "boolean", "label": "独占（阻止同事件其他触发器）", "default": True},
    },
}


async def triggers_list(request: Request):
    g = guard(request)
    if g:
        return g
    eng = _engine()
    if eng is None:
        return err("trigger engine not ready", 503)
    status = request.query_params.get("status", "")
    event = request.query_params.get("event", "")
    items = eng.store.list()
    if status == "enabled":
        items = [t for t in items if t.get("enabled", True)]
    elif status == "disabled":
        items = [t for t in items if not t.get("enabled", True)]
    if event:
        items = [t for t in items if t.get("event") == event]
    return ok({"items": items, "count": len(items)})


async def triggers_save(request: Request):
    """创建/更新 trigger（按 id upsert），保存后立即生效（内存索引已更新）。"""
    g = guard(request)
    if g:
        return g
    eng = _engine()
    if eng is None:
        return err("trigger engine not ready", 503)
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    try:
        normalized, verr = validate_trigger(body)
        if verr:
            return err(verr)
    except Exception as e:
        return err(f"invalid trigger: {e}")
    eng.store.save(normalized)
    logger.info("trigger saved: %s (event=%s)", normalized["id"], normalized["event"])
    return ok({"trigger": normalized})


async def triggers_delete(request: Request):
    g = guard(request)
    if g:
        return g
    eng = _engine()
    if eng is None:
        return err("trigger engine not ready", 503)
    tid = request.path_params.get("trigger_id", "")
    if not tid:
        return err("missing trigger_id")
    deleted = eng.store.delete(tid)
    logger.info("trigger deleted: %s (existed=%s)", tid, deleted)
    return ok({"deleted": deleted})


async def trigger_set_enabled(request: Request):
    """启用/禁用触发器。"""
    g = guard(request)
    if g:
        return g
    eng = _engine()
    if eng is None:
        return err("trigger engine not ready", 503)
    tid = request.path_params.get("trigger_id", "")
    if not tid:
        return err("missing trigger_id")
    try:
        body = await request.json()
    except Exception:
        body = {}
    enabled = bool(body.get("enabled", True))
    trigger = eng.store.get(tid)
    if trigger is None:
        return err("trigger not found", 404)
    trigger["enabled"] = enabled
    trigger["updated_at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
    eng.store.save(trigger)
    logger.info("trigger %s %s", tid, "enabled" if enabled else "disabled")
    return ok({"trigger": trigger})


async def triggers_reload(request: Request):
    """热加载触发器（从磁盘重新读取）。"""
    g = guard(request)
    if g:
        return g
    eng = _engine()
    if eng is None:
        return err("trigger engine not ready", 503)
    count = eng.store.reload()
    logger.info("triggers reloaded: %d", count)
    return ok({"reloaded": count})


async def trigger_templates(request: Request):
    """返回触发器参数模板，供 webui 动态渲染表单。"""
    g = guard(request)
    if g:
        return g
    return ok(TRIGGER_PARAM_TEMPLATES)


async def trigger_runs(request: Request):
    g = guard(request)
    if g:
        return g
    tid = request.path_params.get("trigger_id", "")
    limit = int(request.query_params.get("limit", "50"))
    rows = await asyncio.to_thread(_repo_recent, tid, limit)
    return ok({"items": rows})


def _repo_recent(trigger_id: str, limit: int):
    from butler.store import repo
    return repo.recent_trigger_runs(trigger_id, limit)


async def trigger_test(request: Request):
    """手动测试触发：模拟事件触发该触发器。"""
    g = guard(request)
    if g:
        return g
    eng = _engine()
    if not eng:
        return err("trigger engine not ready", 503)
    tid = request.path_params["trigger_id"]
    # 格6（DCD 裁定 20261001 B）＝面板模拟触发默认试运行 ⇒ 入口缺省即 dry_run，
    # 请求体没带这个字段、或 JSON 解析炸了走 except 分支，都落在"不执行"这一侧（fail-closed）。
    dry_run = True
    try:
        body = await request.json()
        payload = body.get("payload", {})
        dry_run = bool(body.get("dry_run", True))
    except Exception:
        payload = {}
    trig = None
    for t in eng.store.list():
        if t.get("id") == tid:
            trig = t
            break
    if not trig:
        return err(f"trigger {tid} not found", 404)
    event_type = trig.get("event", "voice_wake")
    payload.setdefault("member", "测试用户")
    payload.setdefault("room", "测试房间")
    payload.setdefault("text", "手动测试触发")
    payload["test"] = True
    logger.info("trigger test: id=%s event=%s dry_run=%s", tid, event_type, dry_run)
    try:
        results = await eng.handle_event(event_type, payload, dry_run=dry_run)
        return ok({"trigger_id": tid, "event": event_type, "dry_run": dry_run,
                   "results": results})
    except Exception as e:
        logger.exception("trigger test failed: %s", tid)
        return err(f"test failed: {e}")


async def trigger_logs(request: Request):
    """v2.6#3 技能触发日志页数据接口：trigger/skill/action/trace_id 一次拉平。"""
    g = guard(request)
    if g:
        return g
    q = request.query_params
    limit = int(q.get("limit", "50"))
    rows = await asyncio.to_thread(
        _repo_logs, q.get("trigger_id", ""), q.get("trace_id", ""),
        q.get("status", ""), limit)
    names: dict = {}
    eng = _engine()
    if eng:
        try:
            names = {t.get("id", ""): t.get("name", "") for t in eng.store.list()}
        except Exception:
            names = {}
    items = []
    for r in rows:
        try:
            skills = json.loads(r.get("actions_json") or "[]") or []
        except Exception:
            skills = []
        tid = r.get("trigger_id", "")
        items.append({
            "ts": r.get("ts"),
            "trigger_id": tid,
            "trigger_name": names.get(tid, ""),
            "event": r.get("event", ""),
            "status": r.get("status", ""),
            "skills": skills,
            "actions": skills,
            "trace_id": r.get("trace_id", ""),
            "error": r.get("error") or "",
        })
    return ok({"items": items, "total": len(items), "limit": limit})


def _repo_logs(trigger_id: str, trace_id: str, status: str, limit: int):
    from butler.store import repo
    return repo.trigger_logs(trigger_id, trace_id, status, limit)


def routes():
    return [
        Route("/api/triggers", triggers_list, methods=["GET"]),
        Route("/api/triggers", triggers_save, methods=["POST"]),
        Route("/api/triggers/reload", triggers_reload, methods=["POST"]),
        Route("/api/triggers/templates", trigger_templates, methods=["GET"]),
        Route("/api/triggers/logs", trigger_logs, methods=["GET"]),
        Route("/api/triggers/{trigger_id}", triggers_delete, methods=["DELETE"]),
        Route("/api/triggers/{trigger_id}/enabled", trigger_set_enabled, methods=["PUT"]),
        Route("/api/triggers/{trigger_id}/runs", trigger_runs, methods=["GET"]),
        Route("/api/triggers/{trigger_id}/test", trigger_test, methods=["POST"]),
    ]
