# Bark 推送全链路梳理

> 文档版本：v1.0 | 更新日期：2026-09-10
> 相关模块：`butler/integrations/bark.py`、`butler/guard/push_guard.py`

## 一、Bark 是什么

Bark 是一款 iOS 推送工具，自部署服务端运行在 NAS（`http://192.168.2.200:18273`），
手机端通过 Bark App 接收推送通知。是豆包管家的**主要文字推送通道**（无条数限制）。

## 二、所有 Bark 推送来源（共 11 处）

| # | 来源模块 | 触发条件 | 频率 | 标题前缀 | group |
|---|---|---|---|---|---|
| 1 | **异常检测** `timeseries/anomaly.py` | 每15分钟定时检查，发现设备异常 | 每15分钟 | `【异常检测】` | anomaly |
| 2 | **设备巡检** `app.py` | 每天 9:00 和 21:00 定时巡检 | 每天2次 | `【设备巡检】` | device_inspection |
| 3 | **主动问询** `proactive/engine.py` | 场景条件触发（如冰箱门未关、久坐等） | 场景触发 | `【主动问询】` | proactive |
| 4 | **晨起播报** `morning/routine.py` | 检测到用户起床后 | 每天1次 | `【晨起播报】` | morning |
| 5 | **决策请示** `api/decision_routes.py` | Agent 需要用户决策时 | 事件触发 | `【决策请示】` | decision |
| 6 | **对话推送** `core/dialog.py` | 多用户对话中需要推送消息时 | 事件触发 | `{角色}·{成员}` | dialog |
| 7 | **TTS 兜底** `tts/manager.py` | TTS 播放失败时 Bark 兜底 | 事件触发 | `豆包管家·{成员}` | tts_fallback |
| 8 | **技能执行** `skills/runner.py` | 技能执行结果需要推送时 | 事件触发 | 无固定前缀 | skill |
| 9 | **提醒技能** `skills/engines/reminder_find/` | 定时提醒触发 | 事件触发 | 自定义 | reminder |
| 10 | **MCP 工具** `mcp/server.py` | 外部 agent 调用 send_bark 工具 | 事件触发 | 自定义 | mcp |
| 11 | **外部 API** `api/bark_routes.py` | 小甜菜/TVPilot/DeskPilot 等外部应用调用 | 事件触发 | 自定义 | 自定义 |

## 三、重点问题来源详解

### 3.1 异常检测（截图中 09:43 连发 4 条 CRITICAL）

**做什么**：每 15 分钟检查一次 HA 设备状态，发现以下异常：
- 关键设备掉线（路由器、网关、NAS、主要音箱等）
- 传感器异常（温度过高/过低、湿度异常等）
- 电池电量低

**推送方式**：汇总为一条 Bark（标题 `【异常检测】发现 N 个问题`，正文列出具体异常）。
有 critical 级别时加小爱 TTS 播报。

**问题**：截图中 09:43 有 4 条 `【异常检测】CRITICAL`，正文都是 "push"。
可能原因：
1. 旧版本代码逐条推送 critical（而非汇总），PushGuard 熔断前发了多条
2. 正文 "push" 是 Bark 服务端显示问题（见第五节）

### 3.2 主动问询 — 冰箱门未关（截图中 10:38、11:38 各一条）

**来源**：`butler/data/proactive_scenes.json` 中的 `fridge_door_open` 场景
**触发条件**：HA 中"冰箱"实体状态为 on 超过 2 分钟
**推送内容**：
- 标题：`【主动问询】冰箱门未关`
- 正文：`冰箱门好像没关，需要我提醒你去关一下吗？`
- 同时小爱 TTS 语音提醒

**问题**：
1. 如果冰箱门传感器误报（或冰箱确实开着），每 15 分钟检查一次会反复推送
2. 应该有冷却机制（同一问题 2 小时内只推一次），但可能没生效
3. 正文 "push" 是 Bark 服务端显示问题

### 3.3 设备巡检（每天 9:00、21:00）

**做什么**：巡检所有 HA 设备在线状态，生成报告推送 Bark（不播 TTS）。
**标题**：`【设备巡检】定时报告`
**注意**：用户之前反馈"69个设备掉线不能推 TTS"，已设置为只推 Bark 不推 TTS。

## 四、PushGuard 风控层

所有 Bark 推送都经过 `PushGuard` 风控检查（v1.8 新增）：

| 风控规则 | 参数 | 说明 |
|---|---|---|
| 单用户突发限制 | 5条/60秒 | 同一 group 60秒内超过5条 → 熔断120秒 |
| 全局过载限制 | 15条/60秒 | 全局60秒内超过15条 → 暂停300秒 |
| 合并缓存 | - | 过载时消息存入 `_merge_cache`，每5分钟批量推送为摘要 |
| 熔断重置 | - | `POST /api/guard/reset` 手动重置 |

**风控 API**：
- `GET /api/guard/status` — 查看风控状态
- `POST /api/guard/reset` — 重置风控
- `GET /api/guard/audit` — 查看推送审计日志

## 五、Bark 正文显示 "push" 问题

### 现象
所有 Bark 推送在手机端通知中，正文第二行都显示 "push"，而不是实际正文内容。

### 排查
1. 管家代码中 `bark.push(body=..., title=...)` 参数传递正确
2. Bark API 返回 200 success
3. 同时发送了 `body` 和 `content` 字段（兼容老版本）
4. **怀疑**：自部署 Bark 服务端版本较老，不支持 JSON POST 的 `/push` 端点的 body 字段，
   而是把 URL 路径中的 `/push` 当成了正文内容显示

### 验证方法
- URL 路径方式：`GET /{key}/{title}/{body}` → 正文应正常显示
- JSON 方式：`POST /{key}/push` + `{"title":"...", "body":"..."}` → 正文可能显示 "push"

### 解决方案（待验证后选择）
1. **方案 A**：改用 URL 路径方式发送（`GET /{key}/{title}/{body}`），兼容性最好
2. **方案 B**：升级 Bark 服务端到最新版本
3. **方案 C**：在 payload 中同时发送 `body`、`content`、`text` 三个字段，覆盖所有可能的字段名

## 六、已知问题与改进建议

| 问题 | 严重度 | 建议 |
|---|---|---|
| Bark 正文显示 "push" | 🔴 高 | 改用 URL 路径方式或升级 Bark 服务端 |
| 异常检测连发多条 CRITICAL | 🟡 中 | 确认汇总推送逻辑生效，增加同一异常冷却时间 |
| 冰箱门未关反复推送 | 🟡 中 | 同一问题 2 小时内只推一次，或只在状态变化时推 |
| 推送来源过多（11处） | 🟡 中 | 在 WebUI 中增加 Bark 推送开关，可按 group 单独禁用 |
| 无推送历史查看 | 🟢 低 | PushGuard 已有审计日志，可在 WebUI 中展示 |

## 七、Bark 配置

- **服务端地址**：`http://192.168.2.200:18273`
- **Key**：`tmLBKcC47VWTW95fYmB5x7`
- **手机端**：Bark App（iOS），也可通过 tailscale IP `100.114.216.94:18273` 访问
- **环境变量**：`BARK_URL`、`BARK_KEY`（在 `.env` 中配置）
