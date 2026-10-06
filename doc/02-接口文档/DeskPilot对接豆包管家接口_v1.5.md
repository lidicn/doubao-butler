# DeskPilot ↔ 豆包管家 接口对接文档

> 版本：v1.5 轻 Agent
> 日期：2026-09-12
> 提出方：豆包管家
> 对象：DeskPilot（DP，192.168.2.201:8765）

---

## 一、架构定位

```
用户（在DP界面说话/打字）
       ↓
DeskPilot（接收输入）
       ↓
POST → 豆包管家 /api/agent/chat
       ↓
豆包管家 LLM（new-api, function calling）
       ↓
   ┌───┴───┐
   ↓       ↓
 HA设备   DeskPilot工具
(灯/空调)  (浏览器/截图/音量)
   └───┬───┘
       ↓
  回复文本推回DP界面
```

**核心原则**：DeskPilot 不自己接 LLM，所有理解/推理走豆包管家。DP 只负责：
1. 接收用户输入（文字/语音）
2. 转发给豆包管家
3. 执行豆包管家调度的桌面工具
4. 展示管家回复

---

## 二、接口列表

### 接口 1：Agent 对话（主接口，必接）

DP 收到用户指令后，调这个接口。管家统一理解意图，决定调什么工具（HA设备/DP桌面工具/天气/记忆），返回自然语言回复。

```
POST http://192.168.2.200:8095/api/agent/chat
```

**请求头**：
```
Content-Type: application/json
Authorization: Bearer <redacted>
```

**请求体**：
```json
{
  "text": "书房太热了",
  "member": "大佬",
  "history": []
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| text | string | 是 | 用户输入原文 |
| member | string | 否 | 成员名（大佬/凯文/爱美丽），默认"朋友" |
| history | array | 否 | 对话历史 `[{"role":"user","content":"..."},{"role":"assistant","content":"..."}]` |

**响应**：
```json
{
  "ok": true,
  "data": {
    "reply": "好的，已帮你打开书房空调，调到26度制冷。",
    "member": "大佬",
    "trace_id": "abc123"
  }
}
```

---

### 接口 2：DP 桌面工具回调（管家→DP）

当管家决定操作电脑时，会回调 DP 的 HTTP 接口执行。DP 需要暴露以下接口：

```
POST http://192.168.2.201:8765/api/v1/pm/send
```

**当前已有**（20260909_003 PM消息管道）：
- `target`: PM / TP / DP
- `message`: 发送的内容
- `press_enter`: 是否按回车

**后续扩展**（v1.5 待加）：
- 桌面操控工具（打开应用/截图/音量/锁屏等）
- 这些工具已在 `butler/core/tools.py` 的 `desk_*` 系列定义好了，DP 只需暴露对应执行接口

---

### 接口 3：纯 LLM 对话（轻量，意图解析用）

如果 DP 只需要纯文本理解（不触发工具），用这个：

```
POST http://192.168.2.200:8095/api/llm/chat
```

**请求体**：
```json
{
  "scenario": "desktop",
  "user": "lidicn",
  "user_msg": "把音量调到50%",
  "system": "你是桌面指令解析器，返回JSON：{\"action\":\"set_volume\",\"params\":{\"value\":50}}"
}
```

---

## 三、已注册的 DP 工具（管家侧 tools.py）

管家已经定义好了以下桌面工具 schema，LLM 会自动选择调用：

| 工具名 | 用途 |
|---|---|
| desk_system_status | 获取系统状态 |
| desk_volume_get | 获取音量 |
| desk_volume_set | 设置音量 |
| desk_music_play | 播放音乐 |
| desk_desktop_screenshot | 桌面截图 |
| desk_desktop_click | 点击坐标 |
| desk_desktop_type | 输入文字 |
| desk_desktop_key | 按键 |
| desk_uia_snapshot | 获取UI控件树 |
| desk_uia_click | 点击UI控件 |
| desk_windows_list | 列出窗口 |
| desk_windows_activate | 激活窗口 |
| send_to_dp | 发消息到DP对话 |

---

## 四、对接步骤

1. **DP 后端加一个调用**：收到用户输入 → POST `/api/agent/chat` → 展示回复
2. **DP 暴露工具执行接口**：接收管家回调的桌面操作请求并执行
3. **对话历史**：DP 维护最近10轮历史，每次请求带上
4. **错误处理**：管家超时/失败时，DP 显示"管家暂时不可用"

---

## 五、与现有方案的区别

| 之前 | 现在（v1.5） |
|---|---|
| DP 自己接 LLM | DP 不接 LLM，走管家 |
| DP 自己解析意图 | 管家统一解析，DP 只执行 |
| 工具分散 | 工具统一注册在管家 tools.py |
| 记忆各管各的 | 管家统一管理记忆 |
