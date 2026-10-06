"""butler.tools —— 工具集按业务域拆分后的包。

对外接口面与原 butler/core/tools.py 完全一致：
    from butler.tools import TOOL_SCHEMAS, dispatch_tool, _set_reminder
"""
from __future__ import annotations

from butler.tools.registry import TOOL_SCHEMAS, dispatch_tool
from butler.tools.schedule import (
    _create_schedule,
    _fire_reminder,
    _parse_reminder_at,
    _query_schedule,
    _set_reminder,
    _update_schedule,
)
from butler.tools.devices import _resolve_entity
from butler.tools.media import _play_music, _switch_channel
from butler.tools.desk_pilot import (
    DESKPILOT_PM_SEND_URL,
    _assign_task_to_dp,
    _assign_task_to_tp,
    _deskpilot_pm_send,
    _dispatch_deskpilot,
    _dispatch_docker,
    _dispatch_newapi,
    _send_to_dp,
    _send_to_tp,
)
from butler.tools.tv import _dispatch_tvpilot
from butler.tools.skills import _create_skill

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
