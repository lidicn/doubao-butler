"""兼容 shim：原 butler/core/tools.py 已拆分为 butler/tools/ 包。

此文件只做 re-export，保持所有老 import 路径不变。
新代码请直接 from butler.tools import ...
"""
from __future__ import annotations

from butler.tools import (
    DESKPILOT_PM_SEND_URL,
    TOOL_SCHEMAS,
    _assign_task_to_dp,
    _assign_task_to_tp,
    _create_schedule,
    _create_skill,
    _deskpilot_pm_send,
    _dispatch_deskpilot,
    _dispatch_docker,
    _dispatch_newapi,
    _dispatch_tvpilot,
    _fire_reminder,
    _parse_reminder_at,
    _play_music,
    _query_schedule,
    _resolve_entity,
    _send_to_dp,
    _send_to_tp,
    _set_reminder,
    _switch_channel,
    _update_schedule,
    dispatch_tool,
)

__all__ = [
    "TOOL_SCHEMAS",
    "dispatch_tool",
    "_set_reminder",
    "_parse_reminder_at",
    "_fire_reminder",
    "_query_schedule",
    "_create_schedule",
    "_update_schedule",
    "_resolve_entity",
    "_play_music",
    "_switch_channel",
    "_create_skill",
    "_dispatch_tvpilot",
    "_dispatch_deskpilot",
    "_dispatch_newapi",
    "_dispatch_docker",
    "_assign_task_to_tp",
    "_assign_task_to_dp",
    "_send_to_tp",
    "_send_to_dp",
    "_deskpilot_pm_send",
    "DESKPILOT_PM_SEND_URL",
]
