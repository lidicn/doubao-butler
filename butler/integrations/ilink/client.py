"""iLink Bot API 客户端（基于实际协议重写）。

参考：https://github.com/baiyingawa/wechat-ai-friend/blob/main/wechat_client.py
协议参考：https://kadaliao.github.io/posts/wechat-clawbot-protocol/

关键差异（与第一版的错误实现对比）：
- 认证：HTTP Header Authorization: Bearer {bot_token} + AuthorizationType: ilink_bot_token
- getupdates body: {"get_updates_buf", "base_info": {"channel_version": "1.0.2"}}
- 返回消息字段: data["msgs"]（不是 messages）
- 消息字段: from_user_id, item_list[].text_item.text, context_token, message_type(1=USER,2=BOT)
- sendmessage: 复杂嵌套结构，需要 context_token
- get_qrcode: POST + body {"local_token_list": []}
"""
from __future__ import annotations

import base64
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from butler.logging_setup import get_logger

logger = get_logger("butler.ilink.client")

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.2"
BOT_TYPE = 3


def _random_uin() -> str:
    """生成 X-WECHAT-UIN: base64(random uint32 decimal string)."""
    n = secrets.randbits(32)
    return base64.b64encode(str(n).encode()).decode()


class ILinkClient:
    """iLink Bot API 客户端。"""

    def __init__(self, session_dir: str = "/app/data/ilink"):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.session_dir / "session.json"
        self._session: dict = {}
        self._load_session()
        self._http = httpx.AsyncClient(timeout=40.0)
        # context_token 缓存（按用户）
        self._context_tokens: dict[str, str] = {}

    def _load_session(self) -> None:
        if self.session_file.exists():
            try:
                self._session = json.loads(self.session_file.read_text(encoding="utf-8"))
                logger.info("ilink session loaded: bot_id=%s", self._session.get("ilink_bot_id", "unknown"))
            except Exception as e:
                logger.warning("ilink session load failed: %s", e)
                self._session = {}

    def _save_session(self) -> None:
        try:
            self.session_file.write_text(
                json.dumps(self._session, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("ilink session save failed: %s", e)

    @property
    def base_url(self) -> str:
        return self._session.get("baseurl", ILINK_BASE_URL)

    @property
    def is_logged_in(self) -> bool:
        return bool(self._session.get("bot_token") and self._session.get("ilink_bot_id"))

    @property
    def bot_id(self) -> str:
        return self._session.get("ilink_bot_id", "")

    @property
    def user_id(self) -> str:
        return self._session.get("ilink_user_id", "")

    @property
    def token(self) -> str:
        return self._session.get("bot_token", "")

    def _auth_headers(self) -> dict:
        """认证头（用于 getupdates/sendmessage/sendtyping/getconfig）。"""
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.token}",
            "X-WECHAT-UIN": _random_uin(),
        }

    def _base_info(self) -> dict:
        return {"channel_version": CHANNEL_VERSION}

    async def get_qrcode(self) -> dict:
        """获取登录二维码。

        实际 API: POST /ilink/bot/get_bot_qrcode?bot_type=3
        Body: {"local_token_list": []}
        """
        try:
            url = f"{ILINK_BASE_URL}/ilink/bot/get_bot_qrcode"
            r = await self._http.post(
                url,
                params={"bot_type": BOT_TYPE},
                json={"local_token_list": []},
                timeout=15.0,
            )
            data = r.json()
            qrcode = data.get("qrcode", "")
            qrcode_img = data.get("qrcode_img_content", "")
            if qrcode and qrcode_img:
                logger.info("ilink qrcode obtained")
                return {
                    "ok": True,
                    "qrcode": qrcode,
                    "qrcode_img_content": qrcode_img,
                    "expire_at": int(time.time()) + 300,
                }
            return {"ok": False, "error": str(data)}
        except Exception as e:
            logger.error("ilink get_qrcode failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def check_qrcode_status(self, qrcode: str) -> dict:
        """长轮询扫码状态。

        实际 API: GET /ilink/bot/get_qrcode_status?qrcode=xxx
        状态值: wait, scaned, confirmed, expired, canceled, rejected
        """
        try:
            url = f"{ILINK_BASE_URL}/ilink/bot/get_qrcode_status"
            r = await self._http.get(url, params={"qrcode": qrcode}, timeout=40.0)
            data = r.json()
            status = data.get("status", "waiting")

            if status == "confirmed":
                self._session = {
                    "bot_token": data.get("bot_token", ""),
                    "ilink_bot_id": data.get("ilink_bot_id", ""),
                    "baseurl": data.get("baseurl", ILINK_BASE_URL),
                    "ilink_user_id": data.get("ilink_user_id", ""),
                    "logged_in_at": time.time(),
                }
                self._save_session()
                logger.info("ilink login confirmed: bot_id=%s user_id=%s", self.bot_id, self.user_id)
                return {
                    "ok": True,
                    "status": "confirmed",
                    "bot_id": self.bot_id,
                    "user_id": self.user_id,
                }

            return {"ok": True, "status": status}
        except httpx.TimeoutException:
            return {"ok": True, "status": "wait"}
        except Exception as e:
            logger.error("ilink check_qrcode_status failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def get_updates(self, get_updates_buf: str = "", timeout: int = 35) -> dict:
        """长轮询获取消息。

        实际 API: POST /ilink/bot/getupdates
        Body: {"get_updates_buf": "...", "base_info": {"channel_version": "1.0.2"}}
        返回: {"msgs": [...], "get_updates_buf": "...", "longpolling_timeout_ms": ...}

        消息格式:
        - from_user_id: 发送者 ID
        - message_type: 1=USER, 2=BOT
        - context_token: 回复时需要的上下文 token
        - item_list: [{type: 1(TEXT), text_item: {text: "..."}}]
        """
        if not self.is_logged_in:
            return {"ok": False, "error": "not logged in", "messages": [], "next_buf": get_updates_buf}

        try:
            url = f"{self.base_url}/ilink/bot/getupdates"
            body = {
                "get_updates_buf": get_updates_buf,
                "base_info": self._base_info(),
            }
            r = await self._http.post(url, headers=self._auth_headers(), json=body, timeout=timeout + 5.0)
            data = r.json()

            # session 过期
            if data.get("errcode") == -14:
                logger.warning("ilink session expired (errcode -14), need re-login")
                self._session = {}
                self._save_session()
                return {"ok": False, "error": "session expired", "messages": [], "next_buf": get_updates_buf, "session_expired": True}

            # 更新 buf
            next_buf = data.get("get_updates_buf", get_updates_buf)
            msgs = data.get("msgs", [])

            # 缓存 context_token
            for msg in msgs:
                from_user = msg.get("from_user_id", "")
                ctx_token = msg.get("context_token", "")
                if from_user and ctx_token:
                    self._context_tokens[from_user] = ctx_token

            return {"ok": True, "messages": msgs, "next_buf": next_buf}
        except httpx.TimeoutException:
            # 长轮询超时是正常的（无消息）
            return {"ok": True, "messages": [], "next_buf": get_updates_buf}
        except Exception as e:
            logger.debug("ilink get_updates error: %s", e)
            return {"ok": False, "error": str(e), "messages": [], "next_buf": get_updates_buf}

    async def send_message(self, to_user: str, text: str, context_token: str = "") -> dict:
        """发送微信消息给指定用户。

        实际 API: POST /ilink/bot/sendmessage
        需要 context_token（从收到的消息中获取，或从缓存中取）

        Body 结构:
        {"msg": {
            "to_user_id": "...",
            "client_id": "uuid",
            "message_type": 2,  # BOT
            "message_state": 2,  # FINISH
            "context_token": "...",
            "item_list": [{"type": 1, "text_item": {"text": "..."}}]
        }, "base_info": {"channel_version": "1.0.2"}}
        """
        if not self.is_logged_in:
            return {"ok": False, "error": "not logged in"}

        # 优先用传入的 context_token，否则用缓存的
        ctx = context_token or self._context_tokens.get(to_user, "")
        if not ctx:
            logger.warning("ilink send_message: no context_token for %s", to_user)
            return {"ok": False, "error": "no context_token (user must send a message first)"}

        try:
            client_id = str(uuid.uuid4()).replace("-", "")
            url = f"{self.base_url}/ilink/bot/sendmessage"
            body = {
                "msg": {
                    "to_user_id": to_user,
                    "client_id": client_id,
                    "message_type": 2,  # BOT
                    "message_state": 2,  # FINISH
                    "context_token": ctx,
                    "item_list": [
                        {"type": 1, "text_item": {"text": text}}
                    ],
                },
                "base_info": self._base_info(),
            }
            r = await self._http.post(url, headers=self._auth_headers(), json=body, timeout=15.0)
            data = r.json()
            if data.get("errcode", 0) == 0 or data.get("code", 0) == 0:
                logger.info("ilink message sent: to=%s", to_user)
                return {"ok": True, "message_id": client_id}
            return {"ok": False, "error": data.get("errmsg", str(data))}
        except Exception as e:
            logger.error("ilink send_message failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def send_typing(self, to_user: str, context_token: str = "") -> dict:
        """发送"正在输入"状态。

        需要先 getconfig 获取 typing_ticket，再 sendtyping。
        """
        if not self.is_logged_in:
            return {"ok": False, "error": "not logged in"}

        ctx = context_token or self._context_tokens.get(to_user, "")
        if not ctx:
            return {"ok": False, "error": "no context_token"}

        try:
            # Step 1: getconfig 获取 typing_ticket
            config_url = f"{self.base_url}/ilink/bot/getconfig"
            config_body = {
                "ilink_user_id": self.user_id,
                "context_token": ctx,
                "base_info": self._base_info(),
            }
            r1 = await self._http.post(config_url, headers=self._auth_headers(), json=config_body, timeout=10.0)
            typing_ticket = r1.json().get("typing_ticket", "")
            if not typing_ticket:
                return {"ok": False, "error": "no typing_ticket"}

            # Step 2: sendtyping
            typing_url = f"{self.base_url}/ilink/bot/sendtyping"
            typing_body = {
                "ilink_user_id": to_user,
                "typing_ticket": typing_ticket,
                "status": 1,  # 1=typing, 2=stop
                "base_info": self._base_info(),
            }
            await self._http.post(typing_url, headers=self._auth_headers(), json=typing_body, timeout=10.0)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def extract_text(self, msg: dict) -> str:
        """从消息中提取文本内容。"""
        item_list = msg.get("item_list", [])
        parts = []
        for item in item_list:
            if item.get("type") == 1:  # TEXT
                text = item.get("text_item", {}).get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts)

    def extract_from_user(self, msg: dict) -> str:
        return msg.get("from_user_id", "")

    def is_user_message(self, msg: dict) -> bool:
        """判断是否是用户发来的消息（不是 BOT 自己的回声）。"""
        return msg.get("message_type") == 1  # USER

    def logout(self) -> None:
        self._session = {}
        self._context_tokens.clear()
        if self.session_file.exists():
            self.session_file.unlink()
        logger.info("ilink logged out")

    async def aclose(self) -> None:
        await self._http.aclose()
