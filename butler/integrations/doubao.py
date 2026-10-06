"""doubao2api 多模态执行器（OpenAI 兼容）：视觉理解 / 纯文本对话 / 图片生成 / 音乐生成。

doubao2api 是网页版逆向服务，function calling 为服务端推断式合成
（DOUBAO_CODE_EXECUTOR=on 时可用，family_fallback 匹配中文意图），
可靠性低于 deepseek 原生 function calling，因此工具循环仍由 new-api 负责。
doubao2api 主要承担「出现在豆包app的对话展示层」：keep_conversation 持久对话线程。
图片/音乐的具体端点需与 doubao2api 实际暴露的路由对齐，失败时优雅降级。
"""
from __future__ import annotations

import hashlib
import json
import time

import httpx

from butler.config import Settings
from butler.logging_setup import get_logger

logger = get_logger("butler.doubao")


class DoubaoClient:
    def __init__(self, settings: Settings):
        self.s = settings
        # M4: 多模态分析结果缓存（同截图+同prompt 5分钟内不重复分析，省 token）
        self._vision_cache: dict[str, tuple[str, float]] = {}
        self._vision_cache_ttl = 300  # 5分钟

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.s.doubao_api_key}",
            "Content-Type": "application/json",
        }

    async def chat(self, text: str, keep_conversation: bool = False,
                   conversation_id: str | None = None,
                   system_prompt: str | None = None,
                   silent: bool = False) -> tuple[str, str | None]:
        """纯文本对话，返回 (回复文本, conversation_id)。

        keep_conversation=True 时分角色持久对话线程。
        silent=True 时不发 system_prompt（doubao2api 静默写入模式，避免暴露人设指令）。
        """
        messages = []
        if system_prompt and not silent:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": text})
        payload = {
            "model": self.s.doubao_model,
            "messages": messages,
            "stream": False,
        }
        if silent:
            payload["silent"] = True
        if keep_conversation:
            payload["keep_conversation"] = True
            if conversation_id:
                payload["conversation_id"] = conversation_id
        try:
            async with httpx.AsyncClient(timeout=60) as c:  # 视觉分析需要更长超时
                r = await c.post(self.s.doubao_api_url, headers=self._headers(), json=payload)
                r.raise_for_status()
                data = r.json()
                msg = data["choices"][0]["message"]
                text_out = (msg.get("content") or "").strip()
                conv_id = data.get("conversation_id") or None
                return text_out, conv_id
        except Exception as e:
            logger.warning("doubao chat failed: %s", e)
            return f"对话失败：{e}", None

    async def vision(self, prompt: str, image_url: str, keep_conversation: bool = False,
                     conversation_id: str | None = None) -> tuple[str, str | None]:
        """把一张图片 + 文字问题发给 doubao2api 视觉对话，返回 (文本, conversation_id)。

        使用 /v1/images/analyses 端点（chat/completions 对 image_url 返回 500）。
        keep_conversation=True 时分角色持久对话线程。

        M4 缓存：同截图+同prompt 5分钟内直接返回缓存结果，省 token。
        """
        # M4: 缓存检查（仅单次调用缓存，keep_conversation 不缓存因为有上下文）
        if not keep_conversation:
            cache_key = hashlib.md5(f"{prompt}:{image_url[:2000]}".encode()).hexdigest()
            cached = self._vision_cache.get(cache_key)
            if cached and time.time() - cached[1] < self._vision_cache_ttl:
                logger.debug("vision cache hit (key=%s...)", cache_key[:8])
                return cached[0], None
        else:
            cache_key = None

        payload = {
            "model": self.s.doubao_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "stream": False,
        }
        if keep_conversation:
            payload["keep_conversation"] = True
            if conversation_id:
                payload["conversation_id"] = conversation_id
        # 视觉识别走 /v1/images/analyses（chat/completions 对 image_url 返回 500）
        vision_url = self.s.doubao_api_url.replace("/chat/completions", "/images/analyses")
        try:
            async with httpx.AsyncClient(timeout=self.s.llm_timeout) as c:
                r = await c.post(vision_url, headers=self._headers(), json=payload)
                r.raise_for_status()
                data = r.json()
                text = data["choices"][0]["message"]["content"].strip()
                conv_id = data.get("conversation_id") or None
                # M4: 写入缓存（仅单次调用）
                if cache_key and not keep_conversation:
                    self._vision_cache[cache_key] = (text, time.time())
                    # 清理过期缓存（超过100条时清理）
                    if len(self._vision_cache) > 100:
                        now = time.time()
                        self._vision_cache = {k: v for k, v in self._vision_cache.items()
                                               if now - v[1] < self._vision_cache_ttl}
                return text, conv_id
        except Exception as e:
            logger.warning("doubao vision failed: %s", e)
            return f"图片识别失败：{e}", None

    async def generate_image(self, prompt: str) -> str:
        """根据文字生成图片，返回图片地址或错误信息。"""
        url = f"{self.s.doubao_base_url.rstrip('/')}/v1/images/generations"
        payload = {"model": "doubao-image", "prompt": prompt, "n": 1, "size": "1024x1024"}
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(url, headers=self._headers(), json=payload)
                r.raise_for_status()
                data = r.json()
            item = (data.get("data") or [{}])[0]
            return item.get("url") or item.get("b64_json") or str(data)[:500]
        except Exception as e:
            logger.warning("doubao image failed: %s", e)
            return f"图片生成失败：{e}（请确认 doubao2api 的图片端点）"

    async def generate_music(self, prompt: str) -> str:
        """根据文字创作音乐，返回音频地址或错误信息。"""
        url = f"{self.s.doubao_base_url.rstrip('/')}/v1/audio/speech"
        payload = {"model": "doubao-music", "input": prompt, "voice": "music"}
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(url, headers=self._headers(), json=payload)
                r.raise_for_status()
                data = r.json()
            return data.get("url") or str(data)[:500]
        except Exception as e:
            logger.warning("doubao music failed: %s", e)
            return f"音乐生成失败：{e}（请确认 doubao2api 的音乐端点）"
