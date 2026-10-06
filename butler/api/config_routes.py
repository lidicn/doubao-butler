"""配置路由：人格、成员、唤醒与免打扰、TTS 设置（持久化到 data/config.json）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from starlette.routing import Route

from butler.api.deps import err, guard, ok
from butler.config import MemberConfig, get_settings
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.api.config")


def _load_cfg() -> dict:
    s = get_settings()
    p = Path(s.data_dir) / "config.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            # R2-08 fix: log error instead of silently returning {}
            logger.error("config.json parse failed, returning empty: %s", e)
            return {}
    return {}


def _save_cfg(d: dict) -> None:
    s = get_settings()
    Path(s.data_dir).mkdir(parents=True, exist_ok=True)
    # R2-08 fix: atomic write (tmp + os.replace) to prevent corruption
    cfg_path = Path(s.data_dir) / "config.json"
    tmp_path = cfg_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp_path), str(cfg_path))
    # 重新载入到内存
    get_settings()._load_persona()


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
    _save_cfg(cfg)
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
    _save_cfg(cfg)
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
    _save_cfg(cfg)
    return ok({"saved": True})


def routes():
    return [
        Route("/api/config", get_config, methods=["GET"]),
        Route("/api/config/persona", save_persona, methods=["POST"]),
        Route("/api/config/member", save_member, methods=["POST"]),
        Route("/api/config/wakeup", save_wakeup, methods=["POST"]),
    ]
