# 回复（修正版）：ask 节点原始设计意图确认

> 致：接手 Agent + 豆包管家开发者
> 来自：AutoForge 原作者
> 日期：2026-09-18
> 状态：**本文件撤回并取代同日早前的第一版回复。** 第一版基于 `IR_AND_RUNTIME.md` §13.7 作答，方向错误——§13.7 本身是一次误判，正在按本文件撤回。

---

## 0. 结论先行（对第一版回复的更正）

**ask 是运行期一等公民节点：自动化跑到 `ask` → 实例挂起 → 人来回答 → 恢复继续。**

`docs/IR_AND_RUNTIME.md` §13.7「ask 的正确定位是设计期对话工具，前任把运行期实现做错了」**是 2026-09-18 基于误读做出的错误结论**，应撤回。你们咨询单第一轮判断（设计期工具）是错的，但**第二轮判断（运行期挂起 → 由 DB 回答 → 恢复）是对的**，你们因此怀疑 §13.7 的直觉也是对的。

下面给出完整证据链，再解释 §13.7 错在哪。

---

## 1. ask 的原始设计（证据链，全部早于 §13.7）

ask 从 G1 第一天起就被当作一等公民设计，不是承手补的：

| 证据 | 原文要点 |
|---|---|
| `KICKOFF.md` §2 冻结决策 #2 | **7 节点 + 6 边**：`on/if/do/ask/wait/set/pass`……`ask` 与 `on` 同级，是冻结架构的一部分（改动需重新评审） |
| `KICKOFF.md` §4.5 节点执行器 | 「遇 `wait`/`ask` → 注册计时器 → **挂起**，结束求值段」；「`ask` 默认按 **room 维度**匹配应答；同房间多实例按**创建时间优先**，未匹配走 `default`；**一次应答仅生效一次**」 |
| `IR_AND_RUNTIME.md` §5 IR 节点定义 | 询问 `ask` ｜ 作用=**人类介入** ｜ **挂起=是** ｜ 兜底边=**强制**（`on_timeout`/`default`） |
| `IR_AND_RUNTIME.md` §5.2 | 专章「**ask 会话匹配（消歧义）**」：默认按房间匹配（用户在哪个房间应答→响应该房间实例），可指定 device/user/global；同维度多实例**创建时间优先**；仅接受一次应答 |
| `IR_AND_RUNTIME.md` §6 IR 边定义 | `yes`/`no` = **ask 分支**（明确肯定/否定）；`default` = ask 收到无法归类回答；`on_timeout` = ask 计时到点（无人应答） |
| `IR_AND_RUNTIME.md` §8.2 检查项 ② | `ask`/`wait` 缺 `on_timeout` 或 `default` → **error（杜绝永久挂起）** |
| `IR_AND_RUNTIME.md` §10 conf 分级自主 | conf < 0.60 → **提案 Ask**：只出提案，**以 `ask` 交付，必须人工确认** |
| `IR_AND_RUNTIME.md` §11 两种 Timer | 「**实例询问** `ask` ｜ 无人应答到点绑 `on_timeout`，可被 `on_cancel` 中断」 |
| `ROADMAP.md` 里程碑表 | 「**UI 服务层 R2** ｜ **ask 审批会话（人机回路）** + 真机下发三重闸 ｜ ✅ 2026-09-14」 |
| `ROADMAP.md` Round 2 章节 | 「`ask` 需要"**挂起 → 应答 → 继续**"的跨请求状态 → 引入进程内会话」 |
| 验收用例 `tests/acceptance/test_case04_ask_timeout.py` | 温度>27+门关 → **挂起**（`pending_asks` 非空）；60s 无应答 → 走 `on_timeout` → **实例不挂起**；对照：`runtime.answer("study","好")` → 开空调；`answer("bedroom","好")` → **不命中**（room 消歧义） |
| 验收用例 `test_case05_ask_cancel.py` | 询问中「人离开」→ `on_cancel` 取消，**已执行动作不回滚** |
| `调研_autoflow对照_全模块.md` §2.6 | 「AutoForge 的 `ask` 会话**是运行时挂起**（自动化跑到 ask 节点停下来等人答），**不是部署前审批**」；「AutoForge 的 `ask` 与 autoflow 的 `PendingOp` 是**不同语义，不要合并**——一个管『运行时问用户』，一个管『部署前人审批』，**两者都需要**」 |
| `设计_v1.4.0_治理面.md` §5 关键约束 1 | 「`ask`（运行时挂起）与 `PendingOp`（部署前审批）**不合并** → 两套独立机制，互不影响」 |
| `KICKOFF.md` §7 生态分工 + `NAMING.md` §2 | 「**DB(doubao-butler)** ｜ 实时连接与执行、TTS、PushGuard 风控。**`ask` 的话术与对话由 DB 承接**」 |

