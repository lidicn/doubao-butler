"""技能生成器：根据用户自然语言描述生成技能 JSON（draft 状态）。

工作流程：
1. 用户描述需求（如"每天早上7点看到Kevin说早上好"）
2. LLM 解析意图，生成技能 JSON
3. 保存为 draft 状态，返回预览文本
4. 用户确认后启用，修改则重新生成，取消则丢弃
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from butler.logging_setup import get_logger
from butler.skills.schema import validate_skill

logger = get_logger("butler.skills.creator")

# 技能模板：常用场景的基础结构
TEMPLATES = {
    "greeting": {
        "trigger": {"entry": "face_seen"},
        "senses": [{"type": "camera", "room": "客厅", "max_age_s": 0}],
        "brain": {"type": "llm_text", "engine": "llm_text", "mode": "greeting",
                  "prompt": "", "system": "", "context": ["persona", "member_profile", "time"],
                  "dedup": True, "max_chars": 60, "mess_slot": False},
        "output": [{"type": "tv_notify", "tts": True}],
        "limits": {"per_day": 30},
        "on_busy": "drop",
    },
    "reminder": {
        "trigger": {"entry": "schedule"},
        "senses": [],
        "brain": {"type": "static", "engine": "static_text", "mode": "reminder",
                  "prompt": "", "system": "", "context": [],
                  "dedup": False, "max_chars": 80, "mess_slot": False},
        "output": [{"type": "xiaomi_speak", "tts": True}, {"type": "bark"}],
        "limits": {"per_day": 50},
        "on_busy": "queue",
    },
    "monitor": {
        "trigger": {"entry": "ha_event"},
        "senses": [{"type": "camera", "room": "客厅", "max_age_s": 0}],
        "brain": {"type": "vlm_compose", "engine": "camera_vlm", "mode": "ma_analyze",
                  "prompt": "", "system": "", "context": ["persona"],
                  "dedup": True, "max_chars": 100, "mess_slot": False},
        "output": [{"type": "tv_notify", "tts": True}],
        "limits": {"per_day": 20},
        "on_busy": "drop",
    },
    "automation": {
        "trigger": {"entry": "ha_event"},
        "senses": [],
        "brain": {"type": "ha_service", "engine": "ha_action", "mode": "service",
                  "domain": "", "service": "", "entity": "", "data": {},
                  "system": "", "context": [], "dedup": False, "max_chars": 0, "mess_slot": False},
        "output": [],
        "limits": {"per_day": 100},
        "on_busy": "drop",
    },
    "koin_action": {
        "trigger": {"entry": "schedule", "cron": "0 8 * * *"},
        "senses": [],
        "brain": {"type": "http_condition", "engine": "cron_task", "mode": "",
                  "prompt": "", "system": "", "context": [],
                  "dedup": False, "max_chars": 80, "mess_slot": False,
                  "api_id": "", "condition": {}, "message_template": ""},
        "output": [{"type": "xiaomi_speak", "tts": True, "room": "客厅"}],
        "limits": {"per_day": 20},
        "on_busy": "drop",
    },
}

# 触发入口说明（给 LLM 参考）
TRIGGER_GUIDE = """
触发入口（trigger.entry）可选值：
- webhook: HTTP 触发
- mqtt: MQTT 消息触发
- face_seen: 摄像头识别到人脸触发
- schedule: 定时触发
- ha_event: Home Assistant 事件触发
- heartbeat: 心跳定时触发
"""

# 引擎说明
ENGINE_GUIDE = """
大脑引擎（brain.engine）可选值：
- camera_vlm: 摄像头画面分析（多模态）
- llm_text: LLM 文本生成
- static_text: 静态文本回复
- ha_action: HA 服务调用（控制设备）
- decision: 决策引擎
- reminder_find: 提醒查询
- trigger_creator: 创建触发器
- cron_task: 智动任务（调用 HTTP API，按条件判断后播报/推送）
  - brain 额外字段：api_id（已注册的 API ID）、condition（条件）、message_template（播报文案模板）
  - condition 格式一（通用 JSONPath）：{"jsonpath": "$.result.realtime.temperature", "comparator": "gt", "value": 30}
  - condition 格式二（本地小时匹配，用于天气）：{"type": "hour_local", "hours": [12,18,20], "threshold": 50, "any": true}
  - output 支持：xiaomi_speak（小爱播报）、bark（手机推送）、tv_notify（电视通知）
