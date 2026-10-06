"""推送管理路由：Bark/微信/豆包app 通道状态 + 技能推送配置。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.api.push")

SKILL_DIR = Path("/app/data/skills/user")


async def push_channels(request):
    """获取所有推送通道状态。"""
    g = guard(request)
    if g:
        return g

    rt = get_runtime()
    s = rt.settings

    # Bark 状态
    bark_configured = bool(s.bark_url and s.bark_key)
    bark_enabled = getattr(rt, "bark", None) is not None

    # 微信 iLink 状态
    ilink_client = getattr(rt, "ilink_client", None)
    ilink_logged_in = bool(ilink_client and ilink_client.is_logged_in)

    # 豆包 app 推送状态
    doubao_configured = bool(s.doubao_base_url)

    return ok({
        "channels": {
            "bark": {
                "name": "Bark（iOS 推送）",
                "configured": bark_configured,
                "enabled": bark_enabled,
                "base_url": s.bark_url[:30] + "..." if s.bark_url else None,
            },
            "wechat": {
                "name": "微信（iLink Bot）",
                "configured": ilink_logged_in,
                "enabled": ilink_logged_in,
                "bot_id": getattr(ilink_client, "bot_id", None) if ilink_client else None,
            },
            "doubao_app": {
                "name": "豆包 app 对话推送",
                "configured": doubao_configured,
                "enabled": True,  # 只要配置了就启用
            },
        }
    })


async def push_skills(request):
    """获取所有技能的推送配置。"""
    g = guard(request)
    if g:
        return g

    skills = []
    if SKILL_DIR.exists():
        for f in sorted(SKILL_DIR.glob("*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    d = json.load(fp)

                skill_id = d.get("id", f.stem)
                skill_name = d.get("name", skill_id)
                enabled = d.get("status", "enabled") == "enabled" and d.get("enabled", True)

                # 检查 output 数组里有没有 bark
                output = d.get("output", [])
                bark_enabled = False
                if isinstance(output, list):
                    for o in output:
                        if isinstance(o, dict) and o.get("type") == "bark":
                            bark_enabled = True
                elif isinstance(output, dict):
                    bark_enabled = output.get("bark", False)

                # 顶层 push_to_app
                push_to_app = d.get("push_to_app", False)

                # 微信推送（暂时和 bark 共用，后续可独立配置）
                wechat_enabled = bark_enabled  # 暂时用 bark 配置代替

                skills.append({
                    "id": skill_id,
                    "name": skill_name,
                    "enabled": enabled,
                    "bark": bark_enabled,
                    "wechat": wechat_enabled,
                    "doubao_app": push_to_app,
                })
            except Exception as e:
                logger.warning("failed to read skill %s: %s", f, e)

    return ok({"skills": skills})


async def push_skill_update(request):
    """更新某个技能的推送配置。"""
    g = guard(request)
    if g:
        return g

    skill_id = request.path_params.get("skill_id", "")
    skill_file = SKILL_DIR / f"{skill_id}.json"

    if not skill_file.exists():
        return err(f"skill not found: {skill_id}", 404)

    try:
        body = await request.json()
    except Exception:
        return err("invalid json")

    try:
        with open(skill_file, "r", encoding="utf-8") as fp:
            d = json.load(fp)

        # 更新 bark 配置
        if "bark" in body:
            bark_enabled = bool(body["bark"])
            output = d.get("output", [])

            if isinstance(output, list):
                # 移除旧的 bark 输出
                output = [o for o in output if not (isinstance(o, dict) and o.get("type") == "bark")]
                # 如果启用，添加新的 bark 输出
                if bark_enabled:
                    output.append({"type": "bark"})
                d["output"] = output
            elif isinstance(output, dict):
                output["bark"] = bark_enabled
                d["output"] = output

        # 更新 push_to_app
        if "doubao_app" in body:
            d["push_to_app"] = bool(body["doubao_app"])

        # 写回文件
        with open(skill_file, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False, indent=2)

        # 热加载技能
        rt = get_runtime()
        runner = getattr(rt, "runner", None)
        if runner and hasattr(runner, "store") and hasattr(runner.store, "reload"):
            runner.store.reload()

        return ok({"updated": True, "skill_id": skill_id})
    except Exception as e:
        logger.warning("failed to update skill %s: %s", skill_id, e)
        return err(f"update failed: {str(e)}", 500)


def routes():
    return [
        Route("/api/push/channels", push_channels, methods=["GET"]),
        Route("/api/push/skills", push_skills, methods=["GET"]),
        Route("/api/push/skills/{skill_id}", push_skill_update, methods=["PUT"]),
    ]
