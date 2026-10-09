"""配置路由：人格、成员、唤醒与免打扰、TTS 设置（持久化到 data/config.json）。"""
from __future__ import annotations

import json
from pathlib import Path

from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.config import MemberConfig, get_settings
from butler.core.atomic_json import read_json_object, write_json_atomic
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.api.config")

# D1（批70b）：「读不出来」≠「没有数据」。_load_cfg 兜底空 dict 的口径不变（批17），
# 但兜底之后这份内存态不完整——_cfg_unreadable 记一位，_save_cfg 拿它拒整份覆盖写。
_cfg_unreadable = False


def _load_cfg() -> dict:
    global _cfg_unreadable
    s = get_settings()
    p = Path(s.data_dir) / "config.json"
    cfg, unreadable = read_json_object(p)
    _cfg_unreadable = unreadable
    if unreadable and p.exists():
        # R2-08 fix: log error instead of silently returning {}
        logger.error("config.json parse failed, returning empty: %s (unreadable, writes refused)", p)
    return cfg


def _save_cfg(d: dict) -> bool:
    """整份覆盖写。读不出来时拒绝落盘、盘上一个字节不动；返回是否真正保存（调用方据此如实回报）。"""
    s = get_settings()
    Path(s.data_dir).mkdir(parents=True, exist_ok=True)
    cfg_path = Path(s.data_dir) / "config.json"
    if _cfg_unreadable:
        # 写侧拒绝必须自己喊：这是「保存失败」级别的用户可见事件，只在读侧留痕不够。
        logger.error("config.json unreadable, refusing full overwrite to preserve on-disk scene: %s",
                     cfg_path)
        return False
    # R2-08 fix: atomic write (tmp + os.replace) to prevent corruption
    write_json_atomic(cfg_path, d)
    # 重新载入到内存
    get_settings()._load_persona()
    return True


async def get_config(request):
    g = guard(request)
    if g:
        return g
    s = get_settings()
    return ok(
        {
            "system": s.persona.system,
            "greeting_template": s.persona.greeting_template,
            "members": [m.__dict__ for m in s.persona.members],
            "cooldown_seconds": s.cooldown_seconds,
            "dnd_windows": s.dnd_windows,
            "tts": {
                "primary": s.tts_primary,
                "edge_voice": s.tts_edge_voice,
                "kokoro_voice": s.tts_kokoro_voice,
                "speed": s.tts_speed,
                "volume": s.tts_volume,
                "cache": s.tts_cache_enabled,
            },
        }
    )


async def save_persona(request):
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    cfg = _load_cfg()
    if "system" in body:
        cfg["system"] = body["system"]
    if "greeting_template" in body:
        cfg["greeting_template"] = body["greeting_template"]
    if not _save_cfg(cfg):
        return err("config.json unreadable, change refused to avoid overwriting it", 500)
    return ok({"saved": True})


async def save_member(request):
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    cfg = _load_cfg()
    members = cfg.get("members", [])
    mid = str(body.get("id") or body.get("name") or "")
    found = False
    for m in members:
        if m.get("id") == mid or m.get("name") == body.get("name"):
            m.update(body)
            found = True
            break
    if not found:
        members.append(body)
    cfg["members"] = members
    if not _save_cfg(cfg):
        return err("config.json unreadable, change refused to avoid overwriting it", 500)
    return ok({"saved": True})


async def save_wakeup(request):
    g = guard(request)
    if g:
        return g
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    cfg = _load_cfg()
    if "cooldown_seconds" in body:
        cfg["cooldown_seconds"] = int(body["cooldown_seconds"])
        get_settings().cooldown_seconds = int(body["cooldown_seconds"])
    if "dnd_windows" in body:
        cfg["dnd_windows"] = body["dnd_windows"]
        get_settings().dnd_windows = list(body["dnd_windows"])
    if not _save_cfg(cfg):
        return err("config.json unreadable, change refused to avoid overwriting it", 500)
    return ok({"saved": True})


def routes():
    return [
        Route("/api/config", get_config, methods=["GET"]),
        Route("/api/config/persona", save_persona, methods=["POST"]),
        Route("/api/config/member", save_member, methods=["POST"]),
        Route("/api/config/wakeup", save_wakeup, methods=["POST"]),
    ]
