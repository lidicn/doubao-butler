"""TTS 管理器：多引擎调度、熔断降级、sha1 缓存、音频文件清理。

降级链：主引擎失败 → 备引擎失败 → Bark 纯文字推送。
任一级失败不阻塞对话状态机，只记录降级原因。
引擎熔断：连续失败 N 次自动跳过，30s 后半开探测。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from butler.config import Settings
from butler.logging_setup import get_logger
from butler.modes.engine import gate_tts
from butler.tts.base import TTSResult, cache_filename
from butler.tts.edge_tts import EdgeTTS
from butler.tts.kokoro import KokoroTTS
from butler.tts.nowvoice_client import SAMPLE_VOICES
from butler.tts.nowvoice_tts import NowVoiceTTS
from butler.tts.playback_queue import PlaybackQueue
from butler.tts.queue import PRIORITY_ALERT

logger = get_logger("butler.tts.manager")


def _is_hex8(v) -> bool:
    return bool(v) and len(v) == 8 and all(c in "0123456789abcdef" for c in v.lower())


def _voice_name_to_hex(name: str | None) -> str | None:
    """角色档嗓子名（如 zh-CN-YunyangNeural）→ nowvoice 对照表的 8 位号。"""
    if not name:
        return None
    return SAMPLE_VOICES.get(name.strip().removesuffix("Neural"))


class _CircuitBreaker:
    """简单熔断器：连续失败 threshold 次后 open，cooldown 后半开探测。"""

    def __init__(self, name: str, threshold: int = 3, cooldown_s: float = 30.0):
        self.name = name
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.cooldown_s:
            # half-open: allow one probe
            self._opened_at = None
            self._failures = 0
            logger.info("TTS circuit %s half-open (probe)", self.name)
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = time.monotonic()
            logger.warning("TTS circuit %s OPEN after %d failures", self.name, self._failures)


class TTSManager:
    def __init__(self, settings: Settings, bark=None, ha=None):
        self.s = settings
        self.edge = EdgeTTS(settings)
        self.kokoro = KokoroTTS(settings)
        self.nowvoice = NowVoiceTTS(settings)
        self.bark = bark
        self.ha = ha
        # playback queue per device
        self.queue = PlaybackQueue(ha)
        # circuit breakers per engine
        self._breakers: dict[str, _CircuitBreaker] = {
            "edge-tts": _CircuitBreaker("edge-tts"),
            "nowvoice": _CircuitBreaker("nowvoice"),
            "kokoro": _CircuitBreaker("kokoro"),
        }

    def _voice_for(self, member: str, voice: str | None, backend: str | None = None,
                   nowvoice_voice: str | None = None) -> str:
        backend = backend or self.s.tts_primary
        if backend == "nowvoice":
            # 优先用角色绑定的 nowvoice voice ID（显式 8 位号优先，原语义不动）
            if _is_hex8(nowvoice_voice):
                return nowvoice_voice
            if _is_hex8(voice):
                return voice
            # 件四甲（DCD 20261008-DB五件）：落全局默认【之前】，先拿角色档嗓子名反查对照表
            for cand in (nowvoice_voice, voice):
                hexed = _voice_name_to_hex(cand)
                if hexed:
                    return hexed
            if nowvoice_voice or voice:
                logger.error(
                    "角色档嗓子 %r/nowvoice=%r 既非 8 位号也不在 SAMPLE_VOICES 对照表 ⇒ "
                    "落全局默认 %s（件四甲：记账，不静默）",
                    voice, nowvoice_voice, self.s.nowvoice_voice,
                )
            return self.s.nowvoice_voice
        if voice:
            return voice
        if backend == "kokoro":
            return self.s.tts_kokoro_voice
        return self.s.tts_edge_voice

    def _cache_hit(self, engine: str, text: str, voice: str, speed: float) -> TTSResult | None:
        if not self.s.tts_cache_enabled:
            return None
        filename = cache_filename(text, voice, engine, speed)
        path = Path(self.s.tts_dir) / filename
        if path.exists() and path.stat().st_size > 5000:
            return TTSResult(
                filename=filename,
                public_url=f"{self.s.base_url.rstrip('/')}/tts/{filename}",
                provider=engine,
                voice=voice,
                duration_ms=0,
                cached=True,
            )
        return None

    async def _try_engine(self, engine: str, text: str, voice: str, speed: float) -> TTSResult:
        """Try one engine, record circuit breaker state."""
        cb = self._breakers.get(engine)
        if cb and cb.is_open:
            raise RuntimeError(f"circuit open: {engine}")
        try:
            if engine == "edge-tts":
                result = await self.edge.synthesize(text, voice, speed)
            elif engine == "nowvoice":
                result = await self.nowvoice.synthesize(text, voice, speed)
            else:
                result = await self.kokoro.synthesize(text, voice, speed)
            if cb:
                cb.record_success()
            return result
        except Exception:
            if cb:
                cb.record_failure()
            raise

    async def synthesize(
        self, text: str, *, member: str = "", voice: str | None = None,
        backend: str | None = None, speed: float | None = None,
        allow_bark_fallback: bool = True, nowvoice_voice: str | None = None,
    ) -> TTSResult | None:
        backend = backend or self.s.tts_primary
        raw_voice = voice  # 角色档原始嗓子名/号：备引擎兜底也带上（件四甲），⛔ 用 _voice_for 之后的值
        voice = self._voice_for(member, voice, backend, nowvoice_voice)
        # 音色与引擎自动对齐：zm_ 前缀是 kokoro 音色
        if voice and voice.startswith("zm_") and backend == "edge-tts":
            logger.info("TTS auto-switch edge->kokoro (voice=%s)", voice)
            backend = "kokoro"
        speed = speed if speed is not None else self.s.tts_speed

        cached = self._cache_hit(backend, text, voice, speed)
        if cached is not None:
            return cached

        # 主引擎
        try:
            return await self._try_engine(backend, text, voice, speed)
        except Exception as e:
            logger.warning("TTS primary(%s) failed: %s", backend, e)

        # 备引擎：按优先级试其余两个
        fallbacks = [e for e in ("nowvoice", "edge-tts", "kokoro") if e != backend]
        for fb in fallbacks:
            try:
                if fb == "kokoro":
                    avail = await self.kokoro.is_available()
                    if not avail:
                        continue
                    fb_voice = self.s.tts_kokoro_voice
                elif fb == "nowvoice":
                    # 件四甲：兜底带角色嗓（nowvoice 槽优先，其次角色名反查），⛔ 直接落全局小晨
                    fb_voice = ((nowvoice_voice if _is_hex8(nowvoice_voice)
                                 else _voice_name_to_hex(nowvoice_voice))
                                or (raw_voice if _is_hex8(raw_voice)
                                    else _voice_name_to_hex(raw_voice))
                                or self.s.nowvoice_voice)
                else:
                    # 件四甲：角色档写着 edge 嗓名（…Neural）就带上，⛔ 直接落全局晓晓
                    fb_voice = raw_voice if raw_voice and raw_voice.endswith("Neural") else self.s.tts_edge_voice
                logger.info("TTS fallback -> %s", fb)
                result = await self._try_engine(fb, text, fb_voice, speed)
                return result
            except Exception as e2:
                logger.warning("TTS fallback(%s) failed: %s", fb, e2)

        # 兜底：Bark 纯文字
        if self.bark is not None and allow_bark_fallback:
            try:
                await self.bark.push(text, title=f"豆包管家 · {member}" if member else "豆包管家")
            except Exception as e3:
                logger.warning("Bark fallback failed: %s", e3)
        return None

    async def speak(
        self, text: str, *, voice: str | None = None,
        device_id: str | None = None, member: str = "",
        backend: str | None = None, speed: float | None = None,
        nowvoice_voice: str | None = None,
        trace_id: str = "", via: str = "direct",
        priority: int | None = None,
    ) -> TTSResult | None:
        """合成并播放到指定设备。

        格14（R1 2026-09-30）：全家的嘴只有这一张。``trace_id`` / ``via`` 只进日志，
        ⛔ 参与任何合成、选型、播放判断——它们的默认值就是改动前的行为。
        ``via`` 的取值口径：``"queue"``＝经 TTSQueue（tts/adapter.py 传下来，
        带 TTSItem.trace_id）；``"direct"``＝调用方绕过队列直接叫嘴（AST 尺在册的业务旁路：
        proactive/engine、timeseries/anomaly、morning/routine、notifier/router）。af_bridge ⛔ 不在册：批25 起它走 dialog.speak_as_role，那条腿同样不进 TTSQueue，过载语义归在途单 #59；名册尺＝tests/test_audit_1004_batch34_bypass_roster.py。
        队列那条腿跑在 loop.create_task 的后台 task 里，日志尾上由 contextvar 现取的
        ``trace=``（logging_setup.py）天生取不到请求那头的 id ⇒ 只能把 id 随消息本身带过来。

        ``priority``（DCD 裁定② 20261002）＝这条话的紧急档，取 tts/queue.py 的 1-5 口径；
        只有 PRIORITY_ALERT(=1) 能过「只放紧急」的模式门，⛔ priority＝不声明紧急⇒照表压。
        """
        allowed, mode = gate_tts(emergency=(priority == PRIORITY_ALERT))
        if not allowed:
            logger.info("TTS_SUPPRESSED mode=%s via=%s trace=%s priority=%s len=%d: %.30s",
                        mode, via, trace_id or "-",
                        priority if priority is not None else "-",
                        len(text or ""), text or "")
            return None
        logger.info("TTS_SPEAK via=%s trace=%s len=%d voice=%s backend=%s device=%s",
                    via, trace_id or "-", len(text or ""), voice or "-",
                    backend or "-", device_id or "-")
        res = await self.synthesize(
            text, member=member, voice=voice, backend=backend,
            speed=speed, nowvoice_voice=nowvoice_voice,
        )
        logger.info("TTS_ENGINE via=%s trace=%s engine=%s device=%s",
                    via, trace_id or "-", res.provider if res else "-", device_id or "-")
        if res is None:
            return None
        if device_id and self.ha is not None:
            try:
                await self.queue.enqueue(device_id, res, trace_id=trace_id or "")
                logger.info("tts.speak queued on %s: %.30s", device_id, text)
            except Exception as e:
                logger.warning("tts.speak play failed on %s: %s", device_id, e)
        return res

    async def test_voices(self, voices: list[str]) -> list[dict]:
        """诊断用：逐一对候选音色跑一次合成。"""
        results: list[dict] = []
        sample = "你好，我是豆包管家，今天天气不错。"
        for v in voices or []:
            v = (v or "").strip()
            if not v:
                continue
            try:
                res = await self.edge.synthesize(sample, v, self.s.tts_speed)
                size = 0
                try:
                    p = Path(self.s.tts_dir) / res.filename
                    size = p.stat().st_size if p.exists() else 0
                except Exception:
                    pass
                results.append({"voice": v, "ok": True, "provider": res.provider, "bytes": size})
            except Exception as e:
                results.append({"voice": v, "ok": False, "error": str(e)[:300]})
        return results

    def audio_path(self, filename: str) -> Path | None:
        p = Path(self.s.tts_dir) / Path(filename).name
        return p if p.exists() else None

    async def cleanup_old(self, max_age_days: int = 7) -> int:
        try:
            cutoff = time.time() - max_age_days * 86400
            count = 0
            d = Path(self.s.tts_dir)
            if d.exists():
                for f in d.glob("*.mp3"):
                    if f.name == "wake_ding.mp3":
                        continue  # R2-47: never delete wake prompt
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        count += 1
            if count:
                logger.info("tts cleanup removed %d files", count)
            return count
        except Exception as e:
            logger.warning("tts cleanup error: %s", e)
            return 0
