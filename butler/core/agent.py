"""Agent：把「系统人格 + 家庭记忆 + 工具循环」封装成一句话入口。

对话状态/repo 记录由 DialogManager 负责；Agent 只产出最终回复文本。
v1.1 起：每次 run 创建 AgentTracer，ReAct 每步落库可回放。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from homesdk.time import house_now

from butler.agent_trace import AgentTracer
from butler.agent_skill import match_dialog_skill, extract_params_from_text
from butler.core.persona import PersonaEngine
from butler.core.tools import TOOL_SCHEMAS, dispatch_tool
from butler.core.trace_ctx import current_trace_id
from butler.logging_setup import get_logger
import contextvars

# P1-1：请求级上下文变量，避免单例跨请求串话
_current_user_text = contextvars.ContextVar("current_user_text", default="")
_current_trace_id = current_trace_id

logger = get_logger("butler.agent")


class Agent:
    def __init__(self, settings, llm, ha, memory, tv, doubao, scheduler=None, tvpilot=None, deskpilot=None, newapi=None, docker=None, skill_store=None):
        self.s = settings
        self.llm = llm
        self.ha = ha
        self.memory = memory
        self.tv = tv
        self.doubao = doubao
        self.scheduler = scheduler
        self.tvpilot = tvpilot
        self.deskpilot = deskpilot
        self.newapi = newapi
        self.docker = docker
        self.skill_store = skill_store
        from butler.core.fast_routes import FastRouteStore
        self.fast_routes = FastRouteStore()
        # 最近一次 trace_id，供 API 层查询回放
        self.last_trace_id: str = ""

    def _strip_wake(self, text: str) -> str:
        wp = (self.s.wake_phrase or "").strip()
        if wp and text.startswith(wp):
            return text[len(wp):].strip()
        return text

    async def _fast_route(self, text: str, source: str = "webui") -> str | None:
        """快速路由：确定性操作直接执行，不走 LLM。命中返回结果，未命中返回 None。"""
        rule = self.fast_routes.match(text)
        if rule:
            return await dispatch_tool(rule["tool"], rule.get("tool_args", {}), self)

        # 设备控制兜底：打开/关闭/开灯/关灯 直接执行
        t = text.strip()
        if any(t.startswith(p) for p in ("打开", "关闭", "开灯", "关灯", "打开灯", "关闭灯")):
            # 提取设备名（去掉动词前缀）
            for prefix in ("打开", "关闭", "开灯", "关灯", "打开灯", "关闭灯"):
                if t.startswith(prefix):
                    device_name = t[len(prefix):].strip()
                    if device_name:
                        service = "turn_on" if "打开" in prefix or "开灯" in prefix else "turn_off"
                        logger.info("fast device control: %s -> %s", device_name, service)
                        # 不传 domain，让 _resolve_entity 跨域匹配
                        return await dispatch_tool("control_device", {
                            "entity_id": device_name,
                            "service": service,
                        }, self)
        return None

    async def run(self, text: str, member: str = "", history: list | None = None,
                  system_override: str | None = None, source: str = "active",
                  trace_id: str = "") -> str:
        text = self._strip_wake(text)
        if not text:
            return "我在听，你说。"

        # P1-1：用 contextvar 记录当前请求用户输入，避免并发串话
        _current_user_text.set(text)
        system = system_override or self.s.persona.system
        # 注入当前时间（Asia/Shanghai），避免模型胡编时间
        now = house_now()
        weekday_cn = ["周一","周二","周三","周四","周五","周六","周日"][now.weekday()]
        system += f"\n\n【当前时间】{now.strftime('%Y年%m月%d日')} {weekday_cn} {now.strftime('%H:%M')}（Asia/Shanghai）。回答中涉及时间请以此为准，不确定时说不知道，不要编造。"
        if member:
            ctx = PersonaEngine(self.s).member_context(member)
            if ctx:
                system += "\n\n【当前对话对象】" + ctx

        # v2.6#1 记忆召回：MA 语义检索 → MA 最近记忆 → 管家本地可读副本（G2 降级路径）
        from butler.core.recall import recall_facts
        facts, _recall_leg = await recall_facts(
            self.memory, query=text, member=member, limit=5)
        if facts:
            system += "\n\n【我记得的关于这个家】" + "；".join(facts[:5])

        # 在场状态（best-effort）：O-3 走 memory 客户端单点，取不到就不写「没人」
        try:
            pres = await self.memory.presence_status(room=None, minutes=30, timeout=3.0)
            if pres.get("ok"):
                items = pres.get("items") or []
                if items:
                    present = [f"{i.get('name','?')}在{i.get('room','?')}" for i in items]
                    system += "\n\n【当前在家】" + "，".join(present)
                else:
                    system += "\n\n【当前在家】最近30分钟未识别到家庭成员。"
            else:
                logger.warning("在场状态未取得：%s", pres.get("error"))
        except Exception as e:
            logger.warning("在场状态异常：%s", e)

        messages = list(history or [])
        messages.append({"role": "user", "content": text})

        # 轨迹收集器：本次 ReAct 运行的每步都落库
        tracer = AgentTracer(user_text=text, member=member, source=source,
                             trace_id=trace_id)
        _current_trace_id.set(tracer.trace_id)
        self.last_trace_id = tracer.trace_id  # 兼容 API 查询（最后一次）

        # ── 快速路由：确定性操作直接执行，不走 LLM ──
        fast = await self._fast_route(text, source)
        if fast is not None:
            # v2.6#2：快路由沿用同一条 trace，不再另起 id（否则一句话断成两条链）
            tracer.final(fast)
            tracer.finish("ok")
            return fast

        # ── M3 技能命中：优先匹配已发布的操作模板，命中直接执行，失败回退 ReAct ──
        skill_reply = await self._try_skill_match(text, tracer)
        if skill_reply is not None:
            return skill_reply

        async def executor(name, args):
            return await dispatch_tool(name, args, self)

        # TV 观察验证器：launch_app / zap 后自动查前台，结果回灌 ReAct
        async def tv_observe(tool: str, args: dict, result: str) -> str | None:
            tvp = getattr(self, "tvpilot", None)
            if tvp is None:
                return None
            try:
                if tool == "tv_launch_app":
                    expected = args.get("package", "")
                    ok, actual = await tvp.ensure_foreground(expected)
                    if ok:
                        return f"前台验证通过：当前 {actual} = 目标 {expected}"
                    return f"前台验证未通过：目标 {expected}，实际 {actual}"
                if tool == "switch_channel":
                    # 换台后验证 mytv 在前台
                    ok, actual = await tvp.ensure_foreground("com.tvcam.mytv")
                    if ok:
                        return f"换台后前台验证通过：mytv 在前台（{actual}）"
                    return f"换台后前台验证未通过：mytv 不在前台，实际 {actual}"
            except Exception as e:
                logger.warning("tv_observe failed for %s: %s", tool, e)
            return None

        # DeskPilot 观察验证器：volume_set / windows_activate 后自动验证，结果回灌 ReAct
        async def desk_observe(tool: str, args: dict, result: str) -> str | None:
            dpk = getattr(self, "deskpilot", None)
            if dpk is None:
                return None
            try:
                if tool == "desk_volume_set":
                    expected = int(args.get("level", -1))
                    ok, actual = await dpk.ensure_volume(expected)
                    if ok:
                        return f"音量验证通过：当前 {actual} = 目标 {expected}"
                    return f"音量验证未通过：目标 {expected}，实际 {actual}"
                if tool == "desk_windows_activate":
                    title = args.get("title", "")
                    ok, actual = await dpk.ensure_window_active(title)
                    if ok:
                        return f"窗口验证通过：已激活「{actual}」"
                    return f"窗口验证未通过：{actual}"
            except Exception as e:
                logger.warning("desk_observe failed for %s: %s", tool, e)
            return None

        # 合并观察器：TV + DeskPilot
        async def observe_fn(tool: str, args: dict, result: str) -> str | None:
            r = await tv_observe(tool, args, result)
            if r is not None:
                return r
            return await desk_observe(tool, args, result)

        # LLM 调用：失败后重试 1 次（P1-2：仅在未执行工具时重试，避免重放副作用）
        reply = ""
        for attempt in range(2):
            try:
                reply = await self.llm.chat_with_tools(
                    system, messages, TOOL_SCHEMAS, executor,
                    tracer=tracer,
                    observe_fn=observe_fn,
                    max_iter=15,
                    time_budget=120.0,
                )
                if reply:
                    break
            except Exception as e:
                logger.warning("agent run attempt %d failed: %s (type=%s)", attempt+1, e, type(e).__name__)
                # P1-2：如果首次尝试已执行过工具（有副作用），不再重试，避免重复操作设备
                has_tool_calls = any(m.get("role") == "tool" for m in messages)
                if attempt == 0 and not has_tool_calls:
                    await asyncio.sleep(1.0)
                    continue
                tracer.finish("error", str(e))
                err_type = type(e).__name__
                err_msg = str(e)[:80]
                reply = f"嗯，刚才脑子卡了一下（{err_type}: {err_msg}），能再说一遍吗？"
        # 如果第一次就成功，tracer 已在 chat_with_tools 内 finish；
        # 如果异常且重试后仍失败，上面已 finish("error")。
        if not reply:
            if tracer.status == "running":
                tracer.finish("ok")
            reply = "（没有回复）"
        return reply

    # ── M3 操作模板技能匹配与执行 ──────────────────────────

    async def _try_skill_match(self, text: str, tracer) -> str | None:
        """尝试匹配已发布的操作模板技能。命中并执行成功返回回复文本；
        未命中或执行失败返回 None（调用方继续走 ReAct）。"""
        store = getattr(self, "skill_store", None)
        if store is None:
            return None
        try:
            skills = list(store._index.values())
        except Exception:
            return None
        matched = match_dialog_skill(text, skills)
        if matched is None:
            return None
        params = extract_params_from_text(text, matched)
        logger.info("skill matched: %s (params=%s)", matched["id"], params)
        tracer.thought(f"命中操作模板技能「{matched['name']}」，直接参数化执行，跳过 ReAct 逐步规划。")
        try:
            from butler.skills.engines.tool_sequence.engine import ToolSequenceEngine
            from butler.skills.runner_types import SkillContext
            engine = ToolSequenceEngine()
            ctx = SkillContext(skill=matched, source="agent", payload=params, rt=None)
            # 引擎需要 rt.agent 来 dispatch_tool，用一个轻量代理
            class _RT: pass
            rt = _RT()
            rt.agent = self
            ctx.rt = rt
            result = await engine.run(ctx)
            if result.ok:
                tracer.action("skill_execute", {"skill_id": matched["id"], "params": params})
                tracer.observation("skill_execute", f"技能执行成功：{result.text}")
                tracer.final(result.text)
                tracer.finish("ok")
                return result.text
            else:
                logger.warning("skill %s execute failed: %s, fallback to ReAct", matched["id"], result.error)
                tracer.observation("skill_execute", f"技能执行失败（回退 ReAct）：{result.error}")
                return None
        except Exception as e:
            logger.warning("skill match/exec error: %s, fallback to ReAct", e)
            return None
