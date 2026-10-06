# TVPilot 对接需求 & 豆包管家 Agent 开发方向

> **交接对象**：豆包管家开发者
> **日期**：2026-09-07
> **产品**：TVPilot（原 zap-tv 升级，电视设备控制服务）
> **关联文档**：`mytv_personalized_api_handoff.md`（mytv 换台 API）、`电视语音控制_交接文档.md`

---

## 一、背景与产品定位

### 1.1 什么是 TVPilot

TVPilot（电视领航员）是运行在 NAS 上的**电视设备控制服务**，定位为 **LLM 的"手"**——把 ADB、无障碍、截图、换台等电视操控能力封装成标准化工具接口，供豆包管家（LLM Agent，"脑"）调用。

- **前身**：zap-tv（仅换台功能，Python+ADB，NAS Docker :8090）
- **升级**：从"换台脚本"升级为"设备控制工具服务器"
- **容器名**：暂保留 `zap-tv`，后续逐步迁移为 `tvpilot`
- **访问地址**：
  - 局域网：`http://192.168.2.200:8090`
  - Tailscale HTTPS：`https://fn7t.tailf314d3.ts.net:8090`

### 1.2 脑手分工

```
用户自然语言指令
    │
    ▼
┌──────────────────────────┐
│  豆包管家（脑 / Agent）  │  ← 本文档重点：需要迭代为 ReAct 轻量 Agent
│  理解意图 → 规划步骤     │
│  调用工具 → 观察结果     │
│  错误恢复 → 经验沉淀     │
└──────────┬───────────────┘
           │ 调用标准化工具（HTTP）
           ▼
┌──────────────────────────┐
│  TVPilot（手 / Tool）    │  ← 提供工具接口，不做决策
│  ADB 原语 + 场景动作     │
│  截图 / 前台检测         │
│  换台 / 启动 App         │
└──────────┬───────────────┘
           │ 操作
           ▼
┌──────────────────────────┐
│  电视（红米 75寸）        │
│  mytv / 飞牛TV / 系统UI  │
└──────────────────────────┘

     ┌──────────────────┐
     │  memory-agent    │◄── 经验模板沉淀/检索
     │  操作模板库      │
     └──────────────────┘
```

---

## 二、豆包管家 Agent 开发方向

### 2.1 当前问题

当前豆包管家是**"单轮反射"模式**：

```
用户说一句话 → 管家调一个接口 → 返回结果 → 结束
```

**核心缺陷**：
1. **执行后不看结果**：调完 `/zap` 就返回 ok，但电视真的切过去了吗？不知道
2. **无法处理多步任务**："帮我找《流浪地球2》播放"需要启动 App→搜索→点击，单轮做不完
3. **失败无恢复**：App 没启动、网络卡了、坐标点偏了，管家完全不知道，直接报错给用户
4. **经验不积累**：每次同样的任务都从零开始，不会复用上次成功的操作序列

### 2.2 目标：ReAct 轻量 Agent

**不需要一步到位变成"完整 agent"**，只需要补上 **ReAct 循环**（Reasoning + Acting，推理-行动循环）：

```
用户: "帮我在电视上放《流浪地球2》"

[Thought] 需要打开飞牛TV，搜索电影，然后播放
[Action]  tvpilot.launch_app(package="com.fongmi.android.tv")
[Observation] 截图 → 飞牛TV首页已加载 ✓

[Thought] 点击搜索框
[Action]  tvpilot.tap(x=540, y=180)
[Observation] 截图 → 搜索框已激活 ✓

[Thought] 输入电影名
[Action]  tvpilot.input_text(text="流浪地球2")
[Observation] 截图 → 搜索结果列表出现 ✓

[Thought] 点击第一个搜索结果
[Action]  tvpilot.keyevent(key="KEYCODE_ENTER")
[Observation] 截图 → 视频开始播放 ✓

→ 完成，沉淀操作模板到 memory-agent
```

**核心四步：想 → 做 → 看 → 调整**。这是 LLM Agent 最经典、最轻量的模式，实现成本低，但能解决 80% 的问题。

### 2.3 ReAct Agent 与"完整 Agent"的差距

