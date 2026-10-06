"""技能域（从 butler/core/tools.py 拆分）。"""
from __future__ import annotations

import asyncio
import time

from butler.logging_setup import get_logger

logger = get_logger("butler.tools")

async def _create_skill(agent, args: dict) -> str:
    """创建技能草稿。"""
    description = (args.get("description") or "").strip()
    if not description:
        return "请描述你想要的技能，比如「每天早上7点看到Kevin就说早上好」"
    skill_json = args.get("skill_json") or ""

    # 获取 creator
    creator = getattr(agent, "skill_creator", None)
    if creator is None:
        # 尝试从 runtime 获取
        try:
            from butler.runtime import get_runtime
            rt = get_runtime()
            creator = getattr(rt, "skill_creator", None)
        except Exception:
            pass
    if creator is None:
        return "技能创建器未初始化，请稍后再试。"

    # 如果用户直接提供了 JSON
    if skill_json:
        try:
            import json as _json
            skill = _json.loads(skill_json)
        except Exception as e:
            return f"技能JSON格式错误: {e}"
    else:
        # 调用 LLM 生成
        result = await creator.generate_skill_from_description(description, agent.llm)
        if not result.get("ok"):
            return f"生成技能失败: {result.get('error')}"
        skill = result["skill"]

    # 创建草稿
    role_id = getattr(agent, "current_role", "butler")
    result = creator.create_draft(skill, role_id)
    if not result.get("ok"):
        return f"创建技能草稿失败: {result.get('error')}"

    draft = result["draft"]
    preview = result["preview"]
    return (
        f"我为你创建了一个技能草稿：\\n\\n{preview}\\n\\n"
        f"确认创建请说「确认」，需要修改请告诉我哪里要改，取消请说「取消」。"
    )
