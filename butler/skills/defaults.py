"""内置默认技能定义（首次启动播种到 data/skills/，已存在的不覆盖）。"""
from __future__ import annotations

DEFAULT_SKILLS: list[dict] = [
    {
        "id": "hello",
        "role": "butler",
        "name": "豆包多模态问候",
        "version": 1,
        "enabled": True,
        "trigger": {"entry": "webhook"},
        "senses": [{"type": "camera", "room": "客厅", "max_age_s": 0}],
        "brain": {
            "type": "vlm_compose", "engine": "camera_vlm", "mode": "ma_analyze",
            "prompt": "",
            "system": "",
            "context": ["persona", "member_profile", "memories"],
            "dedup": True, "max_chars": 60, "mess_slot": True,
        },
        "output": [{"type": "tv_notify", "tts": True}],
        "limits": {"per_day": 30},
        "on_busy": "drop",
    },
    {
        "id": "food-calorie",
        "role": "xiaoyue",
        "name": "拍照识别热量",
        "version": 1,
        "enabled": True,
        "push_to_app": True,
        "trigger": {"entry": "webhook"},
        "senses": [{"type": "camera", "room": "客厅", "max_age_s": 0}],
        "brain": {
            "type": "vlm_compose", "engine": "camera_vlm", "mode": "live_vlm",
            "prompt": "这是客厅摄像头画面。请识别画面中的食物：列出食物名称、估计份量，"
                      "并估算总热量（千卡）。如果画面中没有食物，请直接说明没有看到食物。",
            "system": "你是家庭管家里的营养小助手。根据视觉识别结果，用一句不超过50字的"
                      "中文口语化点评（含总热量），亲切不说教。",
            "context": [], "dedup": False, "max_chars": 80, "mess_slot": False,
        },
        "output": [{"type": "tv_notify", "tts": True}],
        "limits": {"per_day": 10},
        "on_busy": "drop",
    },
    {
        "id": "monitor-camera",
        "role": "gu_anheng",
        "name": "家庭监控画面人物分析",
        "version": 1,
        "enabled": True,
        "trigger": {"entry": "webhook"},
        "senses": [
            {"type": "camera", "room": "客厅", "max_age_s": 0},
            {"type": "camera", "room": "起居室", "max_age_s": 0},
            {"type": "camera", "room": "书房", "max_age_s": 0},
        ],
        "brain": {
            "type": "vlm_compose", "engine": "camera_vlm", "mode": "live_vlm",
            "prompt": "你是家庭安防助手「顾安恒」。这是一张家庭监控摄像头画面。请完成安防视角的人物与场景分析：\n"
                      "1. 识别画面中的人物（判断是家庭成员还是陌生人/未识别）；\n"
                      "2. 留意风险点：是否有陌生人闯入、非授权人员出现在私密区域、遗留可疑物品、门窗异常、"
                      "宠物或儿童单独滞留、明火/积水等安全隐患；\n"
                      "3. 先给出一句总体安全结论（安全 / 需关注 / 异常），再列举具体观察到的人与风险点。\n"
                      "保持简洁专业，用中文，不要寒暄。如果画面中无人且无明显异常，直接说明「画面安全，无异常」。",
            "system": "", "context": [], "dedup": False, "max_chars": 200, "mess_slot": False,
        },
        "output": [{"type": "tv_notify", "tts": True}],
        "limits": {"per_day": 60},
        "on_busy": "drop",
    },
    {
        "id": "create_trigger",
        "role": "butler",
        "name": "自我编排（创建规则）",
        "version": 1,
        "enabled": True,
        "trigger": {"entry": "webhook"},
        "senses": [],
        "brain": {
            "type": "trigger_creator",
            "engine": "trigger_creator",
            "max_retry": 2,
        },
        "output": [{"type": "xiaomi_speak"}],
        "limits": {"per_day": 50},
        "on_busy": "queue",
    },
    {
        "id": "decision",
        "role": "butler",
        "name": "决策层心跳",
        "version": 1,
        "enabled": True,
        "trigger": {"entry": "heartbeat"},
        "senses": [],
        "brain": {
            "type": "decision",
            "engine": "decision",
        },
        "output": [],
        "limits": {"per_day": 500},
        "on_busy": "drop",
    },
]
