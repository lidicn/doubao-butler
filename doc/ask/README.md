# ask 节点运行期挂起机制（AutoForge 参考实现）

> 豆包管家是应用层 LLM，有自己的对话入口。AutoForge 的 `ask` 节点在运行期挂起
> 等待人类应答的机制，豆包管家可以直接参考——自动化跑到 ask 节点时，
> 豆包管家的 LLM 代为回答，不需要用户手动点按钮。

---

## 一、机制概览

```
触发自动化 → 跑到 ask 节点 → 实例 SUSPENDED（挂起）
    → ask 进入 pending_asks 队列（带 prompt / room / timeout）
    → 外部通过 API 回答（豆包管家 LLM 或用户）
    → 实例恢复，走 yes / no / default 边
    → timeout 无人答 → 走 on_timeout 边
```

---

## 二、IR schema 中 ask 节点定义

来自 `af_ir/models.py`，ask 节点特有字段：

```python
{
  "id": "q1",
  "kind": "ask",
  "prompt": "要关灯吗？",          # 问用户的话术
  "session": "room",               # 会话维度：room / device / user / global
  "room": "书房",                  # 绑定房间（session=room 时用）
  "timeout": "1m"                  # 超时（如 "1m"、"30s"）
}
```

### 出边

| 边 | 触发条件 |
|---|---|
| `yes` | 回答是肯定的（"关"、"好"、"要"） |
| `no` | 回答是否定的（"不关"、"留着"、"不用"） |
| `default` | 回答无法归类（"等一会"、"再说"） |
| `on_timeout` | 超时无人应答 |

---

## 三、Runtime 挂起机制

来自 `af_executor.py`：

```python
# Executor 维护挂起队列
self.pending_asks: dict[str, AskSession] = {}
# AskSession: {instance_id, node_id, room, prompt, created_at}

# 跑到 ask 节点 → 实例进 SUSPENDED
if node.kind == "ask":
    self.pending_asks[instance_id] = AskSession(...)
    instance.state = SUSPENDED
    return instance

# 回答时按 room 匹配
def answer(self, room: str | None, text: str) -> Instance | None:
    candidates = [s for s in self.pending_asks.values() if s.room == room]
    if not candidates:
        return None
    session = min(candidates, key=lambda s: s.created_at)  # 最早的优先
    # 解析 text → yes / no / default
    # 恢复实例，走对应边
```

### 状态机

```
active → suspended（进入 ask/wait）
suspended → active（收到回答或 timeout 到期）
suspended → failed（on_cancel 中断）
```

---

## 四、API 端点

来自 `af_api.py`：

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/sessions` | 建会话：`{ir, seed?, events?}` → 运行到挂起/终态，返回 `asks[]` |
| GET | `/api/sessions` | 列出活跃会话 |
| GET | `/api/sessions/{sid}` | 查会话状态（含挂起的 ask） |
| POST | `/api/sessions/{sid}/answer` | 应答：`{text, ask_id?, room?}` → 唤醒并继续 |
| POST | `/api/sessions/{sid}/tick` | 推进虚拟时钟（sim 演示用） |
| POST | `/api/sessions/{sid}/cancel` | 取消会话 |
| DELETE | `/api/sessions/{sid}` | 删除会话 |

### answer 请求体

```json
{
  "text": "关",
  "room": "书房",
  "ask_id": "instance-uuid"
}
```

### 会话匹配消歧义

- 默认按 `room` 维度匹配：用户在哪个房间应答 → 响应该房间的挂起实例
- 同房间多个挂起 → 按创建时间最早优先
- 一次应答仅生效一次（答完即从 pending_asks 移除）
- 未匹配 → 走 `default` 边

---

## 五、超时处理

- ask 节点声明 `timeout`（如 `"1m"`）
- Scheduler 到点 → 走 `on_timeout` 边（不再走 yes/no）
- 未声明 timeout/default 的 ask → build ERROR（`MISSING_TIMEOUT_OR_DEFAULT`）

---

## 六、豆包管家怎么用

豆包管家有自己的 LLM 常驻，不需要用户手动回答：

1. 自动化跑到 ask 节点 → Runtime 挂起，`asks[]` 出现在会话状态里
2. 豆包管家的 LLM 读到 `prompt` → 根据上下文生成回答（"关" / "留着"）
3. 调 `POST /api/sessions/{sid}/answer` 传入 LLM 的回答
4. Runtime 恢复，走对应边继续

**豆包管家不需要等用户说话，LLM 自己就能回答 ask。**

---

## 七、示例 IR

```json
{
  "ir_version": "0.2.1",
  "id": "shower_off_bathroom_light",
  "name": "洗澡后问用户要不要关卫生间灯",
  "version": 1,
  "mode": "single",
  "nodes": [
    {"id": "t1", "kind": "on", "trigger": {"type": "state", "entity_id": "sensor.shower_power", "to": "off"}},
    {"id": "w1", "kind": "wait", "duration": "5m"},
    {"id": "q1", "kind": "ask", "prompt": "增压泵已停，卫生间灯还亮着，要关灯吗？", "room": "卫生间", "timeout": "1m"},
    {"id": "d1", "kind": "do", "adapter": "ha", "action": "light.turn_off", "params": {"entity_id": "light.bathroom"}},
    {"id": "p1", "kind": "pass"}
  ],
  "edges": [
    {"from": "t1", "to": "w1", "kind": "then"},
    {"from": "w1", "to": "q1", "kind": "then"},
    {"from": "q1", "to": "d1", "kind": "yes"},
    {"from": "q1", "to": "p1", "kind": "no"},
    {"from": "q1", "to": "p1", "kind": "default"},
    {"from": "q1", "to": "d1", "kind": "on_timeout"},
    {"from": "d1", "to": "p1", "kind": "then"}
  ],
  "expect": [{"entity_id": "light.bathroom", "state": "off"}]
}
```

---

## 八、源码位置

| 文件 | 内容 |
|---|---|
| `AutoForge/src/autoforge/af_ir/models.py` | ask 节点字段定义 |
| `AutoForge/src/autoforge/af_executor.py` | pending_asks 队列 + answer() 匹配逻辑 |
| `AutoForge/src/autoforge/af_scheduler.py` | ask 超时派发（on_timeout） |
| `AutoForge/src/autoforge/af_api.py` | /api/sessions/* 端点 |
| `AutoForge/src/autoforge/af_service.py` | 会话生命周期（create/answer/cancel/delete） |
| `AutoForge/docs/IR_AND_RUNTIME.md` §5.2 | 会话匹配消歧义设计 |
