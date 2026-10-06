"""MCP Schema 自动生成器（v1.8 P0-7）。

从管家内部工具定义自动生成 MCP Tool Schema，不手写第二套。
确保 MCP 暴露的工具与管家内部工具定义始终一致。
"""
from __future__ import annotations

from typing import Any, Callable

from butler.logging_setup import get_logger

logger = get_logger("butler.mcp.schema_gen")


def generate_tool_schema(
    name: str,
    description: str,
    parameters: dict[str, Any],
    required: list[str] | None = None,
) -> dict:
    """生成 MCP Tool Schema（JSON-RPC tools/list 返回格式）。

    Args:
        name: 工具名称（snake_case）
        description: 工具描述（何时调用/何时不要调用/副作用）
        parameters: 参数定义字典 {param_name: {"type": ..., "description": ..., "enum": ...}}
        required: 必填参数列表

    Returns:
        MCP Tool Schema dict
    """
    properties = {}
    for param_name, param_def in parameters.items():
        prop = {
            "type": param_def.get("type", "string"),
            "description": param_def.get("description", ""),
        }
        if "enum" in param_def:
            prop["enum"] = param_def["enum"]
        if "default" in param_def:
            prop["default"] = param_def["default"]
        properties[param_name] = prop

    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


# ============================================================
# 薄 MCP 暴露的 6 个 Tools 定义（v1.8）
# 这些定义与管家内部工具语义一致，但 Schema 在这里显式定义
# 因为 MCP 需要标准 JSON Schema 格式
# ============================================================

