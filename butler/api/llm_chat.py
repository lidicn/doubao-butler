"""兼容 NR LLM 中枢的 /api/llm/chat 端点。

NR 历史上有一个 /llm/chat 端点（hub_chat_in），被 arcface 等 tab 调用生成问候语。
v0.8 NR 瘦身后，这个端点迁移到 butler，输入输出格式保持兼容。

输入: {"scenario": "face", "user": "大佬", "user_msg": "跟我打个招呼吧", "model": "doubao", "system": "..."}
输出: {"reply": "..."}
"""
from __future__ import annotations

import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from butler.integrations.llm import LLMClient
from butler.logging_setup import get_logger
from butler.runtime import get_runtime
from butler.api.deps import guard

logger = get_logger("butler.llm_chat")

# 人设映射（与 NR hub_chat_orch 保持一致）
PERSONAS = {
    "大佬": "40岁，喜欢用飞牛TV看电视剧和电影，偶尔玩Xbox。用轻松自然的语气，1-2句话打招呼，可顺口问要不要继续看剧。",
    "凯文": "12岁男孩，喜欢玩Xbox游戏。用热情、孩子气的口语打招呼，1-2句，可以问要不要玩会儿游戏。",
    "爱美丽": "10岁女孩，喜欢听歌。用温柔可爱的语气，1-2句，可以问想听什么歌。",
}

# 简单的多轮对话记忆（scenario+user -> messages list）
# NR 原实现用 flow context，这里用 in-memory dict（容器重启丢失，可接受）
_history: dict[str, list[dict]] = {}


def _time_of_day() -> str:
    h = time.localtime().tm_hour
    if h < 6:
        return "凌晨"
    if h < 11:
        return "早上"
    if h < 14:
        return "中午"
    if h < 18:
        return "下午"
    return "晚上"


def _build_system(user: str, scenario: str, sys_override: str | None) -> str:
    if sys_override:
        return sys_override
    persona = PERSONAS.get(user, "家里的成员。用亲切自然的口语打招呼，1-2句话，像家人闲聊。")
    tod = _time_of_day()
    return (
        f"你是家里AI助手，住在客厅电视里。当前用户：{user}。{persona} "
        f"当前是{tod}，可以结合时间自然带一句。回复控制在1-2句话，口语化，不要太正式。"
    )


async def handle_llm_chat(request: Request) -> JSONResponse:
    """POST /api/llm/chat — 兼容 NR LLM 中枢。"""
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"reply": "请求格式错误"}, status_code=400)

    user = body.get("user") or "朋友"
    scenario = body.get("scenario") or "default"
    user_msg = body.get("user_msg") or body.get("message") or "（打招呼）"
    system_prompt = _build_system(user, scenario, body.get("system"))

    # 多轮记忆
    hist_key = f"llm_{scenario}_{user}"
    hist = _history.get(hist_key, [])
    hist.append({"role": "user", "content": user_msg})
    if len(hist) > 16:
        hist = hist[-16:]

    # 调用 LLM
    rt = get_runtime()
    llm = rt.llm
    try:
        reply, _ = await llm.chat(system_prompt, hist, max_tokens=200, temperature=0.9)
    except Exception as e:
        logger.warning("llm_chat failed: %s", e)
        return JSONResponse({"reply": "模型服务暂时不可用，请稍后再试"})

    # 保存 assistant 回复
    hist.append({"role": "assistant", "content": reply})
    _history[hist_key] = hist

    logger.info("llm_chat scenario=%s user=%s reply=%s", scenario, user, reply[:50])
    return JSONResponse({"reply": reply})


def routes():
    from starlette.routing import Route
    return [
        Route("/api/llm/chat", handle_llm_chat, methods=["POST"]),
    ]
