"""百度智能云 TTS 语音合成。

API: https://tsn.baidu.com/text2audio
音色: per=5118 (度小晴，精品女声，接近真人)
"""
from __future__ import annotations

import time
from typing import Optional

import aiohttp

from butler.logging_setup import get_logger

logger = get_logger("butler.core.tts_baidu")

_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
_TTS_URL = "https://tsn.baidu.com/text2audio"


class BaiduTTS:
    def __init__(self, api_key: str, secret_key: str):
        self._api_key = api_key
        self._secret_key = secret_key
        self._token: Optional[str] = None
        self._token_expire: float = 0

    async def _get_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expire - 3600:
            return self._token
        async with aiohttp.ClientSession() as session:
            url = f"{_TOKEN_URL}?grant_type=client_credentials&client_id={self._api_key}&client_secret={self._secret_key}"
            async with session.get(url) as r:
                data = await r.json()
                self._token = data["access_token"]
                self._token_expire = now + data.get("expires_in", 2592000)
                logger.info("baidu tts token refreshed")
                return self._token

    async def synthesize(self, text: str, per: int = 5003, spd: int = 5, pit: int = 5, vol: int = 5) -> Optional[bytes]:
        """合成语音，返回 MP3 二进制。失败返回 None。"""
        token = await self._get_token()
        params = {
            "tex": text[:2048],
            "tok": token,
            "cuid": "doubao-butler",
            "ctp": 1,
            "lan": "zh",
            "spd": spd,
            "pit": pit,
            "vol": vol,
            "per": per,
            "aue": 3,  # MP3
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(_TTS_URL, data=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    content_type = r.headers.get("Content-Type", "")
                    if "audio" in content_type:
                        return await r.read()
                    data = await r.json()
                    logger.error("baidu tts error: %s", data)
                    return None
        except Exception as e:
            logger.error("baidu tts request: %s", e)
            return None