MCP_TOOLS = [
    generate_tool_schema(
        name="create_trigger",
        description=(
            "创建管家实时决策触发规则。"
            "何时调用：用户要求创建长期触发规则（涉及 MA 行为信号/管家技能/会话上下文）时。"
            "何时不要调用：①会话内临时提醒（<1h）用 set_reminder；"
            "②持久化家庭自动化规则（如每晚十点半熄灯，纯 HA 设备域）用 autoflow_propose 技能委托 autoflow。"
            "高风险规则（含设备控制/模式切换）需 confirm=true。"
        ),
        parameters={
            "trigger_json": {
                "type": "object",
                "description": "触发规则定义（event/conditions/actions/enabled），参考管家触发规则格式",
            },
            "confirm": {
                "type": "boolean",
                "description": "高风险规则需 confirm=true 才会生效；低风险规则可省略",
                "default": False,
            },
        },
        required=["trigger_json"],
    ),
    generate_tool_schema(
        name="list_triggers",
        description=(
            "列出管家实时决策触发规则，支持按 event/enabled/risk_level 过滤。"
            "只读，无副作用。"
        ),
        parameters={
            "event": {"type": "string", "description": "按事件类型过滤（可选）"},
            "enabled": {"type": "boolean", "description": "按启用状态过滤（可选）"},
            "risk_level": {"type": "string", "description": "按风险等级过滤：low/high（可选）", "enum": ["low", "high"]},
        },
        required=[],
    ),
    generate_tool_schema(
        name="update_trigger",
        description=(
            "更新管家实时决策触发规则。高风险更新（修改动作/条件）需 confirm=true。"
        ),
        parameters={
            "trigger_id": {"type": "string", "description": "要更新的触发规则 ID"},
            "trigger_json": {"type": "object", "description": "更新后的触发规则定义"},
            "confirm": {"type": "boolean", "description": "高风险更新需 confirm=true", "default": False},
        },
        required=["trigger_id", "trigger_json"],
    ),
    generate_tool_schema(
        name="delete_trigger",
        description=(
            "删除管家实时决策触发规则。必须 confirm=true（高风险操作，Server 侧硬编码）。"
        ),
        parameters={
            "trigger_id": {"type": "string", "description": "要删除的触发规则 ID"},
            "confirm": {"type": "boolean", "description": "必须为 true 才会删除"},
        },
        required=["trigger_id", "confirm"],
    ),
    generate_tool_schema(
        name="run_skill",
        description=(
            "执行管家白名单内技能（查询类/分析类/通知类）。"
            "危险技能（直接控制设备类）不在白名单内，调用返回错误；"
            "白名单是 server 侧硬闸，没有 confirm 参数可以绕过（旧版声明过 confirm=true，代码从不读取，已删）。"
        ),
        parameters={
            "skill_id": {"type": "string", "description": "技能 ID"},
            "params": {"type": "object", "description": "技能参数（可选）"},
            "dry_run": {"type": "boolean",
                          "description": "true＝只走闸门不真执行（DCD 裁定 C）。默认 false",
                          "default": False},
        },
        required=["skill_id"],
    ),
    generate_tool_schema(
        name="send_bark",
        description=(
            "发送 Bark 推送到用户手机（自动走 PushGuard 风控，频繁推送会被熔断）。"
            "何时调用：需要通知用户时。何时不要调用：频繁推送（会被风控熔断丢弃）。"
        ),
        parameters={
            "title": {"type": "string", "description": "推送标题"},
            "body": {"type": "string", "description": "推送正文"},
            "priority": {"type": "string", "description": "优先级：critical/warning/info", "enum": ["critical", "warning", "info"], "default": "info"},
            "group": {"type": "string", "description": "推送分组（可选，用于合并同类推送）"},
        },
        required=["title", "body"],
    ),
    # ===== v2.0 技能工厂 =====
    generate_tool_schema(
        name="list_skills",
        description=(
            "列出管家所有技能（含 id/name/engine/enabled/health）。"
            "只读，无副作用。"
        ),
        parameters={
            "engine": {"type": "string", "description": "按引擎类型过滤（可选，如 cron_task/llm_text）"},
            "enabled": {"type": "boolean", "description": "按启用状态过滤（可选）"},
        },
        required=[],
    ),
    generate_tool_schema(
        name="create_skill",
        description=(
            "创建管家技能。支持 cron_task（定时条件任务）、llm_text（LLM播报）、static_text（静态文本）等引擎。"
            "何时调用：用户要求创建自动化/提醒/播报技能时。"
            "cron_task 技能需提供 api_id/condition/message_template。"
        ),
        parameters={
            "skill_json": {"type": "object", "description": "技能定义（id/name/trigger/brain/output 等）"},
            "confirm": {"type": "boolean", "description": "创建后是否立即启用（默认 false，需用户确认）", "default": False},
        },
        required=["skill_json"],
    ),
    generate_tool_schema(
        name="delete_skill",
        description=(
            "删除管家技能。必须 confirm=true（高风险操作）。"
        ),
        parameters={
            "skill_id": {"type": "string", "description": "要删除的技能 ID"},
            "confirm": {"type": "boolean", "description": "必须为 true 才会删除"},
        },
        required=["skill_id", "confirm"],
    ),
    generate_tool_schema(
        name="get_skill_health",
        description=(
            "获取技能执行健康度（总执行次数/连续失败/成功率/最近状态）。"
            "只读，无副作用。"
        ),
        parameters={
            "skill_id": {"type": "string", "description": "技能 ID（可选，不传则返回所有技能健康度）"},
        },
        required=[],
    ),
    generate_tool_schema(
        name="list_cron_apis",
        description=(
            "列出已注册的 HTTP API（供 cron_task 技能使用）。"
            "只读，无副作用。密钥已脱敏。"
        ),
        parameters={},
        required=[],
    ),
]

# 3 个只读 Resources 定义
MCP_RESOURCES = [
    {
        "uri": "butler://triggers",
        "name": "管家触发规则列表",
        "description": "获取所有管家实时决策触发规则（含 id/name/event/conditions/actions/enabled/last_run_at）",
        "mimeType": "application/json",
    },
    {
        "uri": "butler://skills",
        "name": "管家可用技能列表",
        "description": "获取白名单内可用技能（含 id/name/description/version/enabled）",
        "mimeType": "application/json",
    },
    {
        "uri": "butler://guard/status",
        "name": "推送风控状态",
        "description": "获取 PushGuard 风控状态（熔断维度/队列长度/过载状态/最近审计）",
        "mimeType": "application/json",
    },
]


def get_tools() -> list[dict]:
    """获取所有 MCP Tools Schema。"""
    return MCP_TOOLS


def get_resources() -> list[dict]:
    """获取所有 MCP Resources 定义。"""
    return MCP_RESOURCES


def get_tool_names() -> list[str]:
    """获取所有工具名称列表。"""
    return [t["name"] for t in MCP_TOOLS]
