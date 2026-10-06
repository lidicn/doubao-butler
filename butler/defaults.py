"""默认 trigger 规则播种。

v0.1：morning_greet（人脸→问候技能），替代原 dialog.on_face 硬编码逻辑。
v0.2：voice_good_morning（语音"早上好"→问候技能），验证 voice_wake 事件链路。
v0.3：morning_lidicn（7-8点看到lidicn→开电视→切台→问候→昨日开发→今日日程），
      验证多技能动作链顺序执行 + 参数模板 + 事件房间过滤。
v0.6：food_calorie_button（按钮→食物热量拍照分析→小爱播报+豆包app推送）。
"""

DEFAULT_TRIGGERS: list[dict] = [
    {
        "id": "morning_greet",
        "name": "人脸主动问候",
        "version": 1,
        "enabled": True,
        "event": "face_detected",
        "conditions": {
            "time_range": "06:00-23:00",
            "member": "",
            "room": "",
        },
        "role": "butler",
        "actions": [
            {"skill": "greet", "params": {"member": "{{event.member}}"}}
        ],
        "cooldown_sec": 300,
        "priority": 10,
    },
    {
        "id": "voice_good_morning",
        "name": "语音早上好问候",
        "version": 1,
        "enabled": True,
        "event": "voice_wake",
        "conditions": {
            "time_range": "05:00-11:00",
            "member": "",
            "room": "",
            "text_contains": ["早上好", "早安", "早啊"],
        },
        "role": "butler",
        "actions": [
            {"skill": "greet", "params": {"member": "{{event.member}}"}}
        ],
        "cooldown_sec": 600,
        "priority": 20,
    },
    {
        "id": "morning_lidicn",
        "name": "早上看到lidicn开电视+打招呼+播报",
        "version": 1,
        "enabled": True,
        "event": "face_detected",
        "conditions": {
            "time_range": "07:00-08:00",
            "member": "lidicn",
            "room": "",
        },
        "role": "butler",
        "actions": [
            {"skill": "tv_turn_on", "params": {"room": "{{event.room}}", "entity": "media_player.xiaomi_rmh1_6103_play_control"}},
            {"skill": "tv_switch_channel", "params": {"room": "{{event.room}}", "entity": "media_player.xiaomi_rmh1_6103_play_control", "channel": "中央一台"}},
            {"skill": "greet", "params": {"member": "{{event.member}}"}},
            {"skill": "daily_dev_summary", "params": {"member": "{{event.member}}"}},
            {"skill": "today_schedule", "params": {"member": "{{event.member}}"}}
        ],
        "continue_on_error": True,
        "cooldown_sec": 600,
        "priority": 30,
    },
    {
        "id": "food_calorie_button",
        "name": "按钮触发食物热量拍照分析",
        "version": 1,
        "enabled": True,
        "event": "button_pressed",
        "conditions": {
            "time_range": "",
            "member": "",
            "room": "客厅",
            "button_id": "food_calorie",
        },
        "role": "xiaoyue",
        "actions": [
            {"skill": "food-calorie", "params": {"room": "{{event.room}}"}}
        ],
        "continue_on_error": True,
        "cooldown_sec": 60,
        "priority": 15,
    },
    {
        "id": "reminder_due",
        "name": "定时提醒到点全屋找人",
        "version": 1,
        "enabled": True,
        "event": "scheduled",
        "conditions": {
            "time_range": "",
            "member": "",
            "room": "",
        },
        "role": "butler",
        "actions": [
            {"skill": "reminder_announce", "params": {"member": "{{event.member}}", "text": "{{event.text}}"}}
        ],
        "continue_on_error": True,
        "exclusive": True,
        "cooldown_sec": 0,
        "priority": 40,
    },
]
