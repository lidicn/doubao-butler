"""技能 JSON v2 校验与规范化（手写，无外部依赖）。

v2 新增字段：
  - status: draft | enabled | disabled | quarantined（默认 enabled，兼容旧 enabled 字段）
  - source: builtin | user | agent（默认 user）
  - priority: 1-100（默认50，数字越大优先级越高）
  - approval: pending_review | approved | rejected | auto_approved（v1.5 沙箱审批，默认 approved）

技能定义示例：
{
  "id": "hello", "name": "多模态问候", "version": 1,
  "status": "enabled", "source": "user", "priority": 50, "approval": "approved",
  "trigger": {"entry": "webhook"},
  "senses": [{"type": "camera", "room": "客厅", "max_age_s": 0}],
  "brain": {"type": "vlm_compose", "engine": "camera_vlm", "mode": "ma_analyze",
            "prompt": "", "system": "...", "context": ["persona", "member_profile"],
            "dedup": true, "max_chars": 60, "mess_slot": true},
  "output": [{"type": "tv_notify", "tts": true}],
  "limits": {"per_day": 30},
  "on_busy": "drop"
}
"""
from __future__ import annotations

import re
import time

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
_ENTRIES = {"webhook", "mqtt", "face_seen", "schedule", "ha_event", "heartbeat"}
_OUTPUTS = {"tv_notify", "xiaomi_speak", "bark"}
_ON_BUSY = {"drop", "queue"}
_CONTEXTS = {"persona", "member_profile", "memories", "time"}
_STATUSES = {"draft", "enabled", "disabled", "quarantined"}
_SOURCES = {"builtin", "user", "agent"}


def _s(v, default="") -> str:
    return v.strip() if isinstance(v, str) else default


def _b(v, default=False) -> bool:
    return v if isinstance(v, bool) else default


