"""
NowVoice TTS API Wrapper
========================
Reverse-engineered from https://nowvoice.ai/

API Architecture:
- Base URL: https://api.nowvoice.ai
- Authentication: Anonymous guest token (JWT)
- Encryption: AES-256-GCM with shared key
- Endpoints:
  - POST /u/a - Get anonymous auth token
  - POST /u/t - Refresh/renew token
  - POST /tts/speech - Generate speech (returns audio file)
  - POST /tts/speech/verify - Verify voice is valid

Usage:
    from nowvoice_client import NowVoiceClient
    
    client = NowVoiceClient()
    client.login()
    result = client.generate_speech(
        text="Hello world",
        voice_id="7c05d1f7",  # Ava Multilingual (en-US)
        language_code="en-US"
    )
    print(f"Audio URL: {result.url}")
"""

import json
import os
import base64
import time
import httpx
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


API_BASE = "https://api.nowvoice.ai"
AES_KEY_STRING = "k5l6vIpD59MvGFR+gC5KDSH6NdVERt7hwKPsoHzP6Ko="
# The key is the raw UTF-8 bytes of the string (first 32 bytes for AES-256)
AES_KEY = AES_KEY_STRING.encode('utf-8')[:32]


# Sample voice IDs from the 408 available voices
SAMPLE_VOICES = {
    # English (US)
    "en-US-Ava": "7c05d1f7",
    "en-US-Andrew": "7251c0f9",
    "en-US-Jenny": "88f3533a",
    "en-US-Guy": "af00993d",
    "en-US-Aria": "7e4ce63a",
    "en-US-Davis": "b9a0ca12",
    "en-US-Jane": "309fca30",
    "en-US-Jason": "0e04f87e",
    
    # Chinese (Mandarin)
    "zh-CN-Xiaochen": "afeb4759",
    "zh-CN-Xiaoxiao": "ed76d459",
    "zh-CN-Yunxi": "55a0f21e",
    "zh-CN-Yunjian": "43b96be6",
    "zh-CN-Xiaoyi": "5bc21988",
    "zh-CN-Yunyang": "fc7f98b9",
    "zh-CN-Xiaomeng": "e2731b46",
    "zh-CN-Xiaomo": "5ed3ae20",
    "zh-CN-Xiaohan": "6f7ed9f0",
    "zh-CN-Yunze": "9acc37a6",
}


@dataclass
class Voice:
    """Voice object from the API"""
    id: str
    name: str
    local_name: str = ""
    locale: str = ""
    gender: str = ""
    language: str = ""
    accent: str = ""
    styles: List[Dict[str, Any]] = field(default_factory=list)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Voice':
        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            local_name=data.get('localName', ''),
            locale=data.get('locale', ''),
            gender=data.get('gender', ''),
            language=data.get('language', ''),
            accent=data.get('accent', ''),
            styles=data.get('styles', [])
        )


@dataclass
class TTSResult:
    """Result from TTS generation"""
    success: bool
    uuid: Optional[str] = None
    url: Optional[str] = None
    audio_content: Optional[bytes] = None
    filename: Optional[str] = None
    error_code: Optional[str] = None
    error_msg: Optional[str] = None
    
    @property
    def is_error(self) -> bool:
        return not self.success


