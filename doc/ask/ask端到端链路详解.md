# ask 节点端到端链路详解

> 来源：原作者 2026-09-18 回复咨询单，整理为正式文档。
> 生态三角：AF（AutoForge，自动化工厂）｜MA（memory-agent，假设来源）｜DB（豆包管家，连接与执行 + 承接 ask 话术）

---

## 关键区分

| | 设计期对话 | 运行期 ask 节点 |
|---|---|---|
| **时机** | Agent 写 IR 时 | 自动化跑起来后 |
| **通道** | DB 聊天（语音/文字） | AF Runtime 挂起 → DB TTS/语音 |
| **用途** | 补全缺设备/条件 | 机器决定不了、必须问人 |
| **在 IR 里** | 不出现 ask 节点 | 出现 ask 节点 |

---

## 六幕链路

### 第 0 幕 · MA 播种假设

MA 从历史行为发现"书房温度 >27℃ 且门关着时，老王 80% 会在 5 分钟内手动开空调"，产出带 conf 的假设：

- conf < 0.60 → 只能出 ask 提案（必须人工确认）
- 0.60–0.85 → shadow（部署但只读比对，不动作）
- >0.85 → 允许 auto + canary

conf 跟着自动化走，决定它"能不能自己动手"。

### 第 1 幕 · 用户对 agent 说话

老王在 DB 的语音/聊天界面说："夏天书房要是太热，帮我开空调——但别自己开，先问我一声。"

DB 把这句话交给 agent，agent 手里挂着 AF 的 MCP 工具（af_*）。

### 第 2 幕 · agent 编写自动化（设计期对话 → IR）

这一步是**设计期对话**，走 DB 聊天通道，不是 ask 节点：

1. agent 调 `af_resolve_entity("书房空调")` → 拿到真实 entity_id
2. agent 发现话没说全，在对话里追问："门开着也开吗？"
3. 老王答："门关着才开。"
4. agent 落成 AF-Spec，编译成 JSON IR：

```json
{
  "id": "q1", "kind": "ask",
  "prompt": "书房 27 度了，要开空调吗？",
  "session": "room", "room": "study", "timeout": "60s"
}
```

q1 有 5 条出边：yes / no / default / on_timeout / on_cancel。

### 第 3 幕 · 两道闸

**第一道 `forge build`（验安全）**：
- 实体存在性 ✓
- q1 有 on_timeout 兜底 ✓（否则 MISSING_TIMEOUT_OR_DEFAULT）
- d1 是 L2 动作（空调）→ 强制 requires_confirm + canary ✓
- 确定性渲染出自然语言给人看

**第二道 `forge sim`（验逻辑，FakeHA 回放）**：
- 推 `sensor.study_temp=28.5` → 实例 SUSPENDED、pending_asks 非空
- 推进虚拟时钟 61s → 走 on_timeout → 静默结束 ✓
- 对照组：`runtime.answer("study","好")` → 开空调 ✓；`answer("bedroom","好")` → 不命中（room 消歧义）✓

### 第 4 幕 · 归档与部署

1. agent 调 `af_save` → 入 PendingOp 待批队列（agent 不能自批自己）
2. 老王在 WebUI 点"批准" → 落盘 v1
3. `forge watch --live` 常驻，订阅 HA SSE 事件流

### 第 5 幕 · 运行期 —— ask 在这里发挥作用（链路核心）

```
HA 上报 sensor.study_temp = 28.5
  → [on a1] 触发 → 建实例（active）
  → [if i1] >27 且门关 → 真
  → [ask q1] ★ 挂起 ★
      instance.state = SUSPENDED
      注册 60s 实例计时器
      pending_asks = { inst-xxx: {room:"study", prompt:"书房 27 度了，要开空调吗？"} }
      ── 求值段到此结束，线程让出 ──
  → DB 承接话术与对话：
      发现挂起 → 在书房音箱 TTS 播报「书房 27 度了，要开空调吗？」
```

按老王反应分四路：

| 老王反应 | DB 动作 | AF 动作 | 结果 |
|---|---|---|---|
| 说"好" | 语音识别 → `POST /api/sessions/{id}/answer {text:"好", room:"study"}` | classify→yes → 走 yes 边 → d1 开空调（L2 带 canary） | 空调开 |
| 说"不用" | 同上，text:"不用" | → no → p2 拒绝结束 | 不开 |
| 说"等一会儿" | 同上 | → default → p1 模糊兜底 | 不动作 |
| 60 秒没人应 | 无需动作 | 计时器到点 → on_timeout → p1 静默 | 不挂起 |
| 应答期间人离开 | 触发中断 | → on_cancel → cancelled，已执行动作不回滚 | 保持原状 |

### 第 6 幕 · 闭环回灌

老王手动开了空调 / 按了"不用" → 作为正/负样本回灌 conf → MA 校正假设 → conf 升/降 → 决定下次是"自己开"还是"只能再问"。

---

## 一句话概括

用户跟 DB 说人话 → agent 用 MCP 把话翻成 IR（设计期对话补全）→ build 验安全 + sim 验逻辑 → 人批准 → watch 常驻执行 → 跑到 ask 挂起 → DB TTS/语音替机器问一句 → 用户一答，answer API 唤醒实例 → 结果回灌 conf。

ask 是这条链上唯一一个"机器停下来等人"的枢纽——它把自动化和人缝合起来，也是 conf 分级（<0.6 必须人确认）与 L2/L3 高危动作人工确认的落地载体。

---

## 缺的那一环：DB↔AF 对接

AF 侧机制已完整（挂起/超时/取消/房间消歧义/answer API）。缺的是：

1. **DB 怎么发现挂起**：轮询 `/api/sessions` 还是 AF 推送？
2. **DB 怎么拿到房间维度**：决定去哪台音箱播报
3. **DB 把答复注回**：`POST /api/sessions/{id}/answer`（文本 + room）

补齐这三件，家庭无人值守场景下 ask 才真正可用。没 DB 时退化为 WebUI 手动回答（低保真闭环，不挂起安全）。
