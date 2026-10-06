"""决策层（v0.9）：定时拉起 LLM 阅读多源数据，自主推理并主动行动。

模块构成：
- config.py        决策配置（data/decision.json）
- aggregator.py    数据聚合：设备状态/成员在场/视觉事件/对话/活动 → 摘要
- engine.py        推理 + 安全过滤 + 编排主流程
- action_router.py 行动执行（speak/notify/reminder/suggest_automation）

安全边界（硬约束）：
- 行动白名单：speak / notify / reminder / suggest_automation
- 禁止直接操作设备（无 control_device）
- 冷却：同类行动 30 分钟（cooldown_minutes 可配）
- 夜间 23:00-07:00 只允许 notify
- 置信度低于阈值 → 拒绝执行
"""