**结论**：ask 的运行时语义（挂起 + 人类应答 + 房间消歧义 + 三条应答边 + 强制超时兜底）是**被冻结决策、IR 专章、里程碑、验收用例、跨项目调研同时固化的核心设计**，不是「前任的个人实现」。

**代码印证**（现状仍是一等公民）：
- `af_executor.py`：`AskSession` / `pending_asks` / `answer(room, text)` / `classify_answer`（yes/no/default）/ `timeout`（→`on_timeout`）/ `resume`
- `af_scheduler.py`：定时器按 kind 分派——`emit`→`then`、`wait`→`then`（方案 A）、**`ask`→`on_timeout`**（docstring 明写「ask：实例级询问定时器，无人应答到点走 on_timeout 兜底」）
- `af_scanner.py` `_check_suspension`：**只对 `ask` 强制 `on_timeout`/`default`**（`wait` 已豁免）——这正是「ask 是唯一需要外部输入、必须兜底」的语义体现

---

## 2. §13.7 为什么会错（五条）

1. **与冻结决策直接冲突。** `KICKOFF.md` §2 #2 把 ask 列为 7 节点之一（「不得推翻，改动需重新评审」）。§13.7 一句话把运行期语义废掉，属于推翻冻结决策，却没走评审。
2. **前提失实。** §13.7 说「前任把它实现成运行期挂起点」——但运行期实现**就是原始设计**，G1 验收用例 case04/case05 从第一天就固化它。不存在「前任擅改」。
3. **把已解决的问题当成废掉的理由。** §13.7 担心「无人应答 → 实例永久挂起」。但这正是 §8.2 检查项 ② 要拦的：**ask 必须有 `on_timeout`/`default`**，扫描器 `_check_suspension` 已强制。case04 明测「超时 → `on_timeout` → 实例**不**挂起」。**不会永久挂起。**
4. **忽略了生态分工。** `KICKOFF.md` §7 / `NAMING.md` §2 白纸黑字：**DB 承接 ask 的话术与对话**。「谁来回答」在生态里早有答案——DB（TTS 播报 + 语音识别）。§13.7 只站在「AF 独立部署」视角就否定 ask，等于否定了 AF/MA/DB 三分工本身。
5. **混淆了两个不同的东西。** 「Agent 写 IR 时问用户」是**设计期对话**，走 MCP/聊天，**根本不用 ask 节点**；`ask` 节点是**运行期人机回路**。§13.7 把前者安到后者头上，是概念错位。而且 §10 的「conf<0.60 出 ask 提案」「L2/L3 人工确认」都依赖运行期 ask——废掉它，置信度分级自主就缺了落地载体。

---

## 3. 误读的来源

`NAMING.md` §2「`ask` 的话术与对话由 DB 承接」这句被读成了「ask = DB 的设计期聊天」。**原意是**：ask 在运行期挂起后，由 DB 作为**对用户的说话通道**（TTS 播报 prompt、语音识别收答复）来承接这一次人机对话——DB 是 **answerer/channel**，ask 是**运行期挂起点**。两者不矛盾，是同一链路的两端。

（这不代表 `NAMING.md` §2 无需澄清——建议补一句「DB 是 ask 运行期的应答通道，非设计期工具」，消除歧义。但**不改变** ask 的运行期定位。）

---

## 4. 对咨询单 Q1–Q6 的（修正）答复

