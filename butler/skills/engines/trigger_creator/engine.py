"""自我编排引擎：用户自然语言描述需求 → LLM 生成 trigger JSON → 校验 → 保存生效。

v0.7 核心。用户通过小爱说"每天下午2-3点看到我就打招呼+播新闻"，
本引擎把需求转成 trigger 规则并保存，立即生效（TriggerStore 内存索引已更新）。

brain 配置：
  max_retry: LLM 生成 JSON 失败后的重试次数（默认 2）
"""
from __future__ import annotations

import json
import re

from butler.logging_setup import get_logger
from butler.skills.runner_types import SkillContext, SkillResult
from butler.triggers.schema import validate_trigger

logger = get_logger("butler.skills.engines.trigger_creator")

# 事件类型与说明（供 LLM 参考）
_EVENT_DOC = """可用事件类型（event）：
- face_detected: 摄像头/TV 识别到人脸。条件可用 member(成员名)、room(房间)、time_range(时间窗)
- voice_wake: 小爱/TV遥控语音唤醒。条件可用 text_contains(用户说的话包含关键词)、room
- button_pressed: 按钮按下。条件可用 button_id(按钮ID)、room
- scheduled: 定时触发（APScheduler）。条件可用 time_range
- device_state: 设备状态变化。条件可用 room"""

_CONDITION_DOC = """可用条件（conditions，全部可选，空=不限制）：
- time_range: "HH:MM-HH:MM"，支持跨午夜如 "22:00-06:00"
- member: 成员名字符串，如 "lidicn"
- room: 房间字符串，如 "客厅"
- button_id: 按钮ID（button_pressed 事件用）
- text_contains: ["关键词1","关键词2"]（voice_wake 事件用）
- require_presence: {"member":"Kevin"} 触发前确认成员在场"""

_ACTION_DOC = """动作（actions）：数组，每个元素 {"skill":"技能ID","params":{"参数名":"值"}}。
params 支持 {{event.member}} {{event.room}} 等模板变量，运行时替换为事件 payload。
动作按顺序执行，单个失败不中断后续（continue_on_error 默认 true）。"""

_EXAMPLE = """示例 trigger JSON：
{
  "id": "afternoon_greet_lidicn",
  "name": "下午看到lidicn打招呼+播新闻",
  "event": "face_detected",
  "conditions": {"time_range": "14:00-15:00", "member": "lidicn", "room": ""},
  "role": "butler",
  "actions": [
    {"skill": "greet", "params": {"member": "{{event.member}}"}},
    {"skill": "daily_dev_summary", "params": {"member": "{{event.member}}"}}
  ],
  "cooldown_sec": 600,
  "priority": 20
}"""