"""


class SkillCreator:
    """技能生成器：管理草稿、生成、确认流程。"""

    def __init__(self, store, data_dir: str = "/app/data"):
        self.store = store
        self.draft_dir = Path(data_dir) / "skills" / "_drafts"
        self.draft_dir.mkdir(parents=True, exist_ok=True)
        self._pending: dict[str, dict] = {}  # role_id -> draft skill
        self._generation: int = 0  # WO-BUT-002②：草稿代际，跨代作废

    def _draft_path(self, draft_id: str) -> Path:
        return self.draft_dir / f"{draft_id}.json"

    def create_draft(self, skill: dict, role_id: str = "butler") -> dict:
        """创建草稿技能，保存到 _drafts 目录，等待用户确认。"""
        normalized, err = validate_skill(skill)
        if err:
            return {"ok": False, "error": f"技能定义校验失败: {err}"}
        normalized["status"] = "draft"
        normalized["source"] = "agent"
        normalized["draft_id"] = f"draft_{int(time.time())}"
        normalized["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        # WO-BUT-002：代际号（跨代作废）+ UNKNOWN 重试计数（上限1次）
        self._generation += 1
        normalized["generation"] = self._generation
        normalized["unknown_retry"] = 0
        # 保存草稿
        path = self._draft_path(normalized["draft_id"])
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        # 记录待确认
        self._pending[role_id] = normalized
        logger.info("skill draft created: %s (%s) by %s", normalized["id"], normalized["name"], role_id)
        return {"ok": True, "draft": normalized, "preview": self._preview(normalized)}

    def _preview(self, skill: dict) -> str:
        """生成人类可读的预览文本。"""
        trigger = skill.get("trigger", {})
        entry_map = {
            "webhook": "HTTP调用", "mqtt": "MQTT消息", "face_seen": "人脸识别",
            "schedule": "定时", "ha_event": "HA事件", "heartbeat": "心跳",
        }
        entry = entry_map.get(trigger.get("entry"), trigger.get("entry"))
        brain = skill.get("brain", {})
        outputs = [o.get("type") for o in skill.get("output", [])]
        output_map = {"tv_notify": "电视播报", "xiaomi_speak": "小爱音箱", "bark": "手机推送"}
        output_text = "、".join(output_map.get(o, o) for o in outputs)

        lines = [
            f"技能名称：{skill.get('name')}",
            f"触发方式：{entry}",
            f"处理引擎：{brain.get('engine', '未知')}",
            f"输出方式：{output_text}",
            f"每日上限：{skill.get('limits', {}).get('per_day', 20)}次",
        ]
        # cron_task 额外信息
        if brain.get("engine") == "cron_task":
            if brain.get("api_id"):
                lines.append(f"数据来源：{brain['api_id']}")
            cond = brain.get("condition", {})
            if cond.get("type") == "hour_local":
                hours = "、".join(str(h) + "点" for h in cond.get("hours", []))
                mode = "任一" if cond.get("any") else "全部"
                lines.append(f"判断条件：{hours} 降水概率>{cond.get('threshold', 50)}%（{mode}满足）")
            elif cond.get("jsonpath"):
                lines.append(f"判断条件：{cond.get('jsonpath')} {cond.get('comparator')} {cond.get('value')}")
            if brain.get("message_template"):
                lines.append(f"播报文案：{brain['message_template']}")
        senses = skill.get("senses", [])
        if senses:
            rooms = "、".join(s.get("room", "") for s in senses if s.get("room"))
            if rooms:
                lines.append(f"涉及房间：{rooms}")
        return "\\n".join(lines)

    def confirm(self, role_id: str = "butler") -> dict:
        """用户确认草稿，保存为 enabled 状态。"""
        draft = self._pending.pop(role_id, None)
        if draft is None:
            return {"ok": False, "error": "没有待确认的技能草稿"}
        draft["status"] = "enabled"
        draft["enabled"] = True
        draft.pop("draft_id", None)
        draft.pop("created_at", None)
        draft["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.store.save(draft)
        # 删除草稿文件
        path = self._draft_path(draft.get("draft_id", ""))
        if path.exists():
            path.unlink()
        logger.info("skill confirmed and enabled: %s", draft["id"])
        return {"ok": True, "skill": draft}

    def cancel(self, role_id: str = "butler") -> dict:
        """用户取消草稿。"""
        draft = self._pending.pop(role_id, None)
        if draft is None:
            return {"ok": False, "error": "没有待确认的技能草稿"}
        path = self._draft_path(draft.get("draft_id", ""))
        if path.exists():
            path.unlink()
        logger.info("skill draft cancelled: %s", draft.get("id"))
        return {"ok": True, "message": "已取消技能创建"}

    def get_pending(self, role_id: str = "butler") -> dict | None:
        """获取当前角色待确认的草稿。跨代草稿自动作废（WO-BUT-002②）。"""
        pending = self._pending.get(role_id)
        if pending and pending.get("generation", 0) != self._generation:
            logger.info("stale draft generation %s != current %s, discarding",
                        pending.get("generation"), self._generation)
            self._pending.pop(role_id, None)
            return None
        return pending

    def list_drafts(self) -> list[dict]:
        """列出所有草稿。"""
        drafts = []
        for f in sorted(self.draft_dir.glob("*.json")):
            try:
                drafts.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
        return drafts

    async def generate_skill_from_description(self, description: str, llm_client, system_prompt: str = "") -> dict:
        """调用 LLM 根据用户描述生成技能 JSON。

        这是核心方法：把用户自然语言描述转成技能定义。
        llm_client 需要有 chat() 方法。
        """
        prompt = f"""你是技能生成器。根据用户描述生成一个豆包管家技能的 JSON 定义。

