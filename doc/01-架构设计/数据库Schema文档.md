# 豆包管家 数据库 Schema 文档

**版本**: v1.8
**数据库**: SQLite
**文件路径**: `data/butler.db`

---

## 表结构总览

| 表名 | 用途 | 记录量级 |
|------|------|----------|
| dialog_turns | 对话历史 | 万级 |
| chat_logs | 聊天日志 | 万级 |
| dedup_fingerprints | 去重指纹 | 千级 |
| wakeup_log | 唤醒日志 | 千级 |
| notify_history | 推送历史 | 千级 |
| skill_runs | 技能执行记录 | 万级 |
| trigger_runs | 触发器执行记录 | 万级 |
| decision_runs | 决策引擎记录 | 万级 |
| schedules | 日程管理 | 百级 |
| agent_traces | Agent 执行轨迹 | 千级 |
| memory_facts | 记忆事实 | 千级 |

---

## 详细表结构

### 1. dialog_turns - 对话历史

记录每一轮对话（用户说一句 + 管家回一句）。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| ts | REAL | 时间戳（epoch） |
| member | TEXT | 成员 ID（lidicn/kevin/emily） |
| role | TEXT | 角色：butler（管家）/ user（用户） |
| text | TEXT | 对话内容 |
| voice | TEXT | 使用的音色 |
| engine | TEXT | 使用的引擎 |
| duration_ms | INTEGER | 回复耗时（毫秒） |
| llm_ms | INTEGER | LLM 调用耗时 |
| dedup_hit | INTEGER | 是否命中去重（0/1） |
| source | TEXT | 来源：active（主动）/ test（测试）/ proactive（主动） |

**索引**: `(member, ts)`, `(ts)`

---

### 2. chat_logs - 聊天日志

记录所有渠道的聊天记录（小爱/手机/电视）。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| ts | REAL | 时间戳 |
| user_msg | TEXT | 用户说的话 |
| assistant_reply | TEXT | 管家回复 |
| room | TEXT | 房间 |
| role | TEXT | 角色 |
| source | TEXT | 来源：mobile_app / xiaoai / tv_voice |
| tools_called | TEXT | 调用的工具（JSON） |
| success | INTEGER | 成功没（0/1） |
| meta_json | TEXT | 额外信息（JSON） |

**索引**: `(ts)`, `(role, ts)`, `(source)`

---

### 3. dedup_fingerprints - 去重指纹

用于对话去重，避免重复触发相同技能。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| ts | REAL | 时间戳 |
| member | TEXT | 成员 ID |
| text_hash | TEXT | 文本哈希 |
| bigrams | TEXT | Bigrams 数组（JSON） |

**索引**: `(member, ts)`

---

### 4. wakeup_log - 唤醒日志

记录唤醒事件及决策结果。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| ts | REAL | 时间戳 |
| trigger | TEXT | 触发方式：face（人脸）/ timer（定时）/ voice（语音）/ manual（手动） |
| room | TEXT | 房间 |
| member | TEXT | 成员 ID |
| decision | TEXT | 决策结果：pass / cooldown / dnd / muted |
| reason | TEXT | 决策原因 |
| cost_ms | INTEGER | 决策耗时 |

**索引**: `(ts)`

---

### 5. notify_history - 推送历史

记录所有推送通知。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| ts | REAL | 时间戳 |
| member | TEXT | 成员 ID |
| title | TEXT | 推送标题 |
| content | TEXT | 推送内容 |
| type | TEXT | 类型：info / warning / alarm |
| important | INTEGER | 是否重要（0/1） |
| duration | INTEGER | 显示时长（秒） |
| with_tts | INTEGER | 是否带语音（0/1） |
| volume | INTEGER | 音量 |
| pause_media | INTEGER | 是否暂停媒体（0/1） |
| status | TEXT | 状态：ok / fail |
| tts_url | TEXT | TTS 文件 URL |

**索引**: `(ts)`

---

### 6. skill_runs - 技能执行记录

记录每个技能的每次执行。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| ts | REAL | 时间戳 |
| skill_id | TEXT | 技能 ID |
| source | TEXT | 来源：api / mqtt / test |
| status | TEXT | 状态：ok / dry / breaker / daily_limit / dedup / tv_offline / error |
| duration_ms | INTEGER | 执行耗时 |
| text | TEXT | 触发文本 |
| error | TEXT | 错误信息 |
| meta_json | TEXT | 额外信息（JSON） |

**索引**: `(skill_id, ts)`, `(ts)`

---

### 7. trigger_runs - 触发器执行记录

