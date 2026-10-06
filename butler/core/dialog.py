"""对话状态机（设计文档 §3.2.1）：IDLE → LISTENING → THINKING → SPEAKING → WAITING。

负责把感知事件转成「该不该说、说什么、怎么发出去」。
"""
from __future__ import annotations

import asyncio
import inspect
import time

from butler.bus.topics import PUB_DIALOG, PUB_SPEAK_OUT, parse_experiment
from butler.config import Settings
from butler.core.dedup import DedupChecker
from butler.core.persona import PersonaEngine
from butler.core.simple_rules import check_simple_command
from butler.core.state import DialogState, RuntimeState
from butler.integrations.bark import Bark
from butler.integrations.ha import HAClient
from butler.integrations.llm import LLMClient
from butler.integrations.memory_agent import MemoryAgentClient
from butler.integrations.tv import TVClient
from butler.logging_setup import get_logger
from homesdk.consent import YES, NO, UNKNOWN, classify_answer
from butler.store import repo
from butler.runtime import get_runtime
from butler.tts.manager import TTSManager

logger = get_logger("butler.dialog")

_idle_tasks: set[asyncio.Task] = set()   # 收尾任务强引用注册表（第五轮 P0-7 同形状）

_DEVICE_FAIL_REPLY = "嗯，刚才出了点问题，能再说一遍吗？"  # 简单规则设备控制没真成时的统一口径（⛔ 拿确认文案盖住失败）


def _default_butler_role():
    """rt.roles 缺失主角色时的兜底（仅提供 _emit_devices 需要的字段）。"""

    class _R:
        voice = "zh-CN-XiaoxiaoNeural"
        tts_backend = "edge-tts"

    return _R()