class TriggerCreatorEngine:
    async def run(self, ctx: SkillContext) -> SkillResult:
        rt = ctx.rt
        if rt is None:
            return SkillResult(ok=False, error="runtime not ready")

        # 1. 获取用户需求描述
        payload = ctx.payload or {}
        user_text = (
            payload.get("text")
            or payload.get("user_input")
            or payload.get("message")
            or payload.get("utterance")
            or ""
        ).strip()
        if not user_text:
            return SkillResult(ok=False, error="缺少需求描述（payload.text）")

        # 2. 收集可用技能列表
        available_skills = []
        if getattr(rt, "runner", None) is not None:
            for s in rt.runner.store.list():
                if s.get("enabled", True):
                    available_skills.append(f"- {s['id']}: {s.get('name', s['id'])}")
        skills_doc = "可用技能（actions.skill 只能从这里选）：\n" + "\n".join(available_skills)

        # 3. 构造 prompt
        system = (
            "你是家庭自动化规则生成器。根据用户的自然语言需求，输出一个 trigger JSON。\n"
            "只输出 JSON，不要输出任何解释文字、markdown 代码块或前后缀。\n"
            "id 用小写英文+下划线，见名知意，2-40字符。\n"
            "name 用中文简短描述。\n"
            "cooldown_sec 默认 300，priority 默认 10。\n"
            "如果用户需求无法映射到现有事件或技能，输出 {\"error\":\"原因\"}。"
        )
        prompt = f"""用户需求：{user_text}

{_EVENT_DOC}

{_CONDITION_DOC}

{_ACTION_DOC}

{skills_doc}

{_EXAMPLE}

请输出 trigger JSON："""

        # 4. 调用 LLM 生成（带重试）
        brain = ctx.skill.get("brain") or {}
        max_retry = int(brain.get("max_retry", 2))
        trigger_json = None
        last_err = ""

        for attempt in range(max_retry + 1):
            try:
                text, _ = await rt.llm.chat(
                    system,
                    [{"role": "user", "content": prompt}],
                    max_tokens=1024,
                    temperature=0.3,
                )
            except Exception as e:
                last_err = f"LLM 调用失败: {e}"
                logger.warning("trigger_creator LLM call %d failed: %s", attempt, e)
                continue

            # 5. 解析 JSON（容错：去掉 markdown 代码块、提取第一个 {...}）
            raw = (text or "").strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            try:
                trigger_json = json.loads(raw)
            except json.JSONDecodeError:
                # 尝试提取第一个 JSON 对象
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    try:
                        trigger_json = json.loads(m.group())
                    except json.JSONDecodeError:
                        pass
            if trigger_json is None:
                last_err = f"LLM 输出不是有效 JSON: {raw[:200]}"
                logger.warning("trigger_creator attempt %d JSON parse failed: %s", attempt, raw[:200])
                continue

            # LLM 可能返回 {"error":"..."}
            if isinstance(trigger_json, dict) and "error" in trigger_json and "id" not in trigger_json:
                return SkillResult(
                    ok=False,
                    text=f"暂时无法创建这个规则：{trigger_json['error']}",
                    status="rejected",
                )

            # 6. schema 校验
            normalized, verr = validate_trigger(trigger_json)
            if verr:
                last_err = f"校验失败: {verr}"
                logger.warning("trigger_creator attempt %d validate failed: %s", attempt, verr)
                # 把校验错误反馈给 LLM 重试
                prompt += f"\n\n上一次输出校验失败：{verr}\n请修正后重新输出 JSON。"
                trigger_json = None
                continue
            trigger_json = normalized
            break

        if trigger_json is None:
            return SkillResult(
                ok=False,
                text=f"规则生成失败，{last_err}。你可以换个说法再试试。",
                status="error",
            )

        # 7. 保存（立即生效：TriggerStore.save 同时更新内存索引）
        eng = getattr(rt, "trigger_engine", None)
        if eng is None:
            return SkillResult(ok=False, error="trigger_engine not ready")
        eng.store.save(trigger_json)
        logger.info("trigger_creator saved: %s (event=%s, actions=%d)",
                    trigger_json["id"], trigger_json["event"],
                    len(trigger_json.get("actions", [])))

        # 8. 构造口播回复
        action_skills = [a.get("skill", "") for a in trigger_json.get("actions", [])]
        reply = (
            f"好的，已创建规则「{trigger_json['name']}」。"
            f"触发条件：{trigger_json['event']}"
        )
        cond = trigger_json.get("conditions") or {}
        cond_parts = []
        if cond.get("time_range"):
            cond_parts.append(f"时间{cond['time_range']}")
        if cond.get("member"):
            cond_parts.append(f"成员{cond['member']}")
        if cond.get("room"):
            cond_parts.append(f"房间{cond['room']}")
        if cond_parts:
            reply += "（" + "、".join(cond_parts) + "）"
        reply += f"，执行 {'、'.join(action_skills)}。规则已生效。"

        meta = {
            "engine": "trigger_creator",
            "trigger_id": trigger_json["id"],
            "event": trigger_json["event"],
            "actions": action_skills,
        }
        return SkillResult(ok=True, text=reply, meta=meta)

    def describe(self) -> dict:
        return {
            "name": "自我编排引擎",
            "modes": ["nl_to_trigger"],
            "desc": "用户自然语言 → LLM 生成 trigger JSON → 校验 → 保存生效",
        }
