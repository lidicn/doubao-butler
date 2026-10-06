# 调研笔记：HA 集成型 LLM Agent（MCP Assist + HA Agent）

> 调研日期：2026-09-09
> 目的：补豆包管家控制 HA 的短板，学习 token 优化、工具调用、技能持久化
> 项目：[MCP Assist](https://github.com/mike-nott/mcp-assist) + [HA Agent](https://github.com/holger81/ha_agent)

---

## 一、MCP Assist：token 优化的标杆

### 1.1 核心问题

传统 HA 对话助手每次请求都把**全量实体列表**发给 LLM：
- 200+ 设备的家庭 → 12,000+ tokens/次
- 成本高、响应慢、上下文窗口受限

### 1.2 解决方案：MCP 动态实体发现

不发全量实体，而是在 HA 上启动一个 **MCP Server**，暴露以下工具：

| 工具 | 作用 |
|---|---|
| `get_index` | 获取系统结构索引（区域/域/设备类/人员/日历/自动化/脚本），仅 400-800 tokens |
| `discover_entities` | 按类型/区域/域/设备类/状态/名称**按需查找**实体 |
| `get_entity_details` | 获取指定实体的当前状态和属性 |
| `perform_action` | 控制设备 |
| `run_script` | 执行脚本并返回数据 |
| `run_automation` | 手动触发自动化 |
| `list_areas` | 列出所有区域 |
| `list_domains` | 列出实体类型 |
| `set_conversation_state` | 智能多轮跟进 |

### 1.3 工作流程

```
用户："我们家有漏水吗？"
  ↓
LLM 调用 get_index → 看到系统里有 moisture 传感器和 water flow 监控
  ↓
LLM 调用 discover_entities(device_class="moisture")
  → 返回：binary_sensor.bathroom_leak, kitchen_sink_leak, laundry_leak
  ↓
LLM 调用 discover_entities(name_contains="water flow")
  → 返回：sensor.water_flow_rate
  ↓
LLM 调用 get_entity_details 逐个查询
  → 浴室：off, 厨房：off, 洗衣房：on, 水流：2.5 gpm
  ↓
LLM 合成回答："洗衣房漏水传感器检测到水，水流 2.5 加仑/分钟..."
```

### 1.4 Token 对比

| 方法 | Token 用量 |
|---|---|
| 传统（全量 dump） | 12,000+ |
| MCP Assist（动态发现） | ~400 |
| **减少** | **95%** |

### 1.5 智能实体索引（v0.5.0+）

预生成系统结构快照（~400-800 tokens），包含：
- 区域（areas）、域（domains）、设备类（device_classes）
- 人员、日历、区域、自动化、脚本

对于没有标准化 device_class 的实体（自定义集成），用 **LLM 驱动的 gap-filling** 从命名模式推断语义类别。

### 1.6 其他亮点

- **多 Profile**：可运行多个对话 agent，每个用不同模型
- **多语言**：21 种语言，本地化 UI/系统提示/语音检测
- **多轮对话**：维护对话上下文，支持"把厨房那个也关了"
- **响应模式**：None（不追问）/ Smart（相关时追问）/ Always（自然对话）
- **结束词检测**：用户说"bye/thanks/stop"自动结束对话
- **动态模型切换**：配置 UI 改模型立即生效，不用重启
- **实体暴露控制**：只发现暴露给 conversation assistant 的实体

---

## 二、HA Agent：技能学习 + 双通道路由

### 2.1 核心定位

"Your Home Assistant Assist agent — local LLM, real tools, lasting skills."

跟 MCP Assist 的区别：不追求 token 优化，而是追求**技能持久化**和**对话记忆**。

### 2.2 架构

```
你 → STT → HA Agent → TTS → 你
          ↓
     ┌────┴────┐
     ↓         ↓
  Local LLM   MCP Proxy
              ↓
         HA / Mail / News / ...
```

### 2.3 双通道路由（Action / Chat）

| 通道 | 用途 | 模型 |
|---|---|---|
| **action** | 设备控制 | 可选更快/更小的专用模型 |
| **chat** | 其他一切（邮件/新闻/问答） | 主模型 |

设备控制走 action 通道，保证响应速度；复杂问答走 chat 通道，保证推理质量。

### 2.4 技能学习（Skills that learn）

成功的多步工作流可以变成可复用技能：

- **Auto-save off** → agent 会问"要保存为技能吗？"
- **Auto-save on** → 后台自动保存
- 技能可以**绑定自己的 LLM 模型**，否则继承 chat 模型
- 语音管理技能："列出我的技能"、"禁用 XXX"
- 自动化可调用：`ha_agent.enable_skill` / `disable_skill` / `delete_skill` / `list_skills`

### 2.5 对话记忆

- 短对话记忆，支持"把它们也关了"（指代上文控制的设备）
- 可配置历史长度（默认 10 轮）
- 最大工具迭代次数（防止死循环，默认 10）

### 2.6 实时可调

- 从 HA UI 切换 chat/action 模型、路由、技能设置
- 不用重新部署
- 侧边栏 Console：聊天、技能、设置、活动

### 2.7 语音栈

- 可选 LiquidAI 做 STT/TTS，完全本地语音
- 流式回复：文本到达即送 TTS，不用等完整回答

---

## 三、对豆包管家的启示

### 3.1 直接可抄的 3 个点

#### ① MCP 动态实体发现（优先级：P0，省 token）

**当前豆包管家的问题**：控制 HA 设备时，可能把全量实体状态塞给 LLM，或者靠 LLM 猜 entity_id。

**抄法**：
- 在管家侧实现一个"HA 实体索引"（类似 MCP Assist 的 get_index），只存结构不存状态：区域列表、域列表、设备类、人员、自动化、脚本 → ~500 tokens
- 实现"按需发现"工具：`ha_discover_entities(area, domain, device_class, name_contains, state)` → 返回匹配的 entity_id 列表
- 实现"获取状态"工具：`ha_get_states(entity_ids)` → 只查需要的实体
- 实现"执行动作"工具：`ha_action(entity_id, service, data)`

**预期效果**：HA 控制相关的 LLM 调用 token 减少 80-90%。

#### ② 技能自动保存（优先级：P1，对齐已有技能系统）

**当前豆包管家的状态**：已有技能版本管理、技能沙箱、自进化，但技能创建主要靠手动/对话式创建。

**抄法**：
- ReAct 循环中，成功完成多步任务后，自动提取为技能草稿
- 两种模式：询问保存 / 自动保存
- 技能草稿进入自进化引擎的"待优化"队列
- 跟现有的技能版本管理对齐：自动保存的技能标记为 `auto_generated`，版本 0.1

#### ③ Action/Chat 双通道（优先级：P2，响应速度）

**当前豆包管家的状态**：所有 LLM 调用走同一个模型。

**抄法**：
- 设备控制类工具调用（ha_action / tvpilot / deskpilot）走一个更快的模型（如 deepseek-v4-flash）
- 复杂推理/对话/决策走主模型（如 doubao-seed-1-6）
- 在 ReAct 循环中根据下一步要调用的工具类型自动选模型

### 3.2 值得参考但不急的 2 个点

#### ④ 智能多轮 + 结束词检测

MCP Assist 的"响应模式"（None/Smart/Always）和结束词检测（bye/thanks/stop）可以优化对话体验，但豆包管家当前是被动响应模式，优先级低。

#### ⑤ 实体暴露控制

MCP Assist 只发现暴露给 conversation assistant 的实体。豆包管家可以在 WebUI 加一个"HA 实体白名单"，只允许 agent 控制指定设备，提高安全性。这个可以跟 v1.6 模式引擎的权限控制一起做。

### 3.3 豆包管家已经做得更好的地方

| 能力 | MCP Assist / HA Agent | 豆包管家 |
|---|---|---|
| 多端辐射 | 仅 HA 语音/文字 | 电脑+电视+手机 PWA 三端 |
| 角色系统 | 无 | 8 角色各有音色/头像/定位 |
| 主动决策 | 被动响应 | 决策层+触发器+主动推送 |
| 定位引擎 | 无 | 多源融合定位（v1.5） |
| 技能生态 | 简单技能保存 | 技能版本+沙箱+模板+自进化 |
| 多 Agent 协作 | 无 | TP/DP 子项目+工单系统 |

---

## 四、落地建议

### 近期（v1.5-v1.6）

1. **实现 HA 实体索引 + 按需发现工具**（P0）—— 直接省 token，补控制 HA 的短板
2. **ReAct 成功轨迹自动提取技能草稿**（P1）—— 对齐已有技能系统

### 中期（v1.7+）

3. **Action/Chat 模型分流**（P2）—— 设备控制走快模型
4. **HA 实体白名单**（P2）—— 跟模式引擎权限控制一起做

### 不做

- 不做 MCP Server（豆包管家不是 HA 插件，不需要 MCP 协议，直接实现工具即可）
- 不做多 Profile（豆包管家的角色系统已经覆盖了这个需求）
- 不做 21 种语言（中文优先）
