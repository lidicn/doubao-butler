"""autoflow_propose 技能引擎（v1.8 P1-3）。

持久化家庭自动化规则委托 autoflow（提案闸→编译闸→vhass孪生自证→人审→NR执行）。

触发边界路由判定准则：
- HA 设备域事件/状态 → autoflow_propose（DSL 可表达，走验证链）
- MA 行为信号且需实时融合 → 管家引擎（create_trigger，这是决策不是自动化）
- DeskPilot/TVPilot 设备动作 → 管家技能（ReAct 工具，本来就归管家）
- 会话内临时（<1h）→ 管家引擎 + PushGuard（set_reminder，不持久化）
- 拿不准 → 问用户要不要"长期生效"，要 → autoflow_propose

autoflow 宕机降级：
- 管家对 autoflow 健康检查，宕机时降级为"记下意图，恢复后补提案"
- 参考管家自己给 MA 信号设计的 60s 降级策略，同一手法

环境变量：
- AUTOFLOW_URL：autoflow 服务地址（默认 http://192.168.2.200:8000）
- AUTOFLOW_TOKEN：autoflow API Token（从 autoflow WebUI 获取）
- AUTOFLOW_TIMEOUT：请求超时秒数（默认 15）
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult

logger = get_logger("butler.skills.engines.autoflow_propose")

# autoflow 配置
AUTOFLOW_URL = os.environ.get("AUTOFLOW_URL", "http://192.168.2.200:8000")
AUTOFLOW_TOKEN = os.environ.get("AUTOFLOW_TOKEN", "")
AUTOFLOW_TIMEOUT = int(os.environ.get("AUTOFLOW_TIMEOUT", "15"))

# HA 设备域关键词（用于触发边界路由判定）
HA_DEVICE_KEYWORDS = [
    "灯", "空调", "风扇", "窗帘", "插座", "开关", "传感器", "门磁",
    "人体感应", "温度", "湿度", "空气质量", "洗衣机", "冰箱", "扫地机器人",
    "light", "switch", "climate", "fan", "cover", "sensor",
    "binary_sensor", "automation", "scene", "script",
]

# MA 行为信号关键词（需要实时融合，不适合 autoflow）
MA_BEHAVIOR_KEYWORDS = [
    "位置", "在哪个房间", "行为", "习惯", "视觉", "人脸识别",
    "presence", "behavior", "location", "face",
]

# 管家专属设备动作关键词（DeskPilot/TVPilot）
BUTLER_DEVICE_KEYWORDS = [
    "电视", "换台", "播放", "电脑", "桌面", "打开软件",
    "tv", "desktop", "deskpilot", "tvpilot",
]


class AutoflowProposeEngine:
    """持久化自动化提案引擎：把用户描述转为 autoflow DSL 提案，提交到 autoflow 竞技场。"""

    async def run(self, ctx: SkillContext) -> SkillResult:
        brain = ctx.skill.get("brain") or {}
        # 用户描述从 payload 或 brain.prompt 获取
        user_text = (ctx.payload or {}).get("text") or brain.get("prompt") or ""
        if not user_text:
            return SkillResult(ok=False, error="缺少自动化规则描述")

        rt = ctx.rt
        if rt is None:
            return SkillResult(ok=False, error="runtime not ready")

        # 1. 触发边界路由判定
        route_decision = self._route_trigger(user_text)
        logger.info("autoflow_propose route: %s for text: %s",
                    route_decision["route"], user_text[:50])

        # 如果不适合 autoflow，返回路由建议（不提交提案）
        if route_decision["route"] != "autoflow":
            return SkillResult(
                ok=True,
                text=self._format_route_advice(user_text, route_decision),
                meta={"engine": "autoflow_propose", "route": route_decision["route"],
                      "submitted": False, "reason": route_decision["reason"]},
            )

        # 2. 检查 autoflow 可用性
        if not AUTOFLOW_TOKEN:
            # Token 未配置，降级为"记下意图，提示配置 Token"
            return SkillResult(
                ok=True,
                text=(
                    f"📋 自动化规则已记录（待提交 autoflow）：\n{user_text}\n\n"
                    f"⚠️ autoflow API Token 未配置（环境变量 AUTOFLOW_TOKEN）。"
                    f"请在管家 .env 中配置后重新触发，或手动在 autoflow WebUI 创建此规则。"
                ),
                meta={"engine": "autoflow_propose", "route": "autoflow",
                      "submitted": False, "reason": "token_not_configured",
                      "pending_text": user_text},
            )

        # 3. 检查 autoflow 健康状态
        if not await self._check_autoflow_health():
            # autoflow 宕机降级：记下意图
            logger.warning("autoflow unavailable, degrading to pending")
            return SkillResult(
                ok=True,
                text=(
                    f"📋 自动化规则已记录（autoflow 暂不可用，恢复后补提交）：\n{user_text}\n\n"
                    f"autoflow 服务当前不可达，规则已暂存。服务恢复后将自动补提交。"
                ),
                meta={"engine": "autoflow_propose", "route": "autoflow",
                      "submitted": False, "reason": "autoflow_unavailable",
                      "pending_text": user_text},
            )

        # 4. 用 LLM 把用户描述转为 autoflow DSL 提案
        try:
            dsl_proposal = await self._generate_dsl_proposal(rt, user_text)
        except Exception as e:
            logger.error("DSL generation failed: %s", e)
            return SkillResult(ok=False, error=f"DSL 生成失败: {str(e)[:200]}")

        # 5. 提交到 autoflow 提案闸
        try:
            result = await self._submit_to_autoflow(dsl_proposal, user_text)
        except Exception as e:
            logger.error("autoflow submit failed: %s", e)
            return SkillResult(
                ok=True,
                text=(
                    f"📋 自动化规则已记录（提交 autoflow 失败，待重试）：\n{user_text}\n\n"
                    f"错误: {str(e)[:100]}\n请稍后重试，或手动在 autoflow WebUI 创建。"
                ),
                meta={"engine": "autoflow_propose", "route": "autoflow",
                      "submitted": False, "reason": "submit_failed",
                      "error": str(e)[:200], "pending_text": user_text},
            )

        # 6. 返回 autoflow 验证链反馈
        return SkillResult(
            ok=True,
            text=self._format_submit_result(user_text, result),
            meta={"engine": "autoflow_propose", "route": "autoflow",
                  "submitted": result.get("submitted", False),
                  "proposal_id": result.get("proposal_id", ""),
                  "knowledge_feedback": result.get("knowledge_feedback", "")},
        )

    def _route_trigger(self, text: str) -> dict:
        """触发边界路由判定。

        Returns:
            {"route": "autoflow"|"butler_engine"|"butler_skill"|"reminder"|"ask_user",
             "reason": str, "confidence": float}
        """
        text_lower = text.lower()

        # 会话内临时（<1h）→ set_reminder
        temporary_keywords = ["等会儿", "一会儿", "等一下", "待会", "半小时内", "1小时内", "提醒我"]
        if any(kw in text for kw in temporary_keywords):
            return {"route": "reminder", "reason": "会话内临时提醒（<1h），用 set_reminder 不持久化", "confidence": 0.8}

        # 管家专属设备动作（DeskPilot/TVPilot）→ 管家技能
        if any(kw in text_lower for kw in BUTLER_DEVICE_KEYWORDS):
            return {"route": "butler_skill", "reason": "涉及管家专属设备（TV/电脑），归管家技能/ReAct 工具", "confidence": 0.7}

        # MA 行为信号且需实时融合 → 管家引擎
        if any(kw in text for kw in MA_BEHAVIOR_KEYWORDS):
            # 检查是否需要实时融合（"当XX在YY时就ZZ"这种模式）
            if "当" in text and ("时" in text or "就" in text):
                return {"route": "butler_engine", "reason": "涉及 MA 行为信号且需实时融合判断，这是决策不是自动化，用管家 create_trigger", "confidence": 0.75}

        # HA 设备域 → autoflow
        ha_count = sum(1 for kw in HA_DEVICE_KEYWORDS if kw in text_lower)
        if ha_count >= 1:
            return {"route": "autoflow", "reason": f"涉及 HA 设备域（{ha_count} 个关键词），DSL 可表达，走 autoflow 验证链", "confidence": 0.8}

        # 拿不准 → 问用户
        return {"route": "ask_user", "reason": "无法明确判定边界，需询问用户是否要长期生效", "confidence": 0.3}

    def _format_route_advice(self, text: str, decision: dict) -> str:
        """格式化路由建议文本。"""
        route_names = {
            "butler_engine": "管家实时决策引擎（create_trigger）",
            "butler_skill": "管家技能（ReAct 工具）",
            "reminder": "会话内临时提醒（set_reminder）",
            "ask_user": "需确认",
        }
        route_name = route_names.get(decision["route"], decision["route"])
        return (
            f"🔀 触发边界路由判定：此规则不适合提交 autoflow。\n\n"
            f"规则描述：{text}\n"
            f"建议路由：{route_name}\n"
            f"判定理由：{decision['reason']}\n\n"
            f"如果这是长期生效的家庭自动化规则（纯 HA 设备域），请重新描述为"
            f"「每当[HA设备事件]就[HA设备动作]」的格式，我会提交给 autoflow。"
        )

    async def _check_autoflow_health(self) -> bool:
        """检查 autoflow 服务健康状态。"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{AUTOFLOW_URL}/api/health",
                    headers={"Authorization": f"Bearer {AUTOFLOW_TOKEN}"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.debug("autoflow health check failed: %s", e)
            return False

    async def _generate_dsl_proposal(self, rt, user_text: str) -> dict:
        """用 LLM 把用户描述转为 autoflow DSL 提案。"""
        system = (
            "你是家庭自动化规则专家。把用户的自然语言描述转为 autoflow DSL 提案。\n"
            "autoflow DSL 格式：\n"
            "{\n"
            '  "name": "规则名称",\n'
            '  "description": "规则描述",\n'
            '  "trigger": {"type": "state|time|event", "entity_id": "...", "state": "..."},\n'
            '  "conditions": [{"type": "state", "entity_id": "...", "state": "..."}],\n'
            '  "actions": [{"service": "domain.service", "data": {"entity_id": "..."}}]\n'
            "}\n"
            "只输出 JSON，不要输出其他内容。"
        )
        prompt = f"用户描述：{user_text}\n\n请输出 autoflow DSL 提案 JSON。"

        text, _ = await rt.llm.chat(system, [{"role": "user", "content": prompt}], max_tokens=500, temperature=0.3)

        # 解析 LLM 输出的 JSON
        text = (text or "").strip()
        # 尝试提取 JSON 部分
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            dsl = json.loads(text)
        except json.JSONDecodeError:
            # 如果解析失败，返回基础结构
            dsl = {
                "name": user_text[:30],
                "description": user_text,
                "trigger": {"type": "state", "entity_id": "", "state": ""},
                "conditions": [],
                "actions": [],
                "raw_text": text,
            }

        return dsl

    async def _submit_to_autoflow(self, dsl_proposal: dict, user_text: str) -> dict:
        """提交提案到 autoflow 竞技场。"""
        import aiohttp

        payload = {
            "name": dsl_proposal.get("name", user_text[:30]),
            "description": dsl_proposal.get("description", user_text),
            "dsl": dsl_proposal,
            "source": "doubao-butler",
            "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{AUTOFLOW_URL}/api/arena/propose",
                headers={
                    "Authorization": f"Bearer {AUTOFLOW_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=aiohttp.ClientTimeout(total=AUTOFLOW_TIMEOUT),
            ) as resp:
                resp_text = await resp.text()
                logger.info("autoflow propose response: status=%d body=%s", resp.status, resp_text[:200])

                if resp.status == 200:
                    try:
                        data = json.loads(resp_text)
                        return {
                            "submitted": True,
                            "proposal_id": data.get("id", data.get("proposal_id", "")),
                            "knowledge_feedback": data.get("knowledge_feedback", data.get("feedback", "")),
                            "status": data.get("status", "pending"),
                        }
                    except json.JSONDecodeError:
                        return {"submitted": True, "proposal_id": "", "knowledge_feedback": resp_text[:200]}
                elif resp.status == 401:
                    raise Exception("autoflow API Token 无效（401）")
                else:
                    raise Exception(f"autoflow 返回 {resp.status}: {resp_text[:100]}")

    def describe(self) -> dict:
        return {
            "name": "持久化自动化提案（autoflow）",
            "modes": [],
            "desc": "把长期家庭自动化规则（HA 设备域）提交到 autoflow 竞技场，走七轮验证链后部署到 Node-RED。涉及 MA 行为信号/管家专属设备/会话内临时的规则会路由到管家侧。",
        }
