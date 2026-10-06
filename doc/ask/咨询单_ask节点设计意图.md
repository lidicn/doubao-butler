# 咨询单：ask 节点原始设计意图确认

> 致：AutoForge 原作者
> 来自：接手 Agent + 豆包管家开发者
> 日期：2026-09-18
> 主题：ask 节点在 AF/MA/DB 生态中的定位

---

## 背景

接手后我们对 ask 节点做了一轮判断，结论反复修正，需要跟原作者确认原始设计意图。

### 我们目前掌握的事实

1. **NAMING.md §术语表**明确写：
   > DB（doubao-butler）| 连接与执行，`ask` 的话术与对话由它承接

2. **IR_AND_RUNTIME.md §5.2**设计了完整的会话匹配机制：
   - 按房间消歧义（用户在哪个房间应答 → 响应哪条实例）
   - 同房间多实例按创建时间优先
   - 一次应答仅生效一次
   - 出边 yes/no/default/on_timeout

3. **af_executor.py**实现了 pending_asks 挂起队列 + SUSPENDED 状态机
4. **af_api.py**实现了 `/api/sessions/*` 7 个端点（建/列/查/答/tick/取消/删）

### 我们走过的弯路

- **第一轮判断**：ask 是"设计期对话工具"，Agent 写 IR 时缺设备问用户，不进 IR。
  → 这是错的。IR schema 里 ask 是正式节点，Runtime 完整实现了挂起/恢复。

- **第二轮判断**：ask 是"运行期挂起点"，但自动化部署后无人回答，实例永久 SUSPENDED，
  所以加了 `ASK_AT_RUNTIME` build 闸 WARNING，建议删掉。
  → 这也是错的。NAMING.md 已经写明：ask 的话术与对话由豆包管家（DB）承接。
  在完整生态里，DB 就是那个"回答 ask 的人"。

---

## 要确认的问题

### Q1：ask 节点的原始定位是不是"AF Runtime 挂起 → DB LLM 回答 → 恢复实例"？

即：AF 跑自动化跑到 ask 节点挂起，通过 `/api/sessions/{id}/answer` 把 prompt 推给 DB，
DB 的 LLM（或 TTS 播报 + 语音识别）回答，再调 answer API 恢复实例？

### Q2：DB 回答 ask 的链路是什么？

- DB 主动轮询 `/api/sessions` 发现挂起？
- 还是 AF 通过 webhook / SSE 推给 DB？
- DB 的回答是 LLM 自动生成，还是 TTS 播报给用户、语音识别用户回复？

### Q3：`session=room` 的房间维度怎么和 DB 对接？

比如 ask 节点标 `room=书房`，DB 怎么知道"当前对话发生在书房"？
是通过设备绑定（哪个房间的设备触发的 ask 就标哪个房间）？

### Q4：`ASK_AT_RUNTIME` build 闸怎么处理？

我们加的 WARNING 说"运行期无人回答，建议删掉"。按生态分工，DB 就是回答者，
这个 WARNING 应该：
- (A) 改成 INFO（"此节点由豆包管家回答，确认 DB 在线"）
- (B) 只在 DB 未配置时 WARNING
- (C) 保留 WARNING，因为 AF 独立部署时确实无人回答

### Q5：ask 和 wait 的关系

wait 到期走 then（我们拍板的方案 A），on_timeout 只给 ask。
这个设计是不是意味着：wait 是"纯等待"，ask 是"等待+问人"，
两者都是 SUSPENDED 但 ask 需要外部输入、wait 只需要时钟？

### Q6：豆包管家开发者提的 Phase 1（llm_decide 里加简单对话挂起）和 AutoForge ask 节点是什么关系？

- Phase 1：DB 自己实现简单的"问一句→等回答→执行"
- Phase 2：接 AutoForge 的 ask 节点

这个路线图是不是原作者预期的？还是 DB 应该直接用 AF 的 ask 节点，不需要 Phase 1？

---

## 附：当前 ask 实现状态

| 组件 | 状态 |
|---|---|
| IR schema（prompt/session/room/timeout） | ✅ 已实现 |
| Runtime 挂起（pending_asks / SUSPENDED） | ✅ 已实现 |
| 会话匹配（按 room / 时间优先） | ✅ 已实现 |
| /api/sessions/* 7 端点 | ✅ 已实现 |
| on_timeout 超时派发 | ✅ 已实现 |
| ASK_AT_RUNTIME build 闸 | ✅ 已加（WARNING，待修正语义） |
| DB 对接（谁来调 answer API） | ❓ 待确认 |
| DB 通知机制（轮询 / 推送） | ❓ 待确认 |

---

## 我们倾向的修正

1. **ask 节点不砍**，它是 AF/DB 生态分工的一环。
2. **ASK_AT_RUNTIME WARNING 改语义**：从"无人回答建议删掉"改为"此节点由豆包管家承接，确认 DB 在线"。
3. **补 DB 对接文档**：写清楚 DB 怎么发现挂起、怎么回答、房间维度怎么传。

请原作者确认或纠正以上理解。
