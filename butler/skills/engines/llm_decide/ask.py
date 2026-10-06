"""Pending ask：llm_decide 问完问题后挂起，等用户语音回复。

用法：
  ask = PendingAsk(room="客厅", question="要不要开电视？",
                   brain=brain, rt=rt, ctx=ctx)
  ask.arm(timeout=30)  # 注册，30秒内等回复

在 dialog.on_wakeup 里：
  pending = pending_ask_manager.check(room)
  if pending:
      answer = pending.resolve(message)  # 用户回复
      result = await pending.run_followup(answer)  # LLM第二轮
      return result
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homesdk.consent import NO, UNKNOWN, classify_answer  # WO-BUT-003 B1

logger = logging.getLogger("butler.skills.ask")

# 批24（表行 8 RUF006 那族）：loop 对被调度任务只持弱引用⇒fire-and-forget 的 task 必须存进
# 这张强引用注册表，跑完由 done_callback 自己摘掉（同批18 dialog._idle_tasks／批21 cron_task）。
_BG_TASKS: set = set()


@dataclass
class PendingAsk:
    room: str
    question: str
    brain: dict
    rt: Any = None
    created_at: float = field(default_factory=time.time)
    timeout: float = 30.0
    resolved: bool = False
    followup_text: str = ""


class PendingAskManager:
    def __init__(self) -> None:
        self._pending: list[PendingAsk] = []
        self._lock = asyncio.Lock()

    async def arm(self, room: str, question: str, brain: dict, rt: Any, timeout: float = 30.0) -> None:
        ask = PendingAsk(room=room, question=question, brain=brain, rt=rt, timeout=timeout)
        async with self._lock:
            self._pending.append(ask)
        logger.info("pending ask armed: room=%s q=%.30s timeout=%.0fs", room, question, timeout)

        # TVPilot: 播提示音 + 进入免唤醒聆听模式
        # 客厅场景都走 TV（room 可能不准，直接调）
        try:
            import httpx
            tv_base = "http://192.168.2.200:8090"
            # 提示音
            async with httpx.AsyncClient(timeout=5) as hc:
                await hc.post(f"{tv_base}/api/ask/beep")
                logger.info("TV ask beep sent")
            # 进入聆听模式（免唤醒）
            async with httpx.AsyncClient(timeout=5) as hc:
                await hc.post(f"{tv_base}/api/ask/listen",
                              json={"duration": int(timeout), "room": room})
                logger.info("TV ask listen started (%.0fs)", timeout)
        except Exception as e:
            logger.warning("TV ask beep/listen failed: %s", e)

        # 小爱音箱主动问询（完整 NR 版流程）：
        # 1. 唤醒小爱 2. 等2秒 3. play_text 播报问句 4. 小爱自动聆听 5. conversation sensor 捕获回答
        XIAOMI_ROOM_MAP = {
            "书房": "xiaomi_x08a_1648",
            "主卧室": "xiaomi_l17a_60b3",
            "客厅": "xiaomi_lx06_a137",
            "客厅右": "xiaomi_lx06_7709",
            "Kevin房间": "xiaomi_l06a_42e5",
            "Emily房间": "xiaomi_l06a_2dee",
            "主卧室浴室": "xiaomi_s12_10ca",
            "卫生间": "xiaomi_s12_b693",
        }
        core = XIAOMI_ROOM_MAP.get(room)
        if core:
            wake_entity = f"button.{core}_wake_up"
            play_entity = f"text.{core}_play_text"
            conv_entity = f"sensor.{core}_conversation"
            try:
                ha_api = getattr(rt, "ha", None)
                if ha_api is None:
                    raise RuntimeError("runtime ha client unavailable")
                import asyncio

                async def _xiaomi_ask_flow():
                    try:
                        # 1. 唤醒小爱
                        await ha_api.call_service("button", "press", {"entity_id": wake_entity})
                        logger.info("xiaomi ask: wake %s", wake_entity)
                        # 2. 等小爱就绪
                        await asyncio.sleep(2.0)
                        # 3. play_text 播报问句（小爱爱自己播报，播完自动聆听）
                        await ha_api.call_service("text", "set_value", {
                            "entity_id": play_entity, "value": question
                        })
                        logger.info("xiaomi ask: play_text %s -> %.30s", play_entity, question)
                    except Exception as e:
                        logger.warning("xiaomi ask flow failed: %s", e)

                task = asyncio.create_task(_xiaomi_ask_flow())
                _BG_TASKS.add(task)
                task.add_done_callback(_BG_TASKS.discard)
            except Exception as e:
                logger.warning("xiaomi ask setup failed: %s", e)

    def check(self, room: str) -> PendingAsk | None:
        """获取该房间最早未解决的 ask，同时清理超时项。"""
        now = time.time()
        # 清理超时
        expired = [a for a in self._pending if not a.resolved and (now - a.created_at) >= a.timeout]
        if expired:
            self._pending = [a for a in self._pending if not a.resolved and (now - a.created_at) < a.timeout]
            # 超时后停止 TV 聆听（fire-and-forget）
            try:
                import asyncio
                # _tv_ask_stop removed (was undefined NameError)
            except Exception:
                pass
        # 找同房间最早的
        candidates = [a for a in self._pending if a.room == room and not a.resolved]
        if not candidates:
            # room 不匹配时，fallback 到任意 pending ask（手动触发 room 可能不准）
            candidates = [a for a in self._pending if not a.resolved]
            if not candidates:
                return None
            logger.info("ask room mismatch: armed=%s incoming=%s, fallback to any", candidates[0].room, room)
        return min(candidates, key=lambda a: a.created_at)

    async def resolve_and_run(self, ask: PendingAsk, user_answer: str) -> str:
        """用户回复了 → 调 LLM 第二轮 → 返回要TTS的文本。"""
        ask.resolved = True
        self._pending.remove(ask)
        logger.info("pending ask resolved: q=%.30s answer=%.30s", ask.question, user_answer)

        brain = ask.brain
        rt = ask.rt
        llm = getattr(rt, "llm", None)
        agent = getattr(rt, "agent", None)
        if llm is None:
            return ""

        from butler.core.tools import TOOL_SCHEMAS, dispatch_tool

        whitelist = set(brain.get("tools_whitelist") or [])
        # 第二轮可以放宽：如果问的是"要不要开电视"，允许执行工具
        # 但仍限制在白名单内
        filtered_schemas = [
            s for s in TOOL_SCHEMAS
            if s.get("function", {}).get("name") in whitelist
        ]

        async def executor(name: str, args: dict) -> str:
            if name not in whitelist:
                return f"工具 {name} 不在白名单中，不可用。"
            try:
                result = await dispatch_tool(name, args, agent)
                logger.info("ask followup tool_call: %s(%s) -> %.80s", name, args, result)
                return str(result)
            except Exception as e:
                return f"工具调用失败: {e}"

        system = brain.get("system") or "你是家庭管家。"
        # 第二轮：告诉LLM用户回复了什么
        prompt = (
            f"【场景】你是家庭管家。你刚问主人：「{ask.question}」\n"
            f"【主人回答】{user_answer}\n"
            f"【你的任务】\n"
            f"如果主人说的是肯定的（好/可以/行/要/打开吧），立刻调用对应工具执行，然后简短确认。\n"
            f"如果主人说的是否定的（不用/不要/算了），不要调任何工具，简短说句好的就行。\n"
            f"不要反问，不要重复之前的话，直接行动。"
        )

        # 从 brain 配置取 ask_yes_action，明确告诉 LLM 同意时该调什么工具
        yes_action = brain.get("ask_yes_action") or {}
        yes_tool = yes_action.get("tool", "")
        yes_args = yes_action.get("args", {})
        yes_confirm = yes_action.get("confirm_text", "好的")

        system2 = "你是家庭管家豆包管家。主人刚回答了你的问题。允许调用工具。"

        # 构造明确的 followup prompt
        if yes_tool:
            prompt = (
                f"你刚问主人：「{ask.question}」\n"
                f"主人回答：{user_answer}\n"
                f"如果主人是肯定的（好/可以/行/要），调用 {yes_tool} 工具，参数：{yes_args}。"
                f"然后说：{yes_confirm}\n"
                f"如果是否定的（不用/不要/算了），不要调任何工具，简短说句好的。"
            )
        else:
            prompt = (
                f"你刚问主人：「{ask.question}」\n"
                f"主人回答：{user_answer}\n"
                f"简短回应（不超过30字）。"
            )

        # WO-BUT-001 Phase2：先用 homesdk.consent 单点判定，不放行就不给 LLM 调工具的机会
        verdict = classify_answer(user_answer)
        if verdict == NO:
            logger.info("ask followup consent=NO, skip LLM/tools")
            return "好的。"
        if verdict == UNKNOWN:
            logger.info("ask followup consent=UNKNOWN, re-ask")
            return f"没听清。{ask.question}"
        # YES：才进入可调工具的 LLM 第二轮

        messages = [{"role": "user", "content": prompt}]
        try:
            text = await llm.chat_with_tools(
                system2, messages, filtered_schemas, executor,
                max_iter=3, time_budget=20.0, temperature=0.5, max_tokens=300,
            )
        except Exception as e:
            logger.warning("ask followup LLM failed: %s", e)
            return ""

        text = (text or "").strip()
        if len(text) > 60:
            text = text[:60]
        ask.followup_text = text
        return text


# 全局单例
pending_ask_manager = PendingAskManager()