| 能力 | ReAct 轻量 Agent | 完整 Agent | 电视控制需要吗？ |
|------|------------------|-----------|----------------|
| LLM 推理 | ✅ | ✅ | ✅ |
| 多步规划 | ✅ | ✅ | ✅ |
| 工具调用 | ✅ | ✅ | ✅ |
| 状态观察（截图） | ✅ | ✅ | ✅ **关键** |
| 错误重试/换路径 | ✅ | ✅ | ✅ |
| 经验模板检索 | 可选 | ✅ | 推荐，省 token |
| 长期目标追踪 | ❌ | ✅ | ❌ 电视操作都是短任务 |
| 多 Agent 协作 | ❌ | ✅ | ❌ 不需要 |
| 自主探索新工具 | ❌ | ✅ | ❌ 工具集固定 |
| 情感/人格 | ❌ | ✅ | ❌ |

**结论：ReAct 轻量 Agent 就够了，不需要完整 Agent。**

### 2.4 渐进式迭代路径

```
Phase 1（0-2周）: ReAct 循环（最小可用）
  ├─ 豆包管家增加 Thought→Action→Observation 循环
  ├─ 每步调用 TVPilot 工具后，截图返回给 LLM 判断结果
  ├─ 失败自动重试（最多3次），重试不行换方案
  ├─ 工具集：screenshot / keyevent / tap / launch_app / zap_channel / get_foreground
  └─ 适用场景：换台、启动 App、简单导航

Phase 2（2-4周）: 经验模板
  ├─ 成功的操作序列自动存为模板到 memory-agent
  ├─ 新任务先检索模板，有就参数化直接执行（不截图，省 token）
  ├─ 模板执行失败才回退到 ReAct 自主规划
  ├─ 模板有人工审核/编辑入口
  └─ 适用场景：搜索播放电影、切换信号源、打开特定 App 的特定页面

Phase 3（4周+）: 增强
  ├─ 电视端无障碍服务（控件级操作，替代坐标点击）
  ├─ OCR 文字识别（从截图提取文字，辅助状态判断，减少多模态 token）
  ├─ 语音指令直接触发（不用经过管家文字中转）
  ├─ 多设备协同（电视+音箱+灯光场景联动）
  └─ 更智能的异常处理（识别弹窗、广告、加载中等状态）
```

---

## 三、TVPilot 对接需求

### 3.1 通信方式

| 通道 | 用途 | 说明 |
|------|------|------|
| **HTTP** | 工具调用（主通道） | 请求-响应模式，同步操作，豆包管家调用 TVPilot 工具 |
| **MQTT** | 状态推送（辅通道） | TVPilot 主动推送电视状态变化（开关机、频道变化、前台 App 变化） |

**MQTT 配置**：
- Broker：`tcp://192.168.2.200:1883`
- 用户：`butler`
- Topic 前缀：`tv/livingroom/`
- 状态推送 Topic：`tv/livingroom/status`（retained）
- 结果推送 Topic：`tv/livingroom/result`

### 3.2 工具接口规范（Tool Schema）

TVPilot 提供的每个工具都需要标准化定义，供豆包管家 LLM 理解和调用。

**通用响应格式**：
```json
{
  "ok": true,
  "tool": "tool_name",
  "result": { ... },
  "cost_ms": 123,
  "error": null
}
```

**失败响应**：
```json
{
  "ok": false,
  "tool": "tool_name",
  "error": "error_code",
  "message": "人类可读的错误描述",
  "cost_ms": 123
}
```

### 3.3 原子工具列表（Phase 1 必须实现）

