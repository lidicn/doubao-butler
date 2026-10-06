"""技能模板市场（v1.7）。

内置常用技能模板，用户/Agent 可基于模板一键创建技能。
模板分类：问候、日程、设备控制、安防、娱乐、健康、学习。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from butler.logging_setup import get_logger

logger = get_logger("butler.skills.templates")


# 内置模板库
BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "tpl_greet_member",
        "name": "成员问候",
        "category": "greeting",
        "description": "识别到指定成员时，在对应房间打招呼",
        "icon": "👋",
        "params": [
            {"key": "member", "label": "成员", "type": "select", "options": ["lidicn", "Kevin", "Emily"], "required": True},
            {"key": "room", "label": "房间", "type": "select", "options": ["客厅", "书房", "主卧室", "Kevin房间", "Emily房间"], "required": False},
            {"key": "time_range", "label": "时间范围", "type": "text", "placeholder": "07:00-09:00", "required": False},
            {"key": "greeting", "label": "问候语", "type": "text", "placeholder": "{{member}}回来啦，今天过得怎么样", "required": True},
        ],
        "skill_template": {
            "name": "成员问候 - {{member}}",
            "trigger": {"entry": "face_seen"},
            "senses": [],
            "brain": {
                "type": "static", "engine": "static_text", "mode": "reminder",
                "prompt": "{{greeting}}", "system": "", "context": [],
                "dedup": False, "max_chars": 80, "mess_slot": False,
            },
            "output": [{"type": "tv_notify"}],
            "limits": {"per_day": 10},
            "on_busy": "drop",
            "role": "butler",
        },
    },
    {
        "id": "tpl_daily_schedule",
        "name": "每日日程播报",
        "category": "schedule",
        "description": "定时播报当天日程安排",
        "icon": "📅",
        "params": [
            {"key": "time", "label": "播报时间", "type": "text", "placeholder": "08:00", "required": True},
            {"key": "member", "label": "成员", "type": "select", "options": ["lidicn", "Kevin", "Emily"], "required": False},
            {"key": "room", "label": "播报房间", "type": "select", "options": ["客厅", "书房", "主卧室"], "required": False},
        ],
        "skill_template": {
            "name": "每日日程播报 - {{time}}",
            "trigger": {"entry": "schedule"},
            "senses": [],
            "brain": {
                "type": "llm", "engine": "llm_text", "mode": "reminder",
                "prompt": "请播报{{member}}今天的日程安排，简洁明了", "system": "", "context": [],
                "dedup": False, "max_chars": 200, "mess_slot": False,
            },
            "output": [{"type": "tv_notify"}],
            "limits": {"per_day": 2},
            "on_busy": "drop",
            "role": "butler",
        },
    },
    {
        "id": "tpl_device_control",
        "name": "设备一键控制",
        "category": "device",
        "description": "触发时控制指定设备（开关/亮度/温度）",
        "icon": "🔌",
        "params": [
            {"key": "entity_id", "label": "设备实体ID", "type": "text", "placeholder": "light.yeelink_lamp", "required": True},
            {"key": "action", "label": "动作", "type": "select", "options": ["turn_on", "turn_off", "toggle"], "required": True},
            {"key": "trigger_event", "label": "触发事件", "type": "select", "options": ["button_pressed", "voice_wake", "face_seen"], "required": True},
            {"key": "confirm_text", "label": "确认提示", "type": "text", "placeholder": "灯已打开", "required": False},
        ],
        "skill_template": {
            "name": "设备控制 - {{entity_id}}",
            "trigger": {"entry": "{{trigger_event}}"},
            "senses": [],
            "brain": {
                "type": "static", "engine": "static_text", "mode": "reminder",
                "prompt": "{{confirm_text}}", "system": "", "context": [],
                "dedup": False, "max_chars": 50, "mess_slot": False,
            },
            # DCD 裁定④-B：⛔ 在这里放 ha_service。output 白名单只有三种，那条写进来
            # 也会被 schema 拒；设备控制走 brain.engine=ha_action（一条路，不留两套）。
            "output": [
                {"type": "tv_notify"},
            ],
            "limits": {"per_day": 50},
            "on_busy": "drop",
            "role": "butler",
        },
    },
    {
        "id": "tpl_security_alert",
        "name": "安防告警",
        "category": "security",
        "description": "检测到异常时推送告警（陌生人/门窗异常）",
        "icon": "🚨",
        "params": [
            {"key": "alert_type", "label": "告警类型", "type": "select", "options": ["陌生人检测", "门窗异常", "烟雾报警"], "required": True},
            {"key": "push_bark", "label": "推送到手机", "type": "checkbox", "required": False},
            {"key": "speak_alert", "label": "音箱播报", "type": "checkbox", "required": False},
        ],
        "skill_template": {
            "name": "安防告警 - {{alert_type}}",
            "trigger": {"entry": "webhook"},
            "senses": [],
            "brain": {
                "type": "static", "engine": "static_text", "mode": "reminder",
                "prompt": "⚠️ {{alert_type}}，请立即检查", "system": "", "context": [],
                "dedup": False, "max_chars": 100, "mess_slot": False,
            },
            "output": [{"type": "bark"}, {"type": "tv_notify"}],
            "limits": {"per_day": 20},
            "on_busy": "queue",
            "role": "butler",
        },
    },
    {
        "id": "tpl_music_play",
        "name": "音乐播放",
        "category": "entertainment",
        "description": "触发时播放指定音乐/歌单",
        "icon": "🎵",
        "params": [
            {"key": "song_name", "label": "歌曲/歌单名", "type": "text", "placeholder": "周杰伦精选", "required": True},
            {"key": "room", "label": "播放房间", "type": "select", "options": ["客厅", "书房", "主卧室"], "required": False},
        ],
        "skill_template": {
            "name": "音乐播放 - {{song_name}}",
            "trigger": {"entry": "voice_wake"},
            "senses": [],
            "brain": {
                "type": "static", "engine": "static_text", "mode": "reminder",
                "prompt": "正在播放{{song_name}}", "system": "", "context": [],
                "dedup": False, "max_chars": 50, "mess_slot": False,
            },
            "output": [{"type": "tv_notify"}],
            "limits": {"per_day": 30},
            "on_busy": "drop",
            "role": "butler",
        },
    },
    {
        "id": "tpl_food_calorie",
        "name": "食物热量分析",
        "category": "health",
        "description": "拍照分析食物热量并播报",
        "icon": "🍎",
        "params": [
            {"key": "camera_id", "label": "摄像头ID", "type": "text", "placeholder": "camera.living_room", "required": True},
            {"key": "member", "label": "关联成员", "type": "select", "options": ["lidicn", "Kevin", "Emily"], "required": False},
        ],
        "skill_template": {
            "name": "食物热量分析 - {{camera_id}}",
            "trigger": {"entry": "button_pressed"},
            "senses": [],
            "brain": {
                "type": "llm", "engine": "llm_text", "mode": "reminder",
                "prompt": "请分析这张食物图片的热量，给出建议", "system": "", "context": [],
                "dedup": False, "max_chars": 300, "mess_slot": False,
            },
            "output": [{"type": "tv_notify"}],
            "limits": {"per_day": 10},
            "on_busy": "drop",
            "role": "butler",
        },
    },
    {
        "id": "tpl_study_reminder",
        "name": "学习提醒",
        "category": "study",
        "description": "定时提醒孩子学习/作业",
        "icon": "📚",
        "params": [
            {"key": "member", "label": "孩子", "type": "select", "options": ["Kevin", "Emily"], "required": True},
            {"key": "time", "label": "提醒时间", "type": "text", "placeholder": "19:00", "required": True},
            {"key": "subject", "label": "科目", "type": "text", "placeholder": "数学作业", "required": False},
        ],
        "skill_template": {
            "name": "学习提醒 - {{member}} {{time}}",
            "trigger": {"entry": "schedule"},
            "senses": [],
            "brain": {
                "type": "static", "engine": "static_text", "mode": "reminder",
                "prompt": "{{member}}，该做{{subject}}了，加油！", "system": "", "context": [],
                "dedup": False, "max_chars": 80, "mess_slot": False,
            },
            "output": [{"type": "tv_notify"}],
            "limits": {"per_day": 5},
            "on_busy": "drop",
            "role": "butler",
        },
    },
    {
        "id": "tpl_morning_routine",
        "name": "晨起例行",
        "category": "routine",
        "description": "早上识别到成员时，自动开设备+播报日程+问候",
        "icon": "🌅",
        "params": [
            {"key": "member", "label": "成员", "type": "select", "options": ["lidicn", "Kevin", "Emily"], "required": True},
            {"key": "time_range", "label": "时间范围", "type": "text", "placeholder": "06:30-08:30", "required": True},
            {"key": "turn_on_tv", "label": "打开电视", "type": "checkbox", "required": False},
            {"key": "tv_channel", "label": "电视频道", "type": "text", "placeholder": "CCTV1", "required": False},
        ],
        "skill_template": {
            "name": "晨起例行 - {{member}}",
            "trigger": {"entry": "face_seen"},
            "senses": [],
            "brain": {
                "type": "llm", "engine": "llm_text", "mode": "reminder",
                "prompt": "早上好{{member}}，今天的日程是...", "system": "", "context": [],
                "dedup": False, "max_chars": 200, "mess_slot": False,
            },
            "output": [{"type": "tv_notify"}],
            "limits": {"per_day": 2},
            "on_busy": "drop",
            "role": "butler",
        },
    },
]

CATEGORY_LABELS = {
    "greeting": "问候",
    "schedule": "日程",
    "device": "设备控制",
    "security": "安防",
    "entertainment": "娱乐",
    "health": "健康",
    "study": "学习",
    "routine": "例行",
}


class SkillTemplateManager:
    """技能模板管理器。"""

    def __init__(self, store, data_dir: str = "/app/data"):
        self.store = store
        self.custom_dir = Path(data_dir) / "skills" / "_templates"
        self.custom_dir.mkdir(parents=True, exist_ok=True)

    def list_templates(self, category: str | None = None) -> list[dict]:
        """列出所有模板（内置+自定义）。"""
        templates = list(BUILTIN_TEMPLATES)
        # 加载自定义模板
        for f in self.custom_dir.glob("*.json"):
            try:
                tpl = json.loads(f.read_text(encoding="utf-8"))
                tpl["source"] = "custom"
                templates.append(tpl)
            except Exception as e:
                logger.warning("load template failed: %s", e)

        if category:
            templates = [t for t in templates if t.get("category") == category]

        for t in templates:
            if "source" not in t:
                t["source"] = "builtin"
            t["category_label"] = CATEGORY_LABELS.get(t.get("category", ""), t.get("category", ""))

        return templates

    def get_template(self, template_id: str) -> dict | None:
        """获取单个模板。"""
        for t in self.list_templates():
            if t["id"] == template_id:
                return t
        return None

    def create_from_template(self, template_id: str, params: dict, skill_id: str | None = None) -> dict:
        """基于模板创建技能。返回创建后的技能。"""
        tpl = self.get_template(template_id)
        if tpl is None:
            raise ValueError(f"template not found: {template_id}")

        # 验证必填参数
        for p in tpl.get("params", []):
            if p.get("required") and p["key"] not in params:
                raise ValueError(f"missing required param: {p['key']}")

        # 渲染模板
        skill_json = json.dumps(tpl["skill_template"], ensure_ascii=False)
        for key, value in params.items():
            skill_json = skill_json.replace("{{" + key + "}}", str(value))

        skill = json.loads(skill_json)
        skill["id"] = skill_id or f"tpl_{template_id}_{int(time.time())}"
        skill["source"] = "user"
        skill["status"] = "draft"  # 创建为草稿，需确认后启用
        skill["approval"] = "approved"
        skill["created_from_template"] = template_id
        skill["template_params"] = params

        # 保存
        self.store.save(skill)
        logger.info("skill created from template: %s -> %s", template_id, skill["id"])
        return skill

    def list_categories(self) -> list[dict]:
        """列出模板分类及数量。"""
        categories = defaultdict_count = {}
        for t in self.list_templates():
            cat = t.get("category", "other")
            if cat not in categories:
                categories[cat] = {"id": cat, "label": CATEGORY_LABELS.get(cat, cat), "count": 0}
            categories[cat]["count"] += 1
        return list(categories.values())
