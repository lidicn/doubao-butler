"""用户位置（房间）管理 + 开发助理 TTS 播报。

房间→小爱设备映射存在 config，当前房间存在 SQLite。
开发助理用 edge-tts 少用女声合成，推送到当前房间的小爱播放。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from butler.config import get_settings
from butler.logging_setup import get_logger

logger = get_logger("butler.user_location")

# 房间列表（可配置）
DEFAULT_ROOMS = ["主卧室", "书房", "起居室", "客厅", "主卧室浴室", "卫生间"]

# 开发助理音色（edge-tts 少用女声，区别于默认 Xiaoxiao）
DEV_ASSISTANT_VOICE = "zh-CN-XiaoyiNeural"

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        s = get_settings()
        Path(s.data_dir).mkdir(parents=True, exist_ok=True)
        db_path = Path(s.data_dir) / "butler.db"
        c = sqlite3.connect(str(db_path), check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        _conn = c
        _init(c)
        logger.info("user_location store opened at %s", db_path)
    return _conn


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_location (
            id        INTEGER PRIMARY KEY CHECK (id = 1),
            room      TEXT NOT NULL DEFAULT '客厅',
            updated_at REAL NOT NULL
        );
        INSERT OR IGNORE INTO user_location (id, room, updated_at) VALUES (1, '客厅', 0);
        """
    )


def get_current_room() -> str:
    c = get_conn()
    row = c.execute("SELECT room FROM user_location WHERE id = 1").fetchone()
    return row["room"] if row else "客厅"


def set_current_room(room: str) -> str:
    if room not in DEFAULT_ROOMS:
        raise ValueError(f"invalid room: {room}, must be one of {DEFAULT_ROOMS}")
    c = get_conn()
    c.execute("UPDATE user_location SET room = ?, updated_at = ? WHERE id = 1",
              (room, time.time()))
    c.commit()
    logger.info("user location set to %s", room)
    return room


def get_rooms() -> list[str]:
    return list(DEFAULT_ROOMS)


def get_room_xiaoai_entity(room: str) -> str:
    """获取房间对应的小爱 notify entity_id。从 config 读取映射。"""
    s = get_settings()
    mapping = getattr(s, "room_xiaoai_mapping", {}) or {}
    return mapping.get(room, s.xiaomi_notify_entity)


async def dev_assistant_speak(text: str, room: str | None = None) -> dict:
    """开发助理播报：用指定音色 edge-tts 合成 → 推送到当前房间小爱。

    返回 {"ok": bool, "message": str}
    """
    from butler.runtime import get_runtime
    rt = get_runtime()
    target_room = room or get_current_room()

    # 1. edge-tts 合成（开发助理音色）
    try:
        tts = rt.tts
        result = await tts.synthesize(text, voice=DEV_ASSISTANT_VOICE, backend="edge-tts")
        if not result:
            return {"ok": False, "message": "TTS 合成失败"}
        audio_url = result.public_url
        logger.info("dev assistant TTS ok: %s url=%s", DEV_ASSISTANT_VOICE, audio_url)
    except Exception as e:
        logger.warning("dev assistant TTS failed: %s", e)
        return {"ok": False, "message": f"TTS 合成异常: {e}"}

    # 2. 推送到当前房间小爱
    try:
        ha = rt.ha
        entity_id = get_room_xiaoai_entity(target_room)
        # 尝试直连小爱云端播放（一次性）
        xiaoai_id = await ha.get_xiaoai_id(entity_id)
        if xiaoai_id:
            r = await ha.play_xiaomi_url_once(xiaoai_id, audio_url, method="url")
            if r.get("code") == 0:
                return {"ok": True, "message": f"已在{target_room}播报", "room": target_room}
            logger.warning("play_xiaomi_url_once failed: %s", r)
        # 兜底：HA tts_play_url
        r2 = await ha.tts_play_url(audio_url, entity_id)
        return {"ok": True, "message": f"已在{target_room}播报（HA通道）", "room": target_room}
    except Exception as e:
        logger.warning("dev assistant play failed: %s", e)
        return {"ok": False, "message": f"小爱播放异常: {e}", "audio_url": audio_url}
