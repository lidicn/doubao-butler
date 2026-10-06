# TVPilot 调用指南（豆包管家版）

> 版本：v0.9.0 | 更新：2026-09-24
> 适用：豆包管家 Agent 通过 HTTP/MQTT 调用 TVPilot 控制电视

---

## 1. 基础信息

| 项目 | 值 |
|------|-----|
| TVPilot 地址 | `http://192.168.2.200:8090` |
| MQTT Broker | `192.168.2.200:1883` |
| 电视 ADB | `192.168.2.238:5555` |
| 健康检查 | `GET /api/health` |

---

## 2. 核心概念

### 2.1 原子工具（Atomic Tools）

单个操作，直接调用 ADB/无障碍：

| 工具 | 说明 | 示例 |
|------|------|------|
| `zap` | 换台（ADB数字键） | `{"channel": "湖南卫视"}` |
| `keyevent` | 发送按键 | `{"key": "KEYCODE_HOME"}` |
| `tap` | 坐标点击 | `{"x": 100, "y": 200}` |
| `swipe` | 滑动 | `{"x1": 100, "y1": 200, "x2": 300, "y2": 400}` |
| `input_text` | 中文输入 | `{"text": "你好"}` |
| `launch_app` | 启动应用 | `{"package": "com.tvcam.mytv"}` |
| `wait` | 等待 | `{"seconds": 2}` |

### 2.2 蓝图（Blueprint）

**预定义的多步操作流程**，把多个原子工具组合成一个复合操作。

**为什么需要蓝图？**
- 避免 agent 每次都要规划"先按HOME，再等1秒"
- 一次调用执行多步，减少往返
- 成功后自动记录经验，可复用

**内置蓝图：**

| 蓝图名 | 描述 | 参数 | 步骤 |
|--------|------|------|------|
| `go_home` | 回到桌面 | 无 | HOME → wait 1s |
| `zap_to` | 换台到指定频道 | `channel` (必填) | zap → wait 2s |
| `open_settings` | 打开设置 | 无 | SETTINGS → wait 1.5s |

**自定义蓝图：** 可通过 API 动态注册，重启后丢失（需持久化请存配置）。

### 2.3 经验库（Experience Store）

**每次蓝图执行后自动记录**，包含：
- 任务名、步骤序列
- 成功/失败、耗时
- 时间戳

**用途：**
- 查询历史成功率
- 选择最优方案（成功率高 + 耗时短）
- 清理 90 天前的旧经验

### 2.4 任务规划器（Task Planner）

**把自然语言任务拆解为多步操作序列**，目前用规则+模板，预留 LLM 接口。

---

## 3. API 调用指南

### 3.1 健康检查

```
GET /api/health
```

返回：电视在线状态、ADB连接、当前前台App、频道数等。

### 3.2 原子工具调用

```
POST /api/zap
Body: {"channel": "湖南卫视", "request_id": "xxx"}

POST /api/keyevent
Body: {"key": "KEYCODE_VOLUME_UP"}

POST /api/tap
Body: {"x": 500, "y": 300}

POST /api/input_text
Body: {"text": "你好世界"}

POST /api/launch_app
Body: {"package": "com.tvcam.mytv"}

POST /api/wait
Body: {"seconds": 2}
```

### 3.3 组合操作

```
POST /api/combo/go_home          # 回桌面
POST /api/combo/trim_search_play # 飞牛TV搜索播放
Body: {"keyword": "电影名"}
```

### 3.4 蓝图 API

**列出所有蓝图：**
```
GET /api/blueprint/list
```

**执行蓝图：**
```
POST /api/blueprint/run
Body: {
  "name": "zap_to",
  "params": {"channel": "湖南卫视"},
  "request_id": "xxx"
}
```

返回：
```json
{
  "ok": true,
  "result": {
    "name": "zap_to",
    "ok": true,
    "steps": [
      {"step": "zap", "ok": true, "status": "ok", "duration_ms": 9822},
      {"step": "settle", "ok": true, "status": "ok", "duration_ms": 2000}
    ],
    "total_duration_ms": 11822
  }
}
```

**注册自定义蓝图：**
```
POST /api/blueprint/register
Body: {
  "name": "volume_up_3",
  "description": "音量加3次",
  "steps": [
    {"tool": "keyevent", "args": {"key": "KEYCODE_VOLUME_UP"}, "name": "vol1"},
    {"tool": "wait", "args": {"seconds": 0.2}, "name": "wait1"},
    {"tool": "keyevent", "args": {"key": "KEYCODE_VOLUME_UP"}, "name": "vol2"}
  ]
}
```