| 工具名 | 方法 | 路径 | 参数 | 说明 | 当前状态 |
|--------|------|------|------|------|---------|
| `screenshot` | GET | `/api/screenshot` | `fmt=json\|png` | 截图，返回 base64 或 PNG | ✅ 已有（zap /screenshot） |
| `get_foreground` | GET | `/api/foreground` | 无 | 获取当前前台包名 | ✅ 已有（zap 内部函数，需暴露 API） |
| `keyevent` | POST | `/api/keyevent` | `key: "KEYCODE_XXX"` | 发送按键 | ✅ 已有 |
| `tap` | POST | `/api/tap` | `x, y` | 点击坐标 | ✅ 已有 |
| `swipe` | POST | `/api/swipe` | `x1,y1,x2,y2,duration` | 滑动 | ❌ 需新增 |
| `input_text` | POST | `/api/input_text` | `text` | 输入文字 | ❌ 需新增 |
| `launch_app` | POST | `/api/launch_app` | `package` | 启动指定 App | ✅ 部分（ensure_mytv，需通用化） |
| `zap_channel` | POST | `/api/zap` | `channel` | mytv 换台 | ✅ 已有（代理到 mytv） |
| `get_current_channel` | GET | `/api/current` | 无 | mytv 当前播放频道 | ✅ 已有（代理到 mytv） |
| `get_channels` | GET | `/api/channels` | 无 | mytv 频道列表 | ✅ 已有（代理到 mytv） |
| `health` | GET | `/api/health` | 无 | 服务健康检查 | ✅ 已有 |

### 3.4 状态观察机制（ReAct 的关键）

豆包管家每步执行后**必须观察状态**，不能盲操作。TVPilot 提供两种观察方式：

#### 方式 A：截图（多模态 LLM 直接看）

```
GET /api/screenshot?fmt=json
→ 返回 { "ok": true, "png_base64": "iVBORw0KGgo..." }
```

豆包管家把 base64 图片传给多模态 LLM，让 LLM 判断当前界面状态。

**优点**：最直观，LLM 直接"看"电视屏幕
**缺点**：token 消耗大，慢

#### 方式 B：结构化状态（轻量，优先使用）

```
GET /api/foreground
→ { "ok": true, "package": "com.fongmi.android.tv", "activity": ".MainActivity" }

GET /api/current
→ { "ok": true, "channel": { "name": "湖南卫视", "no": 24 } }
```

**优点**：快，token 极少
**缺点**：信息有限，无法判断界面细节

**推荐策略**：
- 模板执行时：用方式 B 验证（快）
- 自主规划时：关键节点用方式 A 截图（启动后、输入后、最终验证），中间步骤用方式 B
- 失败排查时：必须用方式 A 截图

### 3.5 场景模板机制（Phase 2）

复杂操作预定义为可复用模板，存到 memory-agent，豆包管家可检索调用。

**模板格式**：
```json
{
  "template_id": "fongmi_search_play",
  "name": "飞牛TV搜索并播放",
  "description": "在飞牛TV中搜索指定关键词并播放第一个结果",
  "tags": ["飞牛TV", "搜索", "播放", "电影", "电视剧"],
  "parameters": {
    "keyword": { "type": "string", "description": "搜索关键词，如电影名/电视剧名" }
  },
  "steps": [
    { "tool": "launch_app", "params": { "package": "com.fongmi.android.tv" } },
    { "tool": "wait", "params": { "seconds": 3 } },
    { "tool": "tap", "params": { "x": 540, "y": 180 } },
    { "tool": "input_text", "params": { "text": "{{keyword}}" } },
    { "tool": "keyevent", "params": { "key": "KEYCODE_ENTER" } },
    { "tool": "wait", "params": { "seconds": 2 } },
    { "tool": "keyevent", "params": { "key": "KEYCODE_ENTER" } }
  ],
  "success_check": {
    "type": "screenshot_contains",
    "description": "截图中出现播放控制栏或视频画面"
  },
  "created_at": "2026-09-07",
  "success_count": 12,
  "fail_count": 1
}
```

**模板执行流程**：
1. 豆包管家收到用户指令 → 向 memory-agent 检索匹配模板
2. 有匹配 → 参数化模板 → 直接执行（不经过 LLM 逐步规划，省 token）
3. 执行成功 → 更新模板 success_count
4. 执行失败 → 回退到 ReAct 自主规划 → 成功后存为新模板或更新旧模板

### 3.6 错误码规范

TVPilot 返回标准化错误码，豆包管家可据此决定重试策略：