记录自动化触发器的每次执行。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| ts | REAL | 时间戳 |
| trigger_id | TEXT | 触发器 ID |
| event | TEXT | 触发事件 |
| status | TEXT | 状态：ok / no_match / cooldown / error |
| actions_json | TEXT | 执行的动作（JSON） |
| error | TEXT | 错误信息 |

**索引**: `(trigger_id, ts)`, `(ts)`

---

### 8. decision_runs - 决策引擎记录

记录主动感知决策引擎的每次决策。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| ts | REAL | 时间戳 |
| summary | TEXT | 决策摘要 |
| action | TEXT | 动作：no_action / speak / notify / reminder / suggest_automation / filtered |
| room | TEXT | 房间 |
| member | TEXT | 成员 ID |
| text | TEXT | 决策文本 |
| reason | TEXT | 决策原因 |
| confidence | REAL | 置信度（0-1） |
| status | TEXT | 状态：ok / filtered / no_action / error |
| filter_reason | TEXT | 过滤原因 |

**索引**: `(ts)`, `(action, ts)`

---

### 9. schedules - 日程管理

管理成员的日程安排。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| member | TEXT | 成员 ID |
| title | TEXT | 日程标题 |
| description | TEXT | 日程描述 |
| start_time | TEXT | 开始时间（ISO 格式 YYYY-MM-DD HH:MM） |
| end_time | TEXT | 结束时间 |
| location | TEXT | 地点 |
| recurrence | TEXT | 重复：none / daily / weekly / monthly |
| done | INTEGER | 是否完成（0/1） |
| created_at | REAL | 创建时间 |
| updated_at | REAL | 更新时间 |

**索引**: `(member, start_time)`, `(start_time)`

---

### 10. agent_traces - Agent 执行轨迹

记录 ReAct Agent 的完整执行轨迹。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| trace_id | TEXT | 追踪 ID |
| ts | REAL | 时间戳 |
| member | TEXT | 成员 ID |
| source | TEXT | 来源：active / test / api |
| user_text | TEXT | 用户输入 |
| status | TEXT | 状态：ok / timeout / max_iter / error / escaped |
| error | TEXT | 错误信息 |
| total_ms | INTEGER | 总耗时 |
| llm_calls | INTEGER | LLM 调用次数 |
| tool_calls | INTEGER | 工具调用次数 |
| step_count | INTEGER | 步数 |
| steps_json | TEXT | 每一步详情（JSON） |

**索引**: `(trace_id)`, `(ts)`, `(member, ts)`, `(status)`

---

### 11. memory_facts - 记忆事实

v1.1.1 记忆系统，存储提取的事实。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| ts | REAL | 时间戳 |
| fact_type | TEXT | 类型：habit（习惯）/ preference（偏好）/ family（家庭）/ event（事件） |
| content | TEXT | 具体内容 |
| confidence | REAL | 置信度（0-1），默认 0.5 |
| status | TEXT | 状态：pending / approved / rejected |
| source | TEXT | 来源：extraction（提取）/ user_import（导入）/ manual（手动） |
| meta_json | TEXT | 额外信息（JSON） |
| reviewed_ts | REAL | 审核时间 |
| target_role | TEXT | 投喂目标角色（butler / lidicn / kevin / emily） |
| feed_status | TEXT | 投喂状态：pending / fed / changed / failed |
| fed_ts | REAL | 投喂时间 |
| feed_error | TEXT | 投喂错误 |
| fed_content | TEXT | 投喂时内容快照 |

**索引**: `(status)`, `(fact_type)`, `(ts)`, `(target_role)`, `(feed_status)`

---

## 数据清理策略

| 表名 | 保留时间 | 清理方式 |
|------|----------|----------|
| dialog_turns | 90 天 | 自动清理 |
| chat_logs | 90 天 | 自动清理 |
| dedup_fingerprints | 7 天 | 自动清理 |
| wakeup_log | 30 天 | 自动清理 |
| notify_history | 30 天 | 自动清理 |
| skill_runs | 180 天 | 自动清理 |
| trigger_runs | 180 天 | 自动清理 |
| decision_runs | 180 天 | 自动清理 |
| schedules | 永久（完成后清理） | 手动 |
| agent_traces | 30 天 | 自动清理 |
| memory_facts | 永久 | 手动 |

---

## 性能优化建议

1. **定期 VACUUM**：每月执行一次 `VACUUM` 回收磁盘空间
2. **归档冷数据**：超过 180 天的记录归档到只读数据库
3. **监控索引使用率**：定期检查未使用的索引，优化查询性能
4. **批量写入**：批量插入时使用事务，提升写入性能
