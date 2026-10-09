"""Kokoro-82M ONNX 实现：本地离线、隐私。通过 OpenAI 兼容的 /v1/audio/speech HTTP 调用。"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from butler.config import Settings
from butler.logging_setup import get_logger
from butler.tts.base import TTSResult, cache_filename

logger = get_logger("butler.tts.kokoro")


class KokoroTTS:
    name = "kokoro"

    def __init__(self, settings: Settings):
        self.s = settings

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(self.s.tts_kokoro_url.rsplit("/v1/audio/speech", 1)[0] + "/health")
                return r.status_code == 200
        except Exception:
            return False

    async def synthesize(self, text: str, voice: str, speed: float) -> TTSResult:
        Path(self.s.tts_dir).mkdir(parents=True, exist_ok=True)
        filename = cache_filename(text, voice, "kokoro", speed)
        path = Path(self.s.tts_dir) / filename
        t0 = time.time()
        async with httpx.AsyncClient(timeout=self.s.llm_timeout * 2) as c:
            r = await c.post(
                self.s.tts_kokoro_url,
                json={
                    "model": "kokoro",
                    "input": text,
                    "voice": voice,
                    "response_format": "mp3",
                    "speed": speed,
                },
            )
            r.raise_for_status()
            path.write_bytes(r.content)
        duration_ms = int((time.time() - t0) * 1000)
        logger.info("kokoro synth %dms voice=%s", duration_ms, voice)
        return TTSResult(
            filename=filename,
            public_url=f"{self.s.base_url.rstrip('/')}/tts/{filename}",
            provider="kokoro",
            voice=voice,
            duration_ms=duration_ms,
            cached=False,
        )