class NowVoiceClient:
    """
    Client for the NowVoice TTS API.
    
    This client handles:
    - Anonymous authentication
    - Encrypted API communication (AES-256-GCM)
    - TTS request generation
    - Audio retrieval
    """
    
    def __init__(self):
        self.api_base = API_BASE
        self.aes_key = AES_KEY
        self.token: Optional[str] = None
        self.user_data: Optional[Dict[str, Any]] = None
        self.timeout = 60
        
    def encrypt_payload(self, data: Dict[str, Any]) -> bytes:
        """
        Encrypt payload using AES-256-GCM.
        
        Format: iv(12 bytes) + ciphertext(encrypted JSON) + tag(16 bytes)
        """
        iv = os.urandom(12)
        cipher = Cipher(algorithms.AES(self.aes_key), modes.GCM(iv))
        enc = cipher.encryptor()
        
        plaintext = json.dumps(data).encode('utf-8')
        ciphertext = enc.update(plaintext) + enc.finalize()
        tag = enc.tag  # 16 bytes
        
        return iv + ciphertext + tag
    
    def decrypt_payload(self, data: bytes) -> Dict[str, Any]:
        """
        Decrypt response using AES-256-GCM.
        """
        iv = data[:12]
        ct = data[12:]
        ciphertext = ct[:-16]
        tag = ct[-16:]
        
        cipher = Cipher(algorithms.AES(self.aes_key), modes.GCM(iv, tag))
        dec = cipher.decryptor()
        plaintext = dec.update(ciphertext) + dec.finalize()
        
        return json.loads(plaintext.decode('utf-8'))
    
    def _api_call(self, path: str, data: Dict[str, Any], timeout: int = 60) -> Any:
        """Make an encrypted API call to NowVoice"""
        encrypted_body = self.encrypt_payload(data)
        
        headers = {
            'Content-Type': 'application/octet-stream',
            'Accept': 'application/octet-stream',
            'Content-Locale': 'zh',
        }
        
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        
        resp = httpx.post(
            f"{self.api_base}{path}",
            content=encrypted_body,
            headers=headers,
            timeout=timeout
        )
        
        if resp.status_code != 200:
            return {'error': f'HTTP {resp.status_code}', 'status': resp.status_code}
        
        # Check if it's a file response
        if resp.headers.get('X-Response-Encoding') == 'identity':
            filename = resp.headers.get('X-File', 'output.mp3')
            return {
                'file': resp.content,
                'filename': filename,
                'content_type': resp.headers.get('Content-Type', 'audio/mpeg')
            }
        
        # JSON response
        return self.decrypt_payload(resp.content)
    
    def login(self) -> Dict[str, Any]:
        """
        Get an anonymous access token.
        
        Returns:
            dict with 'token' (JWT) and 'isGuest' (bool)
        """
        result = self._api_call('/u/a', {
            'referrer': 'https://nowvoice.ai/zh/',
            'locationHref': 'https://nowvoice.ai/zh/'
        })
        
        if result and 'token' in result:
            self.token = result['token']
            self.user_data = result
            return result
        
        return result
    
    def refresh_token(self, access_token: str = None) -> Dict[str, Any]:
        """
        Refresh/renew the authentication token.
        
        Args:
            access_token: Optional access token to use
            
        Returns:
            dict with new token and user data
        """
        data = {}
        if access_token:
            data['accessToken'] = access_token
        if self.token:
            data['token'] = self.token
        
        result = self._api_call('/u/t', data)
        
        if result and 'token' in result:
            self.token = result['token']
            self.user_data = result
            return result
        
        return result
    
    def verify_voice(self, voice_id: str, language_code: str = 'en-US') -> Dict[str, Any]:
        """
        Verify a voice ID is valid for TTS generation.
        
        Args:
            voice_id: The voice ID to verify (e.g., '7c05d1f7')
            language_code: Language code (e.g., 'en-US', 'zh-CN')
            
        Returns:
            dict with verification result (empty {} if valid)
        """
        return self._api_call('/tts/speech/verify', {
            'voiceId': voice_id,
            'languageCode': language_code
        })
    
    def generate_speech(
        self,
        text: str,
        voice_id: str,
        language_code: str = 'en-US',
        style: Optional[str] = None,
        rate: float = 0.0,
        volume: float = 0.0,
        pitch: float = 0.0,
        pronunciations: Optional[List[Dict[str, str]]] = None
    ) -> TTSResult:
        """
        Generate speech from text.
        
        Args:
            text: The text to convert to speech
            voice_id: Voice ID (e.g., '7c05d1f7' for Ava Multilingual)
            language_code: Language code (e.g., 'en-US', 'zh-CN')
            style: Voice style (optional)
            rate: Speech rate adjustment (optional)
            volume: Volume adjustment (optional)
            pitch: Pitch adjustment (optional)
            pronunciations: Custom pronunciations list (optional)
            
        Returns:
            TTSResult with audio data or error information
        """
        params = {}
        if style:
            params['style'] = style
        if rate != 0:
            params['rate'] = rate
        if volume != 0:
            params['volume'] = volume
        if pitch != 0:
            params['pitch'] = pitch
        if pronunciations:
            params['pronunciations'] = pronunciations
        
        request_data = {
            'text': text,
            'voiceId': voice_id,
        }
        if params:
            request_data['params'] = params
        
        result = self._api_call('/tts/speech', request_data)
        
        if isinstance(result, dict) and 'file' in result:
            return TTSResult(
                success=True,
                audio_content=result['file'],
                filename=result['filename']
            )
        
        if isinstance(result, dict):
            # Check for error
            if result.get('errorCode'):
                return TTSResult(
                    success=False,
                    error_code=result.get('errorCode'),
                    error_msg=result.get('msg')
                )
            
            # Check for successful response with URL
            return TTSResult(
                success=True,
                uuid=result.get('id'),
                url=result.get('url')
            )
        
        return TTSResult(
            success=False,
            error_msg=str(result)
        )
    
    def save_audio(self, result: TTSResult, filepath: str) -> bool:
        """
        Save generated audio to a file.
        
        Args:
            result: TTSResult from generate_speech()
            filepath: Path to save the audio file
            
        Returns:
            True if saved successfully
        """
        if not result.success or not result.audio_content:
            return False
        
        with open(filepath, 'wb') as f:
            f.write(result.audio_content)
        
        return True


# CLI interface
if __name__ == '__main__':
    import sys
    
    client = NowVoiceClient()
    
    # Login
    print("=== Logging in ===")
    login_result = client.login()
    print(f"Login: {json.dumps(login_result, indent=2, ensure_ascii=False)}")
    
    if not client.token:
        print("Failed to login!")
        sys.exit(1)
    
    print(f"\nToken: {client.token[:50]}...")
    
    # Demo TTS with English voice
    print("\n=== Generating speech (English) ===")
    result = client.generate_speech(
        text="Hello, world! This is a test of the NowVoice API.",
        voice_id="7c05d1f7",  # Ava Multilingual
        language_code="en-US"
    )
    
    print(f"Success: {result.success}")
    if result.url:
        print(f"Audio URL: {result.url}")
    if result.uuid:
        print(f"Audio UUID: {result.uuid}")
    
    # Demo TTS with Chinese voice
    print("\n=== Generating speech (Chinese) ===")
    result_zh = client.generate_speech(
        text="你好世界！这是NowVoice API的测试。",
        voice_id="afeb4759",  # Xiaochen zh-CN
        language_code="zh-CN"
    )
    
    print(f"Success: {result_zh.success}")
    if result_zh.url:
        print(f"Audio URL: {result_zh.url}")
    if result_zh.uuid:
        print(f"Audio UUID: {result_zh.uuid}")
