"""NowVoice TTS 引擎：拟真人音色。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from butler.config import Settings
from butler.logging_setup import get_logger
from butler.tts.base import TTSResult, cache_filename, rate_percent
from butler.tts.nowvoice_client import NowVoiceClient

logger = get_logger("butler.tts.nowvoice")

# 一枚重试 sleep 的宽限：内层的 budget 出声要先于外层，外层只兜 SDK 腿卡死这一种情况。
_OUTER_GRACE_S = 2.0


class NowVoiceTTS:
    name = "nowvoice"

    def __init__(self, settings: Settings):
        self.s = settings
        self.client = NowVoiceClient()
        self._token = ""
        self._logged_in = False

    async def is_available(self) -> bool:
        return True  # 只要配置了 token 就可用

    async def _ensure_login(self):
        if self._logged_in:
            return
        # 用配置的 token
        token = getattr(self.s, "nowvoice_token", "")
        if token:
            self.client.token = token
            self._logged_in = True
            logger.info("NowVoice TTS: using configured token")
        else:
            # 匿名登录
            try:
                result = await asyncio.get_running_loop().run_in_executor(
                    None, self.client.login
                )
                if self.client.token:
                    self._logged_in = True
                    logger.info("NowVoice TTS: anonymous login success")
            except Exception as e:
                logger.warning("NowVoice login failed: %s", e)

    async def synthesize(self, text: str, voice: str, speed: float) -> TTSResult:
        await self._ensure_login()
        loop = asyncio.get_running_loop()
        budget = float(self.s.nowvoice_timeout_sec)

        def _gen_and_download():
            """同步生成+下载，创建新 client 避免状态污染。"""
            import time
            deadline = time.monotonic() + budget
            c = NowVoiceClient()
            c.token = self.client.token
            logger.info("NowVoice TTS: text=%r voice=%r", text[:80], voice)
            result = c.generate_speech(
                text=text, voice_id=voice, language_code="zh-CN", rate=rate_percent(speed)
            )
            _masked_url = (result.url[:60] + "...") if result.url and len(result.url) > 60 else result.url
            logger.info("NowVoice TTS: gen result success=%s url=%s err=%s", result.success, _masked_url, result.error_msg)
            if not result.success:
                raise RuntimeError(f"NowVoice TTS failed: {result.error_msg}")
            if not result.url:
                raise RuntimeError("NowVoice TTS: no audio URL")
            # 立即下载：总预算＝nowvoice_timeout_sec。旧形状 5×(30+2)＝160 s 没有总上限，
            # 一枚坏 URL 能把这条播报链占满一整套重试时刻。
            resp = None
            for i in range(5):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"NowVoice TTS: download budget exhausted ({budget:.0f}s) after {i} attempts"
                    )
                resp = httpx.get(result.url, timeout=min(30.0, max(0.1, remaining)))
                logger.info("NowVoice TTS: download attempt %d status=%d len=%d", i+1, resp.status_code, len(resp.content))
                if resp.status_code == 200 and len(resp.content) > 100:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"NowVoice TTS: download budget exhausted ({budget:.0f}s) after {i + 1} attempts"
                    )
                time.sleep(min(2.0, remaining))
            if resp is None or resp.status_code != 200 or len(resp.content) <= 100:
                got = 0 if resp is None else len(resp.content)
                raise RuntimeError(f"NowVoice TTS: download failed, got {got} bytes")
            return result.url, resp.content

        # 外层兜底：generate_speech 是 SDK 同步腿，内层检查点到不了它，只有这里能释放调用方。
        # ⛔ 这不是"杀掉线程"：超时后那枚线程仍会跑到 SDK 自己返回——解的是播报链的等待，泄漏的仍是线程。
        try:
            audio_url, data = await asyncio.wait_for(
                loop.run_in_executor(None, _gen_and_download), budget + _OUTER_GRACE_S
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"NowVoice TTS: outer timeout after {budget + _OUTER_GRACE_S:.0f}s"
                "（生成腿不可中断，调用方已释放）"
            ) from exc

        # 缓存文件名
        filename = cache_filename(text, voice, "nowvoice", speed)
        path = Path(self.s.tts_dir) / filename
        path.write_bytes(data)
        logger.info("NowVoice TTS: downloaded %d bytes -> %s", len(data), filename)

        return TTSResult(
            filename=filename,
            public_url=f"{self.s.base_url.rstrip('/')}/tts/{filename}",
            provider="nowvoice",
            voice=voice,
            duration_ms=0,
            cached=False,
        )
