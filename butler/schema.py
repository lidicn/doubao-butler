"""trigger JSON 校验与规范化（手写，无外部依赖，与 skills/schema.py 同风格）。

trigger 定义示例：
{
  "id": "morning_greet",
  "name": "人脸主动问候",
  "version": 1,
  "enabled": true,
  "event": "face_detected",
  "conditions": {"time_range": "06:00-23:00", "member": "", "room": ""},
  "role": "butler",
  "actions": [{"skill": "greet", "params": {"member": "{{event.member}}"}}],
  "cooldown_sec": 300,
  "priority": 10
}

事件类型（v0.1 起逐步扩展）：
  face_detected  人脸事件（TV/摄像头）
  voice_wake     语音唤醒（小爱/TV遥控）
  button_pressed 按钮事件
  device_state   设备状态变化
  scheduled      定时触发
  skill_completed 技能完成事件
"""
from __future__ import annotations

import re
import time

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
_EVENTS = {"face_detected", "voice_wake", "button_pressed", "device_state", "scheduled", "skill_completed"}


def _s(v, default="") -> str:
    return v.strip() if isinstance(v, str) else default


def _b(v, default=False) -> bool:
    return v if isinstance(v, bool) else default


def _i(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _validate_time_range(s: str) -> bool:
    """校验 HH:MM-HH:MM 格式。"""
    m = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", s.strip())
    if not m:
        return False
    h1, m1, h2, m2 = map(int, m.groups())
    return 0 <= h1 <= 23 and 0 <= m1 <= 59 and 0 <= h2 <= 23 and 0 <= m2 <= 59


def validate_trigger(raw: dict) -> tuple[dict | None, str]:
    """校验并规范化 trigger 定义。返回 (normalized, error)；error 为空表示成功。"""
    if not isinstance(raw, dict):
        return None, "trigger 定义必须是 JSON object"
    tid = _s(raw.get("id"))
    if not _ID_RE.match(tid):
        return None, "id 必须为 2~40 位小写字母/数字/下划线/连字符"

    name = _s(raw.get("name")) or tid

    event = _s(raw.get("event"))
    if event not in _EVENTS:
        return None, f"event 仅支持 {sorted(_EVENTS)}"

    cond_raw = raw.get("conditions") or {}
    time_range = _s(cond_raw.get("time_range"))
    if time_range and not _validate_time_range(time_range):
        return None, "conditions.time_range 格式应为 HH:MM-HH:MM"
    text_contains_raw = cond_raw.get("text_contains")
    if isinstance(text_contains_raw, str):
        text_contains = [text_contains_raw]
    elif isinstance(text_contains_raw, list):
        text_contains = [str(x).strip() for x in text_contains_raw if str(x).strip()]
    else:
        text_contains = []
    # require_presence：触发前需确认目标成员在场（v0.4）
    rp_raw = cond_raw.get("require_presence")
    require_presence = ""
    if isinstance(rp_raw, dict):
        require_presence = _s(rp_raw.get("member"))
    elif isinstance(rp_raw, str):
        require_presence = rp_raw.strip()
    elif rp_raw:
        return None, "conditions.require_presence 应为 {'member': 'Kevin'} 或成员名字符串"
    conditions = {
        "time_range": time_range,
        "member": _s(cond_raw.get("member")),
        "room": _s(cond_raw.get("room")),
        "button_id": _s(cond_raw.get("button_id")),
        "text_contains": text_contains,
        "require_presence": require_presence,
    }

    role = _s(raw.get("role"), "butler")

    actions = []
    for a in raw.get("actions") or []:
        if not isinstance(a, dict):
            continue
        skill = _s(a.get("skill"))
        if not skill:
            continue
        params = a.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        actions.append({"skill": skill, "params": params})
    if not actions:
        return None, "actions 至少需要一个 {skill, params}"

    cooldown_sec = max(0, _i(raw.get("cooldown_sec"), 300))
    priority = _i(raw.get("priority"), 10)

    out = {
        "id": tid,
        "name": name,
        "version": max(1, _i(raw.get("version"), 1)),
        "enabled": _b(raw.get("enabled"), True),
        "event": event,
        "conditions": conditions,
        "role": role,
        "actions": actions,
        "continue_on_error": _b(raw.get("continue_on_error"), True),
        "exclusive": _b(raw.get("exclusive"), True),
        "cooldown_sec": cooldown_sec,
        "priority": priority,
        "updated_at": _s(raw.get("updated_at")) or time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return out, ""


def validate_or_error(raw: dict) -> dict:
    """校验失败直接抛 ValueError（供 API 层转 400）。"""
    normalized, err = validate_trigger(raw)
    if err:
        raise ValueError(err)
    return normalized