| 错误码 | 含义 | 重试策略 |
|--------|------|---------|
| `tv_unreachable` | 电视 ADB 连接失败 | 重试 ADB connect，最多3次 |
| `app_not_responding` | 目标 App 无响应 | 等待2秒后重试，最多2次 |
| `wrong_foreground` | 前台 App 不是预期 | 重新 launch_app |
| `invalid_parameter` | 参数错误（如非法按键名） | 不重试，直接报错给 LLM |
| `operation_timeout` | 操作超时 | 重试1次，仍失败则报错 |
| `mytv_api_error` | mytv HTTP API 错误 | 检查 mytv 是否在运行 |

---

## 四、待讨论决策点

以下问题需要豆包管家开发者与 TVPilot 开发者共同确认：

### 4.1 工具调用通道
- **方案 A**：HTTP 为主（工具调用同步），MQTT 仅状态推送
- **方案 B**：全部走 MQTT（请求/响应都用 MQTT topic）
- **倾向**：方案 A（HTTP 简单直接，MQTT 适合异步状态推送）

### 4.2 截图观察的使用频率
- **方案 A**：每步都截图给 LLM（最稳，但贵且慢）
- **方案 B**：关键节点截图，中间用结构化状态（推荐）
- **方案 C**：模板执行不截图，自主规划才截图
- **倾向**：B+C 混合

### 4.3 无障碍服务 vs ADB 坐标
- **方案 A**：第一期全用 ADB 坐标，第二期再开发无障碍服务
- **方案 B**：直接开发电视端无障碍服务 App，提供控件级操作
- **倾向**：方案 A（先跑通 ReAct 循环，坐标+截图验证够用；无障碍服务作为 Phase 3 增强）

### 4.4 经验模板存储
- 存在 memory-agent（已有对接基础）
- 模板格式：JSON（步骤数组 + 参数 + 成功标志 + 适用场景）
- 检索方式：向量检索（按用户指令语义匹配模板名/描述）
- **需确认**：memory-agent 是否支持模板的存储和向量检索？还是需要新建存储？

### 4.5 豆包管家的 Agent 实现方式
- **方案 A**：自己写 ReAct 循环（轻量，可控）
- **方案 B**：用现成框架（LangChain / AutoGen / CrewAI）
- **倾向**：方案 A（当前场景简单，自己写循环更轻量可控；场景复杂后再考虑框架）

---

## 五、TVPilot 当前已就绪的能力

以下能力已经可以直接对接使用：

| 能力 | 接口 | 验证状态 |
|------|------|---------|
| mytv 换台 | `POST /api/zap` body `{"channel":"湖南卫视"}` | ✅ 已验证 |
| mytv 当前频道 | `GET /api/current` | ✅ 已验证 |
| mytv 频道列表 | `GET /api/channels` | ✅ 已验证（80频道） |
| 截图 | `GET /api/screenshot?fmt=json` | ✅ 已有（zap 原功能） |
| 按键 | `POST /api/keyevent` body `{"key":"KEYCODE_ENTER"}` | ✅ 已有 |
| 点击 | `POST /api/tap` body `{"x":540,"y":180}` | ✅ 已有 |
| PWA 遥控器 | `GET /remote` | ✅ 已部署（HTTPS） |
| MQTT 状态 | `tv/livingroom/status` | ✅ 已连接 |

**待 TVPilot 补充的工具**（Phase 1 内完成）：
- `GET /api/foreground`（前台包名检测，当前是内部函数）
- `POST /api/swipe`（滑动）
- `POST /api/input_text`（文字输入）
- `POST /api/launch_app`（通用 App 启动，当前只支持 mytv）
- 标准化错误码
- 统一 `/api/` 前缀（当前 zap 接口是 `/zap`、`/keyevent` 等，需迁移到 `/api/`）

---

## 六、下一步行动

1. **TVPilot 侧**：补全 Phase 1 原子工具（foreground/swipe/input_text/launch_app），统一 `/api/` 前缀，标准化错误码
2. **豆包管家侧**：实现 ReAct 循环（Thought→Action→Observation），先接入 screenshot + keyevent + tap + launch_app + zap_channel 五个工具
3. **联调测试**：用"打开飞牛TV搜索电影播放"场景端到端测试 ReAct 循环
4. **memory-agent 侧**：确认模板存储方案，设计模板检索接口

---

*本文档由 TVPilot 开发者整理，交豆包管家开发者评审。如有疑问请在文档中批注或直接沟通。*