def _i(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def validate_skill(raw: dict) -> tuple[dict | None, str]:
    """校验并规范化技能定义。返回 (normalized, error)；error 为空表示成功。"""
    if not isinstance(raw, dict):
        return None, "技能定义必须是 JSON object"
    sid = _s(raw.get("id"))
    if not _ID_RE.match(sid):
        return None, "id 必须为 2~40 位小写字母/数字/下划线"

    name = _s(raw.get("name")) or sid

    trigger = raw.get("trigger") or {}
    entry = _s(trigger.get("entry"), "webhook")
    if entry not in _ENTRIES:
        return None, f"trigger.entry 仅支持 {sorted(_ENTRIES)}"
    
    # 扩展 trigger：schedule 类型支持 cron 表达式
    trigger_out = {"entry": entry}
    if entry == "schedule":
        if "cron" in trigger:
            trigger_out["cron"] = _s(trigger.get("cron"))
        elif "interval_s" in trigger:
            trigger_out["interval_s"] = max(1, _i(trigger.get("interval_s"), 60))

    senses = []
    for s in raw.get("senses") or []:
        if not isinstance(s, dict):
            continue
        stype = _s(s.get("type"), "camera")
        senses.append({
            "type": stype,
            "room": _s(s.get("room"), "客厅"),
            "max_age_s": max(0, _i(s.get("max_age_s"), 0)),
        })
    if not senses:
        senses = [{"type": "camera", "room": "客厅", "max_age_s": 0}]

    brain_raw = raw.get("brain")
    if not isinstance(brain_raw, dict):
        brain_raw = {}
    brain_type = _s(brain_raw.get("type"), "vlm_compose")
    context = [c for c in (brain_raw.get("context") or []) if c in _CONTEXTS]
    # ⛔ 按固定白名单重建 brain：引擎自己读的键（ha_action 的 domain/service、
    # ha_inspection 的 checks/ignore_*、trigger_creator 的 max_retry…）会被静默剥掉。
    # 先原样保留，再覆盖需要归一化那几枚。
    brain = dict(brain_raw)
    brain.update({
        "type": brain_type,
        "engine": _s(brain_raw.get("engine"), "camera_vlm"),
        "mode": _s(brain_raw.get("mode"), "ma_analyze"),
        "prompt": _s(brain_raw.get("prompt")),
        "system": _s(brain_raw.get("system")),
        "context": context,
        "dedup": _b(brain_raw.get("dedup")),
        "max_chars": max(20, min(300, _i(brain_raw.get("max_chars"), 80))),
        "mess_slot": _b(brain_raw.get("mess_slot")),
    })
    
    # cron_task 引擎扩展字段（v1.9 Koin Action / v2.0 技能工厂）：定时条件任务
    # 同时支持 brain.type == "cron_task" 和 brain.engine == "cron_task"
    if brain_type == "cron_task" or brain.get("engine") == "cron_task":
        # 预注册 API 引用
        if "api_id" in brain_raw:
            brain["api_id"] = _s(brain_raw.get("api_id"))
        # 条件判断配置
        if "condition" in brain_raw:
            cond = brain_raw["condition"]
            if isinstance(cond, dict):
                # 支持 hour_local 时区感知条件（透传）和 jsonpath 条件
                if cond.get("type") == "hour_local":
                    brain["condition"] = {
                        "type": "hour_local",
                        "hours": cond.get("hours", []),
                        "threshold": cond.get("threshold", 50),
                        "any": cond.get("any", True),
                    }
                else:
                    brain["condition"] = {
                        "jsonpath": _s(cond.get("jsonpath")),
                        "comparator": _s(cond.get("comparator"), "exists"),
                        "value": cond.get("value"),
                    }
        # 消息模板
        if "message_template" in brain_raw:
            brain["message_template"] = _s(brain_raw.get("message_template"))
        # 失败重试次数
        if "max_retries" in brain_raw:
            brain["max_retries"] = max(0, min(5, _i(brain_raw.get("max_retries"), 1)))

    outputs = []
    rejected = []
    for o in raw.get("output") or []:
        if not isinstance(o, dict):
            continue
        otype = _s(o.get("type"), "tv_notify")
        if otype not in _OUTPUTS:
            rejected.append(otype)
            continue
        out_config = {"type": otype, "tts": _b(o.get("tts"), True)}
        # 保留 room / role / device 等扩展字段
        for key in ("room", "role", "device"):
            if key in o:
                out_config[key] = o.get(key)
        outputs.append(out_config)
    if rejected:
        # DCD 裁定④-B：白名单外的 output 当场报错。⛔ 静默剥——剥光就回落成
        # tv_notify(tts=True)：面板以为建好了「开灯」，实际得到一只只会说不会做、
        # 且因为名单里没有 ha_service 而骗过 conflict／sandbox 两道门的哑巴。
        return None, f"output 类型不支持：{sorted(set(rejected))}（可用：{sorted(_OUTPUTS)}）"
    if not outputs:
        outputs = [{"type": "tv_notify", "tts": True}]

    limits_raw = raw.get("limits") or {}
    limits = {"per_day": max(0, _i(limits_raw.get("per_day"), 20))}

    on_busy = _s(raw.get("on_busy"), "drop")
    if on_busy not in _ON_BUSY:
        on_busy = "drop"

    role = _s(raw.get("role"), "butler")

    # v2 新字段：status / source / priority
    # 兼容旧版 enabled 字段：enabled=false → status=disabled
    status = _s(raw.get("status"), "").lower()
    if status not in _STATUSES:
        if _b(raw.get("enabled"), True):
            status = "enabled"
        else:
            status = "disabled"

    source = _s(raw.get("source"), "user").lower()
    if source not in _SOURCES:
        source = "user"

    priority = max(1, min(100, _i(raw.get("priority"), 50)))

    # v1.5 审批状态
    _APPROVALS = {"pending_review", "approved", "rejected", "auto_approved"}
    approval = _s(raw.get("approval"), "approved").lower()
    if approval not in _APPROVALS:
        approval = "pending_review"

    out = {
        "id": sid,
        "name": name,
        "version": max(1, _i(raw.get("version"), 1)),
        "status": status,
        "source": source,
        "priority": priority,
        "approval": approval,
        "enabled": status == "enabled",  # 兼容旧字段
        "trigger": trigger_out,
        "senses": senses,
        "brain": brain,
        "output": outputs,
        "limits": limits,
        "on_busy": on_busy,
        "role": role,
        "push_to_app": _b(raw.get("push_to_app"), False),
        "updated_at": _s(raw.get("updated_at")) or time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return out, ""


def validate_or_error(raw: dict) -> dict:
    """校验失败直接抛 ValueError（供 API 层转 400）。"""
    normalized, err = validate_skill(raw)
    if err:
        raise ValueError(err)
    return normalized