### 3.5 经验库 API

**查询经验：**
```
GET /api/experience/query?task=blueprint:zap_to&k=5
```

**记录经验：**
```
POST /api/experience/record
Body: {
  "task": "自定义任务",
  "steps": [{"tool": "keyevent", "key": "KEYCODE_HOME"}],
  "success": true,
  "duration_ms": 500
}
```

**清理旧经验：**
```
POST /api/experience/prune
Body: {"max_age_days": 90}
```

### 3.6 任务规划器 API

```
POST /api/planner/run
Body: {"task": "打开设置然后回到桌面"}
```

### 3.7 状态查询

```
GET /api/current          # 当前播放状态
GET /api/channels         # 频道列表
GET /api/foreground       # 当前前台App
GET /api/screenshot       # 截图
GET /api/ocr              # OCR文字识别
GET /api/history          # 操作历史
```

### 3.8 无障碍服务

```
GET /api/a11y/tree        # 获取控件树
GET /api/a11y/find?text=设置  # 按文本查找控件
POST /api/a11y/click      # 按文本/ID点击
Body: {"text": "确定"}
```

---

## 4. MQTT 通信

### 4.1 订阅主题

| 主题 | 说明 |
|------|------|
| `tv/livingroom/zap` | 换台指令 |
| `tv/livingroom/control` | 通用控制 |
| `tv/livingroom/wake` | 唤醒指令 |

### 4.2 消息格式

```json
{
  "action": "zap",
  "channel": "湖南卫视",
  "request_id": "xxx"
}
```

---

## 5. 错误码

| 错误码 | 说明 |
|--------|------|
| `invalid_parameter` | 参数无效 |
| `wrong_foreground` | 前台App不匹配 |
| `operation_timeout` | 操作超时 |
| `tv_unreachable` | 电视不可达 |
| `channel_no_number` | 频道无台号 |

---

## 6. Agent 最佳实践

### 6.1 优先用蓝图，不要重复造轮子

**不好的做法：**
```
agent: 先按HOME，等1秒，再启动IPTV，等5秒，再发数字键...
```

**好的做法：**
```
agent: 调用蓝图 zap_to(channel="湖南卫视")
```

### 6.2 先查经验，再执行

```
1. 查询经验库：GET /api/experience/query?task=换台
2. 如果有成功率>80%的经验，直接用对应蓝图
3. 如果没有，用规划器生成步骤
4. 执行后自动记录经验
```

### 6.3 操作前检查状态

```
1. GET /api/health — 确认电视在线
2. GET /api/foreground — 确认当前App
3. 执行操作
4. GET /api/current — 确认操作结果
```

### 6.4 失败处理

```
1. 操作失败 → 记录错误码
2. 重试一次（最多2次）
3. 仍失败 → 回退到原子工具
4. 记录失败经验，避免重复
```

---

## 7. 常见场景示例

### 场景1：语音换台

```
用户："我要看湖南卫视"
Agent：
  1. POST /api/blueprint/run {"name": "zap_to", "params": {"channel": "湖南卫视"}}
  2. 返回："已切换到湖南卫视，耗时12秒"
```

### 场景2：打开设置

```
用户："打开电视设置"
Agent：
  1. POST /api/blueprint/run {"name": "open_settings"}
  2. 返回："已打开设置"
```

### 场景3：音量调节

```
用户："声音大一点"
Agent：
  1. POST /api/keyevent {"key": "KEYCODE_VOLUME_UP"}
  2. 重复3次
  3. 返回："音量已调大"
```

### 场景4：查询当前播放

```
用户："现在在放什么？"
Agent：
  1. GET /api/current
  2. 返回："当前播放：CCTV-1，节目：新闻联播"
```

---

## 8. 待集成功能

- [ ] 蓝图持久化（重启不丢失）
- [ ] 无障碍工具注册到蓝图工具表
- [ ] LLM 规划器接入（目前规则+模板）
- [ ] 界面地图学习（自动构建App操作路径）
- [ ] 透明悬浮窗交互（ArcFace实验中）

---

## 9. 联系方式

- TVPilot 开发者：TVPilot 团队
- 问题反馈：通过 PM 工单系统
- 文档更新：随版本迭代
