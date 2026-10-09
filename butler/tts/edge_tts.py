"""edge-tts 实现：使用 CLI 子进程合成，避免 uvicorn 事件循环干扰 websocket。"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

from butler.config import Settings
from butler.logging_setup import get_logger
from butler.tts.base import TTSResult, cache_filename, rate_percent

logger = get_logger("butler.tts.edge")


def _rate_str(speed: float) -> str:
    pct = rate_percent(speed)
    return f"{pct:+d}%"


class EdgeTTS:
    name = "edge-tts"
    MAX_RETRY = 3

    def __init__(self, settings: Settings):
        self.s = settings

    async def is_available(self) -> bool:
        return True

    async def _synth_once(self, text: str, voice: str, speed: float, path: Path) -> None:
        # 用 edge-tts CLI 子进程合成（独立进程，不受 uvicorn 事件循环干扰）
        cmd = [
            sys.executable, "-m", "edge_tts",
            "-t", text,
            "-v", voice,
            "--rate", _rate_str(speed),
            "--write-media", str(path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode()[-300:] if stderr else "unknown"
            raise RuntimeError(f"edge-tts CLI failed: {err}")
        size = path.stat().st_size
        if size < 3000:
            raise RuntimeError(f"edge-tts audio too small: {size}B")
        logger.info("edge-tts CLI synth: %d chars -> %d bytes", len(text), size)

    async def synthesize(self, text: str, voice: str, speed: float) -> TTSResult:
        Path(self.s.tts_dir).mkdir(parents=True, exist_ok=True)
        filename = cache_filename(text, voice, "edge-tts", speed)
        path = Path(self.s.tts_dir) / filename
        last_err = None
        for attempt in range(1, self.MAX_RETRY + 1):
            try:
                t0 = time.monotonic()
                await self._synth_once(text, voice, speed, path)
                duration_ms = int((time.monotonic() - t0) * 1000)
                logger.info("edge-tts synth ok voice=%s attempt=%d", voice, attempt)
                return TTSResult(
                    filename=filename,
                    public_url=f"{self.s.base_url.rstrip('/')}/tts/{filename}",
                    provider="edge-tts",
                    voice=voice,
                    duration_ms=duration_ms,
                    cached=False,
                )
            except Exception as e:
                last_err = e
                logger.warning("edge-tts attempt %d failed: %s", attempt, e)
                if path.exists():
                    try:
                        path.unlink()
                    except Exception:
                        pass
                if attempt < self.MAX_RETRY:
                    await asyncio.sleep(1.5 * attempt)
        raise last_err or RuntimeError("edge-tts failed")
