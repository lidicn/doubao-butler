"""画像页 API：成员档案（profile_json）读写、LLM 整理、管家观察展示与撤销。

数据全部来自 memory-agent（butler_token 窄接口 + MCP）：
- 成员与 profile：GET /api/members、PATCH /api/members/{id}
- 管家观察：成员 tags（agent 观察，带 evidence/confidence/source）
- 撤销观察：MCP revoke_memory（软删墓碑，不在窄接口白名单内）
"""
from __future__ import annotations

import json

from starlette.requests import Request
from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.api.profile")

ORGANIZE_SYSTEM = """你是家庭画像整理助手。把用户粘贴的口语化描述整理成结构化 JSON。
只输出 JSON，不要解释，不要 markdown 代码块。字段约定：
{
  "routine": [{"label": "起床", "time": "07:00", "note": ""}],
  "courses": [{"day": "周一", "time": "16:00-17:30", "name": "数学", "note": ""}],
  "interests": ["画画", "乐高"],
  "habits": ["睡前听故事"],
  "notes": "其它补充说明"
}
缺失的字段给空数组或空字符串，不要编造内容。"""


def _shape(m: dict) -> dict:
    return {
        "id": m.get("id"),
        "name": m.get("name"),
        "rooms": m.get("rooms") or [],
        "profile": m.get("profile") if isinstance(m.get("profile"), dict) else {},
        "tags": m.get("tags") or [],
        "avatar_url": m.get("avatar_url") or "",
    }


async def members(request: Request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    try:
        items = await rt.memory.get_members()
    except Exception as e:
        logger.warning("profile members failed: %s", e)
        return err(f"MA 成员读取失败：{e}", 502)
    return ok({"items": [_shape(m) for m in items if isinstance(m, dict)]})


async def get_profile(request: Request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    name = request.path_params.get("name", "")
    try:
        items = await rt.memory.get_members()
    except Exception as e:
        logger.warning("profile get failed: %s", e)
        return err(f"MA 成员读取失败：{e}", 502)
    member = next((m for m in items if isinstance(m, dict) and m.get("name") == name), None)
    if member is None:
        return err("member not found", 404)
    try:
        schedule = await rt.memory.member_schedule(name, days=14)
    except Exception:
        schedule = {}
    return ok({
        "member": _shape(member),
        "observations": member.get("tags") or [],
        "schedule": schedule,
    })


async def put_profile(request: Request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    name = request.path_params.get("name", "")
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    profile = (body or {}).get("profile")
    if not isinstance(profile, dict):
        return err("profile object required")
    written = await rt.memory.update_member_profile(name, profile)
    if not written:
        return err("写回失败（成员不存在或 MA 拒绝）", 502)
    return ok({"name": name, "profile": profile})


async def organize(request: Request):
    """贴文本 → LLM 整理 → 返回结构化建议（不写库，等用户在确认框里定稿）。"""
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    text = str((body or {}).get("text", "")).strip()
    if not text:
        return err("text required")
    name = str((body or {}).get("name", "")).strip()
    msgs = [{"role": "user", "content": f"成员：{name or '未指定'}\n描述：{text}"}]
    try:
        raw, _ = await rt.llm.chat(ORGANIZE_SYSTEM, msgs, max_tokens=800, temperature=0.3)
    except Exception as e:
        logger.warning("profile organize failed: %s", e)
        return err(f"LLM 整理失败：{e}", 502)
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        profile = json.loads(raw)
    except Exception:
        return err("LLM 输出不是合法 JSON，请重试或手动改")
    if not isinstance(profile, dict):
        return err("LLM 输出必须是对象")
    return ok({"profile": profile, "raw": raw})


async def revoke(request: Request):
    g = guard(request)
    if g:
        return g
    rt = get_runtime()
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    mid = str((body or {}).get("memory_id", "")).strip()
    if not mid:
        return err("memory_id required")
    res = await rt.memory.revoke_observation(mid)
    if not res.get("ok"):
        return err(str(res.get("raw") or "撤销失败")[:300], 502)
    return ok({"revoked": mid})


def routes():
    return [
        Route("/api/profile/members", members, methods=["GET"]),
        Route("/api/profile/organize", organize, methods=["POST"]),
        Route("/api/profile/revoke", revoke, methods=["POST"]),
        Route("/api/profile/{name}", get_profile, methods=["GET"]),
        Route("/api/profile/{name}", put_profile, methods=["PATCH"]),
    ]