class DialogManager:
    def __init__(
        self,
        settings: Settings,
        state: RuntimeState,
        mqtt,
        llm: LLMClient,
        tts: TTSManager,
        tv: TVClient,
        ha: HAClient,
        bark: Bark,
        memory: MemoryAgentClient,
        persona: PersonaEngine,
        dedup: DedupChecker,
        wakeup,
        agent,
    ):
        self.s = settings
        self.state = state
        self.mqtt = mqtt
        self.llm = llm
        self.tts = tts
        self.tv = tv
        self.ha = ha
        self.bark = bark
        self.memory = memory
        self.persona = persona
        self.dedup = dedup
        self.wakeup = wakeup
        self.agent = agent
        self.trigger_engine = None          # v0.1: 由 app.py 注入，人脸等事件走 trigger 规则层
        self._role_history: dict[str, list] = {}   # 按角色隔离的多轮上下文
        self._echo_until: float = 0.0              # 小爱出声后的回声抑制窗口（时间戳）
        self._pending_skill_desc: dict[str, float] = {}   # WO-DB-101：存过期时间戳，TTL=120s

    # ---------- 事件分发 ----------

    async def on_event(self, topic: str, payload: dict) -> None:
        if topic.endswith("/face") or topic.endswith("/face/fused"):
            name = (payload.get("name") or payload.get("member") or "").strip()
            conf = float(payload.get("confidence") or 0.0)
            await self.on_face(name or "stranger", conf)
        elif topic.endswith("/status"):
            # TV 在线状态：离线时清理在场人员
            if str(payload.get("state") or payload.get("status")) == "offline":
                for n in list(self.state.present.keys()):
                    self.state.mark_absent(n)
        elif "/event/" in topic:
            kind = topic.split("/")[-1]
            member = str(payload.get("member") or payload.get("name") or "")
            if kind == "voice" and member:
                await self.on_user_utterance(member, str(payload.get("text") or ""))
            elif kind == "timer":
                await self._timed_proactive(payload)
            elif kind == "button":
                # v0.6: 按钮事件 → trigger 规则层 button_pressed
                if self.trigger_engine is not None:
                    await self.trigger_engine.handle_event("button_pressed", {
                        "button_id": str(payload.get("button_id") or payload.get("id") or ""),
                        "room": str(payload.get("room") or "客厅"),
                        "action": str(payload.get("action") or "click"),
                        "member": str(payload.get("member") or ""),
                    })
        elif topic.endswith("/experiment"):
            # TV 端小爱聆听结束实验数据（tv/livingroom/experiment）：遥控器语音按钮入口
            await self.on_tv_voice(parse_experiment(topic, payload))
        elif topic.endswith("/wake"):
            # Arcface 远场语音（tv/livingroom/wake）：唤醒词 + IAT 识别结果
            vtype = str(payload.get("type") or "")
            cmd = str(payload.get("command") or "")
            if vtype == "voice_command" and cmd:
                logger.info("TV wake command: %s", cmd[:50])
                await self.on_wakeup(None, "客厅", cmd, source="tv_wake", source_device="tv_living")

    # ---------- 人脸 → 主动问候 ----------

    async def on_face(self, name: str, confidence: float) -> None:
        """人脸事件 → 全局防打扰 → trigger 规则层匹配 → 执行动作。

        v0.1：原硬编码打招呼逻辑迁移到 trigger 规则（morning_greet → greet 技能）。
        wakeup.decide 保留为全局防打扰（muted/dnd/全局cooldown），trigger 引擎负责
        规则匹配（time_range/member/room）+ 按 trigger 粒度冷却 + 动作执行。
        """
        self.state.mark_present(name, confidence)
        allowed, reason = await self.wakeup.decide("face", member=name, room="客厅")
        if not allowed:
            logger.info("greeting %s skipped: %s", name, reason)
            return
        if self.trigger_engine is not None:
            await self.trigger_engine.handle_event("face_detected", {
                "member": name,
                "room": "客厅",
                "confidence": confidence,
            })
        else:
            # 回退：trigger 引擎未就绪时保持原行为
            greeting = self.persona.greeting(name)
            await self.speak(greeting, name, source="proactive")

    # ---------- 用户说话 → 思考 → 回复 ----------

    async def on_user_utterance(self, member: str, text: str) -> str:
        """NR 老入口（butler/event/voice）也按唤醒词路由到角色，复用 on_wakeup 的
        角色系统 + output_devices 分发；没匹配到角色时回退到 butler 默认行为。

        回声抑制：butler 刚在小爱上播报过，其自身声音会被小爱对话传感器再次识别，
        形成「自问自答」死循环。此处若在抑制窗口内，直接丢弃该次语音事件。
        """
        if time.time() < self._echo_until:
            logger.info("on_user_utterance dropped (echo suppress, %.1fs left)", self._echo_until - time.time())
            return ""
        if member:
            self.state.mark_present(member)
        # 房间：优先从成员档案取；取不到则空（后续不过滤房间，仍按 output_devices 出声）
        room = ""
        mc = self.s.member_by_name(member)
        if mc and mc.room:
            room = mc.room
        role, cleaned = self.match_role_by_text(text)
        # P2: 没说唤醒词时，按房间默认角色（书房→小理等）
        if role is None and room:
            dr = self.match_role_by_room(room)
            if dr is not None:
                role = dr
                cleaned = text
                logger.info("room default role: room=%s -> %s", room, dr.id)
        # AF ask 桥：如果有挂起的 ask 等回答，优先注回
        try:
            from butler import af_bridge
            if await af_bridge.on_user_reply(room, cleaned):
                logger.info('af_bridge: reply routed to pending ask, skip normal dialog')
                return ''
        except Exception as e:
            logger.debug("af_bridge reply routing skipped: %s", e)
        # v0.2: voice_wake trigger 检查——用户说的话匹配特定短语时，走 trigger 规则执行动作链
        if self.trigger_engine is not None:
            trig_res = await self.trigger_engine.handle_event("voice_wake", {
                "member": member,
                "room": room,
                "text": cleaned,
                "raw_text": text,
            })
            # trigger 匹配并执行成功则不走普通对话（trigger 动作链已包含发声）
            if trig_res and any(r.get("ok") for r in trig_res):
                logger.info("voice_wake trigger matched, skip normal dialog")
                return ""
        # 没匹配到任何角色时回退到 butler，保持旧入口「任意语音都能被管家听见」的兼容性
        role_id = role.id if role else "butler"
        res = await self.on_wakeup(role_id, room, cleaned, member)
        return res.get("reply", "")

    # ---------- 按角色唤醒路由（Phase 6）----------

    def match_role_by_text(self, text: str):
        """从文本中识别唤醒短语 → 角色（返回 role, 去唤醒词后的文本）。"""
        rt = get_runtime()
        for role in rt.roles.all():
            if not role.enabled or not role.wake_words:
                continue
            for w in role.wake_words:
                if w and w in text:
                    cleaned = text.replace(w, "", 1).strip() if w else text
                    return role, cleaned
        return None, text

    def match_role_by_room(self, room: str):
        """按房间推断默认角色：私人助理(scope=private)的 bound_rooms 即其专属房间。

        书房→小理、Kevin房间→小凯、Emily房间→小艾。
        客厅/厨房等无私人角色的房间返回 None（调用方回退 butler）。
        """
        if not room:
            return None
        rt = get_runtime()
        for role in rt.roles.all():
            if not role.enabled:
                continue
            if getattr(role, "scope", "") == "private" and role.bound_rooms and room in role.bound_rooms:
                return role
        return None

    async def _quick_reply(self, role, room: str, member: str, reply: str, source_device: str = "") -> dict:
        """快速回复：不经过 LLM，直接播报回复文本。"""
        if role is None:
            from butler.runtime import get_runtime
            role = get_runtime().roles.get("butler")
        spoken = await self._speak_transition(lambda: self.speak_as_role(role, reply, room, member or "家人", source_device=source_device))
        return {"ok": True, "role": role.id, "reply": reply, "spoken": spoken}

    async def on_wakeup(self, role_id: str | None, room: str, message: str, member: str = "", source_device: str = "", source: str = "active") -> dict:
        """统一唤醒入口：NR 耳朵检测到唤醒短语后 POST 至此。
        - role_id 可省略，由 message 自动识别；
        - 私人助理(scope=private)校验 bound_rooms，越界拒绝；
        - 用角色 system 跑对话，按角色隔离多轮上下文；
        - 经角色 output_devices 发声（尊重设备 play_mode）。
        """
        # 回声抑制：butler 刚在小爱上播报过，其自身声音会被小爱对话传感器再次识别
        if time.time() < self._echo_until:
            logger.info("on_wakeup dropped (echo suppress, %.1fs left)", self._echo_until - time.time())
            return {"ok": False, "error": "echo_suppress"}
        rt = get_runtime()

        # Ask 挂起：llm_decide 刚问完问题，用户语音回复走第二轮
        try:
            from butler.skills.engines.llm_decide.ask import pending_ask_manager
            pending = pending_ask_manager.check(room)
            if pending is not None:
                followup = await pending_ask_manager.resolve_and_run(pending, message)
                if followup:
                    role = rt.roles.get("butler") if rt.roles else None
                    if role:
                        await self.speak_as_role(role, followup, room, "家人", source_device=source_device)
                    return {"ok": True, "reply": followup, "ask_followup": True}
                return {"ok": True, "reply": "", "ask_followup": True}
        except Exception:
            logger.exception("pending ask handling failed")

        # 审计 T-01：确认分支里就要用 role，绑定必须早于分支（⛔ 靠分支之后的赋值）
        role = rt.roles.get(role_id) if role_id and rt.roles else None
        # 技能草稿确认流程：如果有待确认草稿，优先处理确认/修改/取消
        creator = getattr(rt, "skill_creator", None)
        if creator and role_id:
            pending = creator.get_pending(role_id)
            if pending:
                # WO-BUT-001：用 homesdk.consent 单点判定，物理删除内联词表
                verdict = classify_answer(message)
                skill_name = pending.get("name", "这个技能")
                if verdict == YES:
                    result = creator.confirm(role_id)
                    if result.get("ok"):
                        reply = f"技能「{result['skill']['name']}」已创建并生效。"
                    else:
                        reply = f"确认失败: {result.get('error')}"
                    return await self._quick_reply(role, room, member, reply, source_device)
                if verdict == NO:
                    result = creator.cancel(role_id)
                    reply = result.get("message", "已取消技能创建") if result.get("ok") else f"取消失败: {result.get('error')}"
                    return await self._quick_reply(role, room, member, reply, source_device)
                # WO-BUT-002①：UNKNOWN 重播上限 1 次，第二次仍 UNKNOWN → 走 NO
                pending["unknown_retry"] = pending.get("unknown_retry", 0) + 1
                if pending["unknown_retry"] >= 2:
                    logger.info("skill draft consent unknown x2, treating as NO")
                    result = creator.cancel(role_id)
                    reply = result.get("message", "已取消技能创建") if result.get("ok") else f"取消失败: {result.get('error')}"
                    return await self._quick_reply(role, room, member, reply, source_device)
                logger.info("skill draft consent unknown (retry %d): %s", pending["unknown_retry"], message[:50])
                return await self._quick_reply(
                    role, room, member,
                    f"没听清。要创建技能「{skill_name}」吗？回复确认或取消。",
                    source_device,
                )
        if not role_id:
            role, message = self.match_role_by_text(message)
            if role is None and room:
                dr = self.match_role_by_room(room)
                if dr is not None:
                    role = dr
                    logger.info("room default role(on_wakeup): room=%s -> %s", room, dr.id)
            role_id = role.id if role else "butler"  # 无唤醒词且无房间默认角色时回退 butler
        role = rt.roles.get(role_id) if role_id else None
        if not role:
            return {"ok": False, "error": "unknown_role", "role": role_id}
        if not role.enabled:
            return {"ok": False, "error": "role_disabled", "role": role.id}
        # 私人助理范围校验
        if role.scope == "private":
            if room and role.bound_rooms and room not in role.bound_rooms:
                logger.info("on_wakeup scope_private rejected: role=%s room=%s allowed=%s", role.id, room, role.bound_rooms)
                return {"ok": False, "error": "scope_private",
                        "message": f"{role.name} 只能在 {('、'.join(role.bound_rooms))} 唤醒"}
        if role.presence_rooms != ["*"] and room and role.presence_rooms and room not in role.presence_rooms:
            logger.info("role %s woken in room %s (outside presence_rooms)", role.id, room)
        member = member or role.member
        self.state.set_state(DialogState.THINKING)
        import uuid
        trace_id = uuid.uuid4().hex[:8]
        logger.info("on_wakeup: trace=%s message=%s role=%s", trace_id, message[:30], role.id)
        # v2.6: 唤醒即写入 trace 上下文，未走 agent 的分支（技能创建/简单规则）发声也能带上链路
        try:
            from butler.core.agent import _current_trace_id
            _current_trace_id.set(trace_id)
        except Exception:
            pass
        
        # WO-DB-101：pending 技能描述（TTL 120s，过期自动清除）
        _PENDING_TTL = 120.0  # 语音交互2分钟内有效，超时用户走开后不会误触发
        _pending_exp = self._pending_skill_desc.get(role_id)
        if _pending_exp:
            if time.time() > _pending_exp:
                # 过期：清除，走正常流程
                del self._pending_skill_desc[role_id]
                logger.info("skill create pending expired for role=%s", role_id)
            else:
                # 未过期：检查是否是取消词
                if any(kw in message for kw in ["取消", "算了", "不建了", "不弄了", "不要了", "退出"]):
                    del self._pending_skill_desc[role_id]
                    reply = "好的，已取消创建技能。"
                    logger.info("skill create pending cancelled by user: role=%s", role_id)
                    spoken = await self._speak_transition(lambda: self.speak_as_role(role, reply, room, member or "家人", source_device=source_device))
                    return {"ok": True, "role": role.id, "reply": reply, "spoken": spoken, "skill_create_cancel": True}
                # 未过期：检查是否又是"创建技能"触发词（缺陷2：连说两遍不喂生成器）
                from butler.core.simple_rules import _check_skill_create
                if _check_skill_create(message):
                    # 二次触发：重新返回短反问，不喂生成器
                    logger.info("skill create re-trigger while pending: role=%s, returning short reply", role_id)
                    reply = "你想创建什么技能？"
                    self._pending_skill_desc[role_id] = time.time() + _PENDING_TTL  # 刷新TTL
                    spoken = await self._speak_transition(lambda: self.speak_as_role(role, reply, room, member or "家人", source_device=source_device))
                    return {"ok": True, "role": role.id, "reply": reply, "spoken": spoken, "skill_create_retrigger": True}
                # 正常描述：消费 pending，喂生成器
                del self._pending_skill_desc[role_id]
                creator = getattr(rt, "skill_creator", None)
                if creator:
                    logger.info("skill create from description: role=%s desc=%s", role_id, message[:50])
                    gen_result = await creator.generate_skill_from_description(message, self.llm)
                    if gen_result.get("ok"):
                        draft_result = creator.create_draft(gen_result["skill"], role_id=role_id)
                        if draft_result.get("ok"):
                            preview = draft_result.get("preview", "")
                            reply = f"我生成了一个技能草稿：\n{preview}\n\n要创建吗？回复确认或取消。"
                        else:
                            reply = f"创建草稿失败: {draft_result.get('error')}"
                    else:
                        reply = f"生成技能失败: {gen_result.get('error', '未知错误')}"
                else:
                    reply = "技能生成器未就绪，请稍后再试。"
                spoken = await self._speak_transition(lambda: self.speak_as_role(role, reply, room, member or "家人", source_device=source_device))
                return {"ok": True, "role": role.id, "reply": reply, "spoken": spoken, "skill_create": True}

        # 最小 Token 原则：先检查是否是简单命令，是则直接执行，不走 LLM
        simple_result = await check_simple_command(message, rt)
        logger.info("on_wakeup: simple_result=%s", simple_result)
        if simple_result:
            reply, action = simple_result
            logger.info("simple command matched, skip LLM: %s", message[:30])
            # 设备控制两档：call_service＝认出了实体才发令；ask_device＝认不出，只回追问（P0-G 裁 A′）
            if action.get("action") in ("call_service", "ask_device"):
                # 小爱音箱来源：设备控制由小爱同学原生处理，butler 不重复 TTS（防双重播报胡言乱语）
                if source == "xiaoai":
                    logger.info("simple device control from xiaoai, skip TTS (handled natively): %s", message[:30])
                    self.state.set_state(DialogState.WAITING)
                    self._spawn_idle()
                    return {"ok": True, "role": role.id, "reply": "", "spoken": [], "simple_rule": True, "silent": True}
                # 认不出设备实体：只回追问，⛔ 调 HA、⛔ 报成功（P0-G 裁 A′）
                if action.get("action") == "ask_device":
                    logger.info("simple device control needs a room: %s hint=%s",
                                message[:30], action.get("device_hint", ""))
                else:
                    # 非小爱来源：真正调用 HA 服务
                    try:
                        _rt = get_runtime()
                        if _rt.ha:
                            ha_result = await _rt.ha.call_service(action["domain"], action["service"], action.get("data", {}))
                            if not str(ha_result).startswith("ok"):
                                logger.warning("simple device control rejected by HA: %s.%s -> %s",
                                               action["domain"], action["service"], ha_result)
                                reply = _DEVICE_FAIL_REPLY
                            else:
                                logger.info("simple device control executed: %s.%s %s", action["domain"], action["service"], action.get("data", {}))
                        else:
                            logger.warning("simple device control: HA not available, would call %s.%s", action["domain"], action["service"])
                            reply = _DEVICE_FAIL_REPLY
                    except Exception as e:
                        logger.warning("simple device control failed: %s", e)
                        reply = _DEVICE_FAIL_REPLY
            elif action.get("action") == "skill_create_intent":
                # WO-DB-101：设置待描述状态（存过期时间戳，TTL=120s）
                self._pending_skill_desc[role_id] = time.time() + 120.0
                logger.info("skill create intent: waiting for description from role=%s (TTL=120s)", role_id)
            spoken = await self._speak_transition(lambda: self.speak_as_role(role, reply, room, member or "家人", source_device=source_device))
            return {"ok": True, "role": role.id, "reply": reply, "spoken": spoken, "simple_rule": True}
        
        history = list(self._role_history.get(role.id, []))
        # v0.5: 持久化 user 消息（之前只存 butler 回复，决策层需要完整对话数据）
        await asyncio.to_thread(repo.add_turn, member or "家人", "user", message,
                                engine=role.id, source="voice")

        # v2.6: 记忆召回——对话前从 MA 拉相关记忆注入 system prompt
        sys_prompt = role.system or ""
        try:
            mems = await asyncio.wait_for(
                self.memory.retrieve(message, member=member or "", limit=3),
                timeout=3.0,
            )
            if mems:
                mem_block = "\n\n【相关记忆】\n" + "\n".join(f"- {m}" for m in mems)
                sys_prompt = sys_prompt + mem_block
                logger.info("memory recall: %d facts injected (role=%s)", len(mems), role.id)
        except Exception as e:
            logger.debug("memory recall skipped: %s", e)

        try:
            reply = await self.agent.run(message, member, history, system_override=sys_prompt,
                                       source=source, trace_id=trace_id)
        except Exception as e:
            logger.warning("wakeup agent run failed: %s", e)
            reply = f"嗯，刚才卡了一下（{type(e).__name__}），能再说一遍吗？"
        # 多轮上下文（按角色隔离，保留最近 12 轮）
        hist = history + [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        self._role_history[role.id] = hist[-12:]
        # v2.6: agent.run 会把上下文换成自己的 trace_id，唤醒与轨迹之间是一座双跳桥，两个 id 都记下来才好回放
        agent_trace = getattr(self.agent, "last_trace_id", "") or ""
        if agent_trace and agent_trace != trace_id:
            logger.info("on_wakeup trace bridge: wake=%s agent=%s", trace_id, agent_trace)
        spoken = await self._speak_transition(lambda: self.speak_as_role(role, reply, room, member or "家人", source_device=source_device))
        return {"ok": True, "role": role.id, "reply": reply, "spoken": spoken,
                "trace_id": trace_id, "agent_trace_id": agent_trace}

    # ---------- 电视遥控器语音按钮 → 触发管家（tv/livingroom/experiment）----------

    async def on_tv_voice(self, event) -> None:
        """TV 端小爱聆听结束事件（ButlerEvent，payload 来自 parse_experiment）：仅当命中唤醒词
        （interrupted==true，已打断小爱）时，把识别文本（去唤醒词前缀）作为用户语音送入管家大脑，并仅在电视上作答。

        普通小爱对话（interrupted=false，未命中关键词）直接忽略，不惊动管家。
        """
        payload = event.payload if hasattr(event, "payload") else (event or {})
        exp_type = str(payload.get("exp_type") or "")
        text = str(payload.get("text") or "").strip()
        interrupted = bool(payload.get("interrupted", False))
        logger.info("tv_voice event received: type=%s interrupted=%s text=%s", exp_type, interrupted, text[:40])
        if exp_type and exp_type != "xiaoai_listening_end":
            return
        if not interrupted:
            logger.info("tv_voice ignored (not interrupted): %s", text[:20])
            return
        if not text:
            return
        # 剥离唤醒词前缀（豆包管家/豆包/管家）
        cleaned = text
        for w in ("豆包管家", "豆包", "管家"):
            if cleaned.startswith(w):
                cleaned = cleaned[len(w):].strip()
                break
        if not cleaned:
            return
        rt = get_runtime()
        role = rt.roles.get("butler") or _default_butler_role()
        # v2.6#2：TV 语音按钮与唤醒词同等对待——进门即分配 trace
        import uuid
        trace_id = uuid.uuid4().hex[:8]
        logger.info("on_tv_voice: trace=%s text=%s", trace_id, cleaned[:30])
        from butler.core.trace_ctx import current_trace_id
        current_trace_id.set(trace_id)
        history = list(self._role_history.get("butler", []))
        self.state.set_state(DialogState.THINKING)
        try:
            reply = await self.agent.run(cleaned, "", history, system_override=role.system,
                                         trace_id=trace_id)
        except Exception as e:
            logger.warning("tv_voice agent run failed: %s", e)
            reply = f"嗯，刚才卡了一下（{type(e).__name__}），能再说一遍吗？"
        # 多轮上下文（按 butler 角色隔离，保留最近 12 轮）
        hist = history + [{"role": "user", "content": cleaned}, {"role": "assistant", "content": reply}]
        self._role_history["butler"] = hist[-12:]
        logger.info("tv_voice reply: %s", reply[:80])
        # 仅在电视出声（后台 TTS，不打断当前视频）
        await self._speak_transition(lambda: self._speak_tv_only(reply, role))

    async def _speak_tv_only(self, text: str, role) -> None:
        """仅在电视上出声：合成 edge-tts 后经 TVClient.play_url 后台播放（不打断视频）；
        合成失败则降级为电视弹窗通知。"""
        res = await self.tts.synthesize(
            text, voice=getattr(role, "voice", None), backend=getattr(role, "tts_backend", None), nowvoice_voice=getattr(role, "nowvoice_voice", None)
        )
        if res:
            self.tv.play_url(res.public_url)
        else:
            self.tv.notify({
                "title": getattr(role, "name", "豆包管家"), "content": text,
                "type": "info", "duration": 8000, "important": False, "pause_media": False,
            })
        engine = "edge-tts"
        try:
            await asyncio.to_thread(repo.add_turn, "家人", "butler", text, engine=engine,
                                    source="tv_voice", target="tv_living")
        except Exception as e:
            logger.warning("WO-DB-111 butler 回复落库失败（不影响播报）：%s", e)
        self.state.note_speak("家人")
        self.state.add_turn("家人", "butler", text)
        self._publish_dialog({
            "type": "speak", "member": "家人", "text": text, "engine": engine,
            "devices": ["tv_living"], "source": "tv_voice", "ts": time.time(),
        })

    def suppress_echo(self, text: str) -> None:
        """小爱出声后，其自身播报会被对话传感器再次识别，形成自问自答死循环。

        按文本长度估算播报时长 + 合成耗时 + 播放后唤醒检测窗口。
        注意：时钟从调用时（合成前）开始计时，edge-tts 合成需 3-8s，
        因此缓冲必须覆盖合成 + 播放 + 播放末尾的唤醒词检测窗口。
        中文 TTS 语速约 2.2-2.8 字/秒（YunyangNeural 偏慢），取保守值 2.5 字/秒。
        """
        secs = max(8.0, len(text) * 0.4 + 6.0)
        self._echo_until = max(self._echo_until, time.time() + secs)
        logger.info("echo suppress set +%.1fs (text %d chars)", secs, len(text))

    @staticmethod
    def _est_speak_secs(text: str) -> float:
        """估算中文音频时长（秒）：约 3.1 字/秒，作为 media_stop 的轮询阈值（不含缓冲，
        缓冲由 schedule_media_stop 的轮询/超时逻辑处理）。"""
        return max(2.0, len(text) * 0.32)

    async def _emit_devices(self, text: str, devices: list, role) -> list:
        """按设备分发发声；尊重设备 play_mode（notify_text / tts_speak）。返回每个设备的回执。

        TV 分支：speak_target=mqtt 时把文本委托给 Node-RED（但ler/speak/out 即「嘴」主题），
        由 NR 独占电视出声，避免 butler 与 NR 双声；否则 butler 直接放电视。
        小爱分支：一律由 butler 经 HA 直驱（按房间绑定），不经 NR。
        """
        out = []
        # 小爱出声会产生回声（自身播报被对话传感器再次捕获），预先设置抑制窗口
        if any(d.type == "xiaomi" for d in devices):
            self.suppress_echo(text)
        for dev in devices:
            if dev.type == "tv":
                if self.s.speak_target == "mqtt":
                    if self.mqtt:
                        self.mqtt.publish(PUB_SPEAK_OUT, {"text": text, "member": getattr(role, "name", "butler"), "engine": "edge-tts", "ts": time.time()})
                        logger.info("emit tv mqtt delegated device=%s text_len=%d topic=%s", dev.id, len(text), PUB_SPEAK_OUT)
                        out.append({"device": dev.id, "mode": "tv_delegated", "ok": True})
                    else:
                        # WO-BUT-025: MQTT 未连接，回退直连 TTS（不静默失败）
                        logger.warning("emit tv mqtt not connected, fallback to direct TTS device=%s", dev.id)
                        res = await self.tts.synthesize(text, voice=role.voice, backend=role.tts_backend, allow_bark_fallback=False, nowvoice_voice=role.nowvoice_voice)
                        if res:
                            self.tv.play_url(res.public_url)
                            logger.info("emit tv direct fallback device=%s url=%s", dev.id, res.public_url)
                            out.append({"device": dev.id, "mode": "tv_direct_fallback", "url": res.public_url, "ok": True})
                        else:
                            logger.error("emit tv direct fallback TTS failed device=%s", dev.id)
                            out.append({"device": dev.id, "mode": "tv_direct_fallback", "ok": False})
                else:
                    res = await self.tts.synthesize(text, voice=role.voice, backend=role.tts_backend, allow_bark_fallback=False, nowvoice_voice=role.nowvoice_voice)
                    if res:
                        self.tv.play_url(res.public_url)
                        out.append({"device": dev.id, "mode": "tv", "url": res.public_url, "ok": True})
                    else:
                        out.append({"device": dev.id, "mode": "tv", "ok": False})
            elif dev.type == "xiaomi":
                if dev.play_mode == "tts_speak" and dev.ha_player_entity:
                    # butler 自带 edge-tts 合成 mp3，经 media_player.play_media 在指定小爱出声（不走 notify/小爱原生）
                    res = await self.tts.synthesize(text, voice=dev.voice_override or role.voice, backend=role.tts_backend, allow_bark_fallback=False, nowvoice_voice=role.nowvoice_voice)
                    if res:
                        logger.info("emit xiaomi device=%s token_set=%s uid_set=%s player=%s", dev.id, bool(self.s.xiaomi_service_token), bool(self.s.xiaomi_user_id), dev.ha_player_entity)
                        # 优先：直连小爱云端 player_play_url（一次性投射，不循环）；否则回退 HA media_player
                        if self.s.xiaomi_service_token and self.s.xiaomi_user_id and dev.ha_player_entity:
                            aid = await self.ha.get_xiaoai_id(dev.ha_player_entity)
                            logger.info("emit xiaomi xiaoai_id=%s for %s", aid, dev.ha_player_entity)
                            if aid:
                                # 注：唤醒时 ear 已播"叮"提示音抢占通道，此处直接播内容
                                xmethod = getattr(dev, "xiaomi_play", None) or "url"
                                rj = await self.ha.play_xiaomi_url_once(aid, res.public_url, method=xmethod)
                                ok = str(rj.get("code")) == "0"
                                detail = f"xiaomi_direct:{rj.get('message', rj)}"
                                logger.info("emit xiaomi direct result device=%s code=%s detail=%s", dev.id, rj.get("code"), detail)
                                if xmethod == "music":
                                    # X08A 等固件：发完关循环 + 兜底 pause，防止单曲循环
                                    self.ha.schedule_xiaomi_stop(aid, self._est_speak_secs(text))
                                out.append({"device": dev.id, "mode": "tts_speak_direct", "ok": ok, "detail": detail, "play": xmethod})
                            else:
                                r = await self.ha.tts_play_url(res.public_url, dev.ha_player_entity)
                                out.append({"device": dev.id, "mode": "tts_speak", "ok": r.startswith("ok"), "detail": r})
                                await self.ha.schedule_media_stop(dev.ha_player_entity, self._est_speak_secs(text))
                        else:
                            r = await self.ha.tts_play_url(res.public_url, dev.ha_player_entity)
                            out.append({"device": dev.id, "mode": "tts_speak", "ok": r.startswith("ok"), "detail": r})
                            await self.ha.schedule_media_stop(dev.ha_player_entity, self._est_speak_secs(text))
                    else:
                        r = await self.ha.notify_message(text, dev.ha_entity or None)
                        out.append({"device": dev.id, "mode": "notify_fallback", "ok": r == "ok"})
                else:
                    # notify_text：小爱原生 TTS（execute_text_directive），不走 media
                    r = await self.ha.notify_message(text, dev.ha_entity or None)
                    out.append({"device": dev.id, "mode": "notify_text", "ok": r == "ok", "detail": r})
        return out

    async def speak_as_role(self, role, text: str, room: str, member: str, source_device: str = "") -> dict:
        """按角色 output_devices 分发发声；尊重设备 play_mode（notify_text / tts_speak）。"""
        rt = get_runtime()
        # 有触发设备时只用触发设备（哪个音箱问哪个音箱答），避免同房间多设备同时播报
        if source_device:
            devices = [d for d in rt.devices.all() if d.id == source_device and d.enabled]
            if not devices:
                logger.warning("source_device %s not found, fallback to output_devices", source_device)
                devices = rt.devices.resolve(role.output_devices, room)
        else:
            devices = rt.devices.resolve(role.output_devices, room)
        if not devices:
            logger.warning("role %s 在房间 %s 无可出声设备，Bark 兜底", role.id, room)
            if self.bark:
                await self.bark.push(text, title=f"{role.name} · {member}")
            return {"spoken": False, "fallback": "bark"}
        out = await self._emit_devices(text, devices, role)
        # WO-DB-111: butler reply persisted with target (TV + xiaomi both covered)
        ok_devs = [dd.get("device", "") for dd in out if dd.get("ok")]
        target = ",".join(ok_devs) if ok_devs else (room or "unknown")
        try:
            await asyncio.to_thread(
                repo.add_turn, member, "butler", text,
                voice=getattr(role, "voice", None),
                engine="edge-tts", source="active", target=target,
            )
        except Exception as e:
            logger.warning("WO-DB-111 butler 回复落库失败（不影响播报，也不得影响回声抑制）：%s", e)
        # 回声抑制：播报后标记设备，6 秒内忽略该设备的 ear 事件
        from butler.core.xiaomi_ear import XiaomiEar
        for d in devices:
            XiaomiEar._echo_cooldown[d.id] = time.time()
        return {"spoken": True, "devices": out}

    # ---------- 发声（统一出口）----------

    async def speak(self, text: str, member: str, *, source: str = "proactive", llm_ms: int = 0, use_queue: bool = True) -> dict:
        # 防重复：主动场景命中则跳过。这把闸在两条出口**之前**（第五轮 P0-9＝第三轮 T-1：
        # 旧形状＝入队成功就地 return，队列一活这把闸对那条腿永远轮不到）。
        if source == "proactive":
            dup, sim = await self.dedup.is_duplicate(member, text)
            if dup:
                logger.info("proactive dedup skip (sim=%.2f): %s", sim, text[:20])
                return {"spoken": False, "reason": "dedup", "similarity": sim}

        room = ""
        mc = self.s.member_by_name(member)
        if mc and mc.room:
            room = mc.room

        # v2.5: 可选走 TTS 队列（优先级/熔断/过载保护）
        queued = False
        if use_queue:
            from butler.tts.helper import enqueue_tts
            priority = 1 if source == "alert" else (2 if source == "proactive" else 3)
            queued = bool(enqueue_tts(text, priority=priority, room=room, member=member,
                                      override_quiet=True))

        if queued:
            # 入队＝还没播：这里⛔ 再自己 emit（同一条会被队列和直发各播一遍＝双声），
            # 但指纹／流水／在场计数／SSE 四条腿照走——旧形状里这段整个被 return 掉了（账实不符）。
            engine = "queue"
            out: list[dict] = []
            # WO-DB-111：入队时还不知道最终落到哪只音箱，target 只许写「queue:房间」，⛔ 编一台。
            tgt = f"queue:{room}" if room else "queue"
        else:
            # 统一按「角色 + 房间」解析出声设备：家庭管家(butler)默认在客厅，
            # 若能从成员查到所在房间则按房间过滤，使通用发声也走 output_devices 绑定（而非固定电视）。
            rt = get_runtime()
            role = rt.roles.get("butler") or _default_butler_role()
            devices = rt.devices.resolve(role.output_devices, room) if room else []
            if not devices:
                devices = rt.devices.resolve(role.output_devices, None)
            out = []
            if self.s.speak_target == "mqtt" and not devices:
                # 仅在没有可解析设备时，才把文本交给 Node-RED 当嘴（避免与下面直接出声双声）
                pass
            elif devices:
                out = await self._emit_devices(text, devices, role)
            else:
                # 兜底：直接电视 + Bark 纯文字
                res = await self.tts.synthesize(text, member=member)
                if res:
                    self.tv.play_url(res.public_url)
                    out.append({"device": "tv_living", "mode": "tv", "url": res.public_url, "ok": True})
                else:
                    await self.ha.tts_speak(text)
                    out.append({"device": "tv_living", "mode": "tv_fallback", "ok": True})
            engine = "edge-tts"
            ok_ids = [x for x in (d.get("device") for d in out) if x]
            tgt = ",".join(str(x) for x in ok_ids) if ok_ids else (room or "unknown")

        await self.dedup.record(member, text)
        dev_ids = [d.get("device") for d in out]
        try:
            await asyncio.to_thread(repo.add_turn, member, "butler", text,
                                    engine=engine, llm_ms=llm_ms, source=source, target=tgt)
        except Exception as e:
            logger.warning("WO-DB-111 butler 回复落库失败（不影响播报）：%s", e)
        self.state.note_speak(member)
        self.state.add_turn(member, "butler", text)
        # 注：PUB_SPEAK_OUT（Node-RED「嘴」主题）由 _emit_devices 的 TV 委托分支按需发布，
        # 这里不再全量发布，避免小爱路径被 NR 额外重复放电视。
        await self._speak_transition(lambda: self._publish_dialog({"type": "speak", "member": member, "text": text, "engine": engine,
                "devices": dev_ids, "source": source, "queued": queued, "ts": time.time()}))
        result = {"spoken": True, "engine": engine, "devices": out}
        if queued:
            result["queued"] = True
        return result

    async def _speak_transition(self, action):
        """SPEAKING → action → 无论成败都收尾（第六轮 P2-18）。

        旧形状＝set_state(SPEAKING) / await 发声 / set_state(WAITING) 三段平铺，发声一抛
        （HA 断连、合成失败、mqtt 抛错）后两段永不执行 ⇒ 状态机永久停在 SPEAKING，且日志里
        没有一句「卡住了」。收尾放进 finally；异常照原样上抛（吞异常＝另一枚缺陷）。
        """
        self.state.set_state(DialogState.SPEAKING)
        try:
            result = action()
            if inspect.isawaitable(result):
                result = await result
            return result
        finally:
            self.state.set_state(DialogState.WAITING)
            self._spawn_idle()

    def _spawn_idle(self) -> None:
        """起 _return_idle 并把 task 存进模块级强引用。

        第五轮 P0-7 同形状：裸 create_task 的返回值没人保存，loop 对任务只持弱引用
        ⇒ 可能在跑完前被回收；这里 add/discard 成对，注册表⛔ 越跑越涨。
        """
        task = asyncio.create_task(self._return_idle())
        _idle_tasks.add(task)
        task.add_done_callback(_idle_tasks.discard)

    async def _return_idle(self) -> None:
        await asyncio.sleep(self.s.waiting_seconds)
        self.state.set_state(DialogState.IDLE)

    # ---------- WebUI 测试 ----------

    async def test_speak(self, member: str, text: str) -> dict:
        res = await self.speak(text, member or "家人", source="test")
        await asyncio.to_thread(repo.add_turn, member or "家人", "user", text, source="test")
        self.state.add_turn(member or "家人", "user", text)
        return res

    # ---------- 场景上下文（best-effort）----------

    async def _scene_context(self) -> str:
        if not self.s.memory_agent_token:
            return ""
        try:
            st = await asyncio.wait_for(self.memory.get_vision_status(), timeout=4.0)
            txt = st.get("summary") or st.get("raw") or ""
            return txt[:300] if txt else ""
        except Exception as e:
            logger.debug("vision status fetch failed: %s", e)
            return ""

    async def _timed_proactive(self, payload: dict) -> None:
        """定时器触发的主动关怀（如早晨天气）。"""
        member = str(payload.get("member") or "")
        allowed, reason = await self.wakeup.decide("timer", member=member, room="客厅")
        if not allowed:
            return
        topic = str(payload.get("topic") or "今天也要元气满满呀")
        await self.speak(topic, member or "家人", source="proactive")

    # ---------- 内部 ----------

    def _publish_dialog(self, payload: dict) -> None:
        if self.mqtt:
            self.mqtt.publish(PUB_DIALOG, payload)
