"""iLink 微信消息桥接（适配实际协议）。

消息流：
1. getUpdates 长轮询获取消息（msgs 数组）
2. 过滤：只处理 message_type=1 的用户消息
3. 提取 from_user_id + text（从 item_list[].text_item.text）
4. 缓存 context_token（用于回复）
5. 发送"正在输入"状态
6. 调用豆包管家 agent/LLM 处理
7. sendmessage 回复（需要 context_token）
8. 按用户维护对话上下文
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Optional

from butler.logging_setup import get_logger
from butler.integrations.ilink.client import ILinkClient

logger = get_logger("butler.ilink.bridge")

MAX_HISTORY_PER_USER = 20
PROCESS_TIMEOUT = 60


class ILinkBridge:
    """iLink 微信消息桥接器。"""

    def __init__(self, client: ILinkClient, runtime=None):
        self.client = client
        self.rt = runtime
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._get_updates_buf = ""

        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_PER_USER))
        self._context_dir = Path("/app/data/ilink/contexts")
        self._context_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        if self._running:
            logger.warning("ilink bridge already running")
            return
        if not self.client.is_logged_in:
            logger.warning("ilink bridge not started: not logged in")
            return
        self._running = True
        self._task = asyncio.create_task(self._message_loop())
        logger.info("ilink bridge started, bot_id=%s", self.client.bot_id)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("ilink bridge stopped")

    async def _message_loop(self) -> None:
        logger.info("ilink message loop started")
        consecutive_errors = 0

        while self._running:
            try:
                result = await self.client.get_updates(get_updates_buf=self._get_updates_buf, timeout=35)

                # session 过期，停止桥接
                if result.get("session_expired"):
                    logger.error("ilink session expired, stopping bridge")
                    self._running = False
                    break

                if not result.get("ok"):
                    consecutive_errors += 1
                    if consecutive_errors > 10:
                        logger.error("ilink get_updates failed 10 times, stopping bridge")
                        self._running = False
                        break
                    await asyncio.sleep(min(consecutive_errors * 2, 30))
                    continue

                consecutive_errors = 0
                messages = result.get("messages", [])
                self._get_updates_buf = result.get("next_buf", self._get_updates_buf)

                for msg in messages:
                    try:
                        await self._handle_message(msg)
                    except Exception as e:
                        logger.error("ilink message handle error: %s", e, exc_info=True)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("ilink message loop error: %s", e)
                consecutive_errors += 1
                await asyncio.sleep(min(consecutive_errors * 2, 30))

        logger.info("ilink message loop exited")

    async def _handle_message(self, msg: dict) -> None:
        """处理单条微信消息。"""
        # 只处理用户消息（message_type=1），忽略 BOT 回声
        if not self.client.is_user_message(msg):
            return

        from_user = self.client.extract_from_user(msg)
        content = self.client.extract_text(msg)
        context_token = msg.get("context_token", "")

        if not from_user or not content:
            logger.debug("ilink message skipped: from=%s content=%s", from_user, content)
            return

        logger.info("ilink message received: from=%s content=%s", from_user, content[:50])

        # 发送"正在输入"状态（best-effort）
        try:
            await self.client.send_typing(from_user, context_token)
        except Exception:
            pass

        # 调用豆包管家 agent 处理
        try:
            reply = await asyncio.wait_for(
                self._process_with_butler(from_user, content),
                timeout=PROCESS_TIMEOUT,
            )
        except asyncio.TimeoutError:
            reply = "处理超时，请稍后再试。"
        except Exception as e:
            logger.error("ilink butler process error: %s", e, exc_info=True)
            reply = f"处理出错：{str(e)[:100]}"

        # 发送回复（需要 context_token）
        if reply:
            max_len = 1800
            if len(reply) > max_len:
                parts = [reply[i:i+max_len] for i in range(0, len(reply), max_len)]
                for i, part in enumerate(parts):
                    await self.client.send_message(from_user, part, context_token)
                    if i < len(parts) - 1:
                        await asyncio.sleep(0.5)
            else:
                await self.client.send_message(from_user, reply, context_token)

        # 保存对话上下文
        self._save_context(from_user, content, reply)

    async def _process_with_butler(self, from_user: str, content: str) -> str:
        """调用豆包管家处理消息。先走 Koin 工具层（简单工具），没命中再走 LLM。"""
        rt = self.rt
        if rt is None:
            return "管家运行时未就绪。"

        # Koin 简单工具层：先尝试确定性工具匹配
        agent = getattr(rt, "agent", None)
        if agent and hasattr(agent, "_fast_route"):
            try:
                fast_reply = await agent._fast_route(content, source="wechat")
                if fast_reply:
                    logger.info("ilink fast_route hit: %s -> %s", content[:30], fast_reply[:30])
                    return fast_reply
            except Exception as e:
                logger.debug("ilink fast_route error: %s", e)

        # 方式 1：通过 agent.run() 完整对话流程
        if agent and hasattr(agent, "run"):
            try:
                history = self._load_context(from_user)
                result = await agent.run(
                    text=content,
                    history=history,
                    source="wechat",
                )
                return result or ""
            except Exception as e:
                logger.debug("ilink agent.run failed, fallback to llm: %s", e)

        # 方式 2：回退到 LLM 直接回复
        llm = getattr(rt, "llm", None)
        if llm:
            try:
                history = self._load_context(from_user)
                messages = list(history) + [{"role": "user", "content": content}]
                system = (
                    "你是豆包管家。用户通过微信与你对话。"
                    "你可以控制家里的设备（灯光/空调/电视等），直接执行不要犹豫。"
                    "回答简洁友好，不超过 500 字。"
                )
                text, _ = await llm.chat(system, messages, max_tokens=800, temperature=0.7)
                return (text or "").strip()
            except Exception as e:
                logger.error("ilink llm fallback failed: %s", e)
                return f"AI 处理失败：{str(e)[:80]}"

        return "管家 AI 未就绪。"

    def _load_context(self, from_user: str) -> list[dict]:
        ctx_file = self._context_dir / f"{from_user}.json"
        if ctx_file.exists():
            try:
                data = json.loads(ctx_file.read_text(encoding="utf-8"))
                return data.get("history", [])[-MAX_HISTORY_PER_USER:]
            except Exception:
                pass
        return []

    def _save_context(self, from_user: str, user_msg: str, reply: str) -> None:
        ctx_file = self._context_dir / f"{from_user}.json"
        history = self._load_context(from_user)
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": reply})
        history = history[-MAX_HISTORY_PER_USER:]
        try:
            ctx_file.write_text(
                json.dumps({"user": from_user, "history": history, "updated_at": time.time()},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("ilink context save failed: %s", e)

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "logged_in": self.client.is_logged_in,
            "bot_id": self.client.bot_id,
            "get_updates_buf": self._get_updates_buf[:20] + "..." if self._get_updates_buf else "",
            "active_users": len(self._history),
        }
