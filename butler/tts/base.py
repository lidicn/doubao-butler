"""TTS 抽象层：新增引擎只需实现 TTSProvider。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TTSResult:
    filename: str       # 容器内相对文件名，如 <sha1>.mp3
    public_url: str     # 可被 TV 访问的 URL
    provider: str       # "edge-tts" | "kokoro"
    voice: str
    duration_ms: int
    cached: bool


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    async def is_available(self) -> bool: ...

    async def synthesize(self, text: str, voice: str, speed: float) -> TTSResult: ...


def cache_filename(text: str, voice: str, engine: str, speed: float) -> str:
    """缓存文件名的唯一公式：text|voice|engine|speed（speed 缺位＝改语速仍播旧音频）。"""
    return hashlib.sha1(f"{text}|{voice}|{engine}|{speed}".encode("utf-8")).hexdigest() + ".mp3"


def rate_percent(speed: float) -> int:
    """语速倍率→有符号百分点（1.0＝0）：edge 的 `+/-N%` 与 nowvoice 的 `rate` 共用这一份公式，⛔ 各抄一遍（批15 那批的 `cache_filename` 就是四份各抄的老形状）。
    """
    return int(round((speed - 1.0) * 100))