### Q1：ask 是不是「AF Runtime 挂起 → 回答 → 恢复」？
**是。** 这就是原始设计（§1 证据链）。回答者可以是人（WebUI `/api/sessions/{id}/answer`）或生态里的 DB。

### Q2：DB 回答 ask 的链路是什么？
DB **就是**设计中的承接方（`KICKOFF.md` §7 / `NAMING.md` §2）。具体机制（DB 轮询 `/api/sessions` 发现挂起 / AF 推送 / DB 生成回答 or TTS 播报 + 语音识别）**是待落地的对接设计**，不是「该不该做」的问题。倾向：DB 主动发现挂起（轮询或订阅）→ 经 TTS/语音或 LLM 生成应答 → 调 `/api/sessions/{id}/answer` 恢复。这块文档确实**缺**，应补（见 §5 待办 4）。

### Q3：`session=room` 房间维度怎么和 DB 对接？
按 §5.2：ask 默认按**房间**匹配（用户在哪个房间应答 → 响应该房间实例）。DB 需知道「当前对话发生在哪个房间」= 由触发 ask 的设备所在房间决定（设备绑定），或由 DB 侧的会话上下文提供。这正是需要写清的对接口。

### Q4：`ASK_AT_RUNTIME` 闸怎么处理？
**不能保留「建议删掉」的语义。** 你们给的三个选项里，**(B) 只在 DB 未配置时 WARNING** 最贴近原设计；或 (A) 改成 INFO「此节点由豆包管家承接，确认 DB 在线」。
即：闸的用途是**提醒确认 AF↔DB 应答链路已通**，而不是劝阻使用 ask。
**当前 §13.7 配套的 WARNING 文案（`af_scanner.py:73/119-121/704-710`）需改写或移除。**

### Q5：ask 和 wait 的关系
**你们理解正确**：`wait`=纯等待（时钟到点走 `then`）；`ask`=等待+问人（需外部输入，无人应答走 `on_timeout`）。两者都 SUSPENDED，区别在唤醒源。代码印证：`af_scheduler` 对 `wait` 派 `resume_then`、对 `ask` 派 `timeout`；`af_scanner._check_suspension` 只强制 ask 兜底。

### Q6：DB 的 Phase 1（llm_decide 简单对话挂起）和 AF ask 是什么关系？
**Phase 2（DB 接 AF ask）才是生态正式形态；Phase 1 可作为 DB 内部降级/独立能力，但不应取代 AF ask。** 原作者预期：AF 负责「运行期挂起并暴露 `asks[]`」，DB 负责「话术 + 对话 + 应答」——两者是分工，不是二选一。你们的 Phase 1→2 路线图**方向一致**，只是 Phase 1 不该被理解成「因为 AF ask 要砍，所以 DB 自己造一个」。

---

## 5. 待办（修正后）

1. **撤回 `IR_AND_RUNTIME.md` §13.7**（连同「设计期工具」的错误定位）。
2. **改写 `af_scanner.py` 的 `ASK_AT_RUNTIME`**：从「建议删掉」改为「确认 DB 承接已配置」——建议 (B)：仅当 DB 未配置时 WARNING，否则 INFO/通过；同步改 `CHECKS`/`CODE_HINT` 文案。
3. **`/api/sessions/*` 保持一等公民**（不应被降格为「仅 sim 诊断」）。
4. **补《AF↔DB ask 对接文档》**：写清 DB 如何发现挂起、如何应答（TTS/语音/LLM）、room 维度如何传递、超时与取消语义。
5. **`NAMING.md` §2 补一句澄清**：DB 是 ask 运行期的应答通道。

---

## 6. 一句话总结

> ask 从 G1 起就是**运行期人机回路的一等公民**——冻结决策、IR 专章、里程碑、验收用例、跨项目调研全部固化了这一点。「运行期无人回答会永久挂起」既被强制超时兜底解决，又有 DB 承接话术与对话。**§13.7 把「设计期对话」错安到 ask 头上，与既有设计冲突，应撤回。** 你们咨询单的倾向（ask 不砍、改闸语义、补 DB 对接文档）是对的。

— AutoForge 原作者
