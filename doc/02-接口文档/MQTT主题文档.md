# 豆包管家 MQTT 主题文档

**版本**: v1.8
**Broker**: `192.168.2.200:1883`
**认证**: username=butler, password=（见 .env）

---

## 主题总览

### 命名规范

```
<前缀>/<模块>/<事件类型>
```

| 前缀 | 说明 |
|------|------|
| `tv/livingroom` | TV 端（小电视）相关 |
| `butler` | 豆包管家核心 |
| `ma` | Memory Agent 相关 |

---

## 订阅主题（管家消费）

### TV 端

| 主题 | QoS | 说明 |
|------|-----|------|
| `tv/livingroom/status` | 1 | TV 在线状态（retained + LWT） |
| `tv/livingroom/face` | 0 | 人脸识别结果 |
| `tv/livingroom/face/fused` | 0 | 融合人脸识别结果 |
| `tv/livingroom/experiment` | 0 | TV 端小爱实验数据 |
| `tv/livingroom/result` | 0 | zap-tv 执行回执 |

### 豆包管家

| 主题 | QoS | 说明 |
|------|-----|------|
| `butler/event/+` | 0 | 通用事件（通配符） |
| `butler/trigger/+` | 0 | 技能 MQTT 触发入口 |
| `butler/dialog/event` | 0 | 对话事件（自身发布，用于 SSE） |

### Memory Agent

| 主题 | QoS | 说明 |
|------|-----|------|
| `ma/presence` | 1 | 成员在场快照（retained） |
| `ma/device-health` | 1 | 设备健康变化 |

---

## 发布主题（管家生产）

### TV 端控制

| 主题 | QoS | 载荷 | 说明 |
|------|-----|------|------|
| `tv/livingroom/cmd/tts` | 1 | `{"url": str, "volume": int}` | TTS 播放 |
| `tv/livingroom/control` | 1 | `{"action":"zap","channel":...}` | 设备控制（zap-tv） |
| `tv/livingroom/cmd/notify` | 1 | - | 预留：通知推送 |

### 豆包管家核心

| 主题 | QoS | 载荷 | 说明 |
|------|-----|------|------|
| `butler/status/state` | 1 | `{"online": bool, "ts": float}` | 在线状态（retained + LWT） |
| `butler/dialog/event` | 0 | - | 对话事件（供 WebUI SSE） |
| `butler/speak/out` | 0 | `{"text": str, "volume": int}` | 统一发声出口（NR 转小爱/Bark） |

### 技能执行

| 主题 | QoS | 载荷 | 说明 |
|------|-----|------|------|
| `butler/skill/{skill_id}/done` | 0 | - | 技能执行完成广播 |

---

## 事件载荷格式

### 人脸识别事件 `tv/livingroom/face`

```json
{
  "name": "lidicn",
  "confidence": 0.95,
  "ts": 1726298400.123
}
```

### TV 状态事件 `tv/livingroom/status`

```json
{
  "state": "online",
  "ts": 1726298400.123
}
```

### 小爱实验事件 `tv/livingroom/experiment`

```json
{
  "type": "xiaoai_listening_end",
  "recognized_text": "现在几点了",
  "interrupted": false,
  "timestamp": 1726298400.123
}
```

### 成员在场事件 `ma/presence`

```json
{
  "members": [
    {"name": "lidicn", "room": "客厅", "confidence": 0.98},
    {"name": "kevin", "room": "书房", "confidence": 0.92}
  ],
  "ts": 1726298400.123
}
```

### 通用事件 `butler/event/{kind}`

```json
{
  "room": "客厅",
  "member": "lidicn",
  "confidence": 0.9,
  "ts": 1726298400.123,
  "data": {}
}
```

---

## 错误码

| 主题 | 错误类型 | 说明 |
|------|----------|------|
| `tv/livingroom/result` | `error` | 设备控制失败 |
| `butler/skill/{id}/done` | `breaker` | 熔断器触发 |
| `butler/skill/{id}/done` | `daily_limit` | 日限额触发 |
| `butler/skill/{id}/done` | `cooldown` | 冷却中 |

---

## 最佳实践

1. **QoS 选择**：
   - 状态类（在线/离线）：QoS 1 + retained
   - 控制类（TTS/设备控制）：QoS 1
   - 数据类（人脸/传感器）：QoS 0

2. **Payload 规范**：
   - 所有 payload 必须是 JSON 格式
   - 必须包含 `ts` 字段（epoch 时间戳）
   - 错误信息用 `error` 字段

3. **安全建议**：
   - MQTT Broker 启用认证（已启用）
   - 生产环境使用 TLS 加密（后续考虑）
   - 定期轮换密码