{TRIGGER_GUIDE}
{ENGINE_GUIDE}

用户描述：{description}

请输出 JSON 格式的技能定义，包含以下字段：
- id: 小写字母数字下划线，2-40位
- name: 技能名称
- trigger: {{"entry": "触发入口"}}（定时触发用 "schedule"，并加 cron 字段如 "0 8 * * *"）
- senses: [{{"type": "camera", "room": "房间名"}}]（不需要感官时空数组）
- brain: {{"type": "类型", "engine": "引擎名", "mode": "模式", "prompt": "", "system": "", "context": [], "dedup": false, "max_chars": 80, "mess_slot": false}}
  - 如果 engine 是 "cron_task"，brain 还需包含：api_id、condition、message_template
- output: [{{"type": "输出类型", "tts": true}}]
- limits: {{"per_day": 数字}}
- on_busy: "drop" 或 "queue"
- role: "butler"

已注册的 API 列表（用户描述中提到天气相关时用 caiyunweather）：
- caiyunweather: 彩云天气 API（支持 hour_local 条件按本地小时匹配降水概率）

只输出 JSON，不要其他文字。"""

        try:
            text, _ = await llm_client.chat(system_prompt or "你是技能生成专家。",
                                       [{"role": "user", "content": prompt}],
                                       max_tokens=1500, temperature=0.3)
            # 提取 JSON
            text = text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            # 找到第一个 { 和最后一个 }
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end + 1]
            skill = json.loads(text)
            return {"ok": True, "skill": skill}
        except Exception as e:
            logger.warning("generate_skill failed: %s", e)
            return {"ok": False, "error": f"生成技能失败: {e}"}
