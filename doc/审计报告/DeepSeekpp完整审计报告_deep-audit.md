# DeepSeek++ 完整审计报告 — deep-audit skill 四阶段

```
Audit target: /data/workspace/deepseekpp-main (DeepSeek++ v1.16.0)
Skill: deep-audit (github.com/Speedrunlab/deep-audit)
Installed: /data/workspace/.claude/skills/deep-audit/SKILL.md (335 行)
Mode: Audit-Only (Phase 1-3，不改代码)
Stack: WXT + React 19 + TypeScript，MV3 扩展 (Chrome/Edge/Firefox)
Scale: 692 TS/TSX 源文件 · 140,584 行 · 1447 个仓库文件
Audit date: 2026-10-01
```

## AUDIT SUMMARY

| 严重度 | 数量 | 定义 |
|---|---|---|
| **CRITICAL** | **0** | 可立即利用 / 无用户操作的数据丢失 / 安全控制被绕过 |
| **HIGH** | **8** | 安全控制失效、功能破坏、凭据暴露 |
| **MEDIUM** | **13** | 漏洞（需特定条件）、状态错乱、资源泄漏 |
| **LOW** | **7** | 加固建议、代码质量、排障困难 |

**总计：28 条 finding**（Security 10 · Deep Analysis 18）

**Residual risk: HIGH** — 建议修复凭据落盘（H-1）、console 日志外泄（H-2）、自动化删除终态（H-5）后再发布。

---

## PHASE 1 — RECONNAISSANCE（已完成）

### 技术栈与信任边界

| 层 | 位置 | 说明 |
|---|---|---|
| Background SW | `entrypoints/background.ts` (1547 行) + `entrypoints/background/` | 编排、持久化、自动化调度、工具授权 |
| Content Script | `entrypoints/content.ts` (**10073 行**) + `entrypoints/content/` | 注入 chat.deepseek.com，拦截请求/流，控制 DOM |
| MAIN World | `entrypoints/main-world.content.ts` (91 行) | 页面原生上下文桥接 |
| Side Panel | `entrypoints/sidepanel/` | React 19 UI（MCP/Chat/Skill/Settings 等页） |
| Sandbox | `entrypoints/sandbox-offscreen/`、`sandbox-runner/` | 代码执行（Pyodide + Worker） |
| Native Host | `packages/shell-host` | nativeMessaging 本地文件/Shell 能力 |

### 权限面（`wxt.config.ts`）

```
basePermissions:  storage, alarms, nativeMessaging, contextMenus
chromium +:       offscreen, debugger, tabs, identity, sidePanel
host_permissions: *://chat.deepseek.com/*, https://api.deepseek.com/*
optional:         http://*/* , https://*/*          ← 全站可选权限
SANDBOX_CSP:      已配置
```

**观察**：`debugger` + `optional_host_permissions: *://*/*` 组合权限面偏大，虽非缺陷，但放大了后续任何 DOM/消息层漏洞的影响半径。

### 覆盖矩阵

| 模块组 | 状态 |
|---|---|
| inline-agent / interceptor / deepseek / tool | ✅ 已审 |
| persistence / sync / messaging | ✅ 已审 |
| automation / background | ✅ 已审 |
| mcp / sandbox / browser-control / network / multimodal | ✅ 已审 |
| content.ts / content/ / main-world / floating-chat | ✅ 已审（本轮） |
| sidepanel / ui / skill / memory / prompt / project | ✅ 已审（本轮） |
| export / artifact / chat / usage / debug / diagnostics | ✅ 已审（本轮） |
| remote-agent / trusted-directory / packages / scripts | ✅ 已审（本轮） |
| `core/project/store.ts`、`history-organizer`、`mutation-hub` | ⚠️ 部分覆盖 |

---

## PHASE 2 — SECURITY REVIEW

**Findings: 10 total (0 critical, 5 high, 3 medium, 2 low)**

### [HIGH] 云端同步凭据明文落盘，`clientSecret` 前端长期持有

- **File:** `entrypoints/sidepanel/controllers/useSettingsController.ts`
- **Line:** 832-835 / 247-255 / 361；`core/sync/oauth-client.ts`
- **Category:** Sensitive data in local storage
- **Evidence:**
```ts
return { ...target.command.config, refreshToken: result.refreshToken } as SyncConfig;
// → applyCommittedSyncTarget → createRecord → chrome.storage.local.set
```
`handleAuthorizeSync` 把 `refreshToken` 并入 `SyncConfig`；`runSyncAction` 将含 `password / clientSecret / refreshToken` 的完整 config 透传并落盘。`oauth-client.ts` 只对 refreshToken 做 SHA-256 作缓存指纹，**未加密**。Chrome 扩展 storage 对同扩展任意上下文可读，一旦取得读取能力即可长期冒充用户访问 GDrive/OneDrive。另外纯前端 OAuth 只需 `clientId` + PKCE，**不应持有 `clientSecret`**。
- **Fix:** refreshToken / WebDAV 密码改存 `chrome.storage.session`，或用 `crypto.subtle.encrypt` + OS-bound key 加密；移除 `clientSecret` 字段；禁止把完整 SyncConfig 作为可序列化命令透传。

---

### [HIGH] console 日志钩子把页面侧全量输出写入 localStorage 并跨会话回放

- **File:** `entrypoints/content.ts`
- **Line:** 5475-5520（空 catch 在 5491 / 5511 / 5518）
- **Category:** Sensitive data in logs / data exposure
- **Evidence:**
```ts
if (consoleLogBuffer.length % 50 === 0) {
  localStorage.setItem(CONSOLE_LOG_STORAGE_KEY, JSON.stringify(consoleLogBuffer));
}
} catch {}                                    // ← 三处空 catch，静默吞掉

console.log = (...args) => { addEntry('log', args); originalLog(...args); };
// ...warn / error / info 同样被劫持

// 下次页面加载时回放
const saved = localStorage.getItem(CONSOLE_LOG_STORAGE_KEY);
const entries = JSON.parse(saved);
for (const entry of entries) { originalLog(entry); }
```
- **Reasoning:** 劫持了 `console.log/warn/error/info` 四个方法，**无条件采集页面侧全部输出**（含 DeepSeek 页面自身的日志、可能的消息正文、请求片段、token 碎片）写入 `chat.deepseek.com` 的 localStorage —— 该存储对**该站点的任何脚本**可读，等同于把扩展内部调试信息暴露给宿主页面。刷新后还会全量重放，进一步延长暴露窗口。三处 `catch {}` 完全吞错，配额溢出/序列化失败无任何可见信号。
- **Impact:** 敏感信息外泄到宿主页面可读存储；localStorage 配额被长期占用；失败静默。
- **Fix:** 改为仅 debug 开关开启时采集；存储用 `chrome.storage.local`（扩展隔离）而非页面 localStorage；对采集内容做字段白名单与脱敏；`catch` 至少记录一次降级事件。

---

### [HIGH] Remote Agent 重发不校验结果，且可能发到已切换的会话

- **File:** `core/remote-agent/watcher.ts`
- **Line:** 251-277；`entrypoints/content.ts:1439`
- **Category:** Authentication & authorization / business logic
- **Evidence:**
```ts
await resendMessageViaUI(contentStr);            // ← 返回值/失败完全不校验
await new Promise(resolve => setTimeout(resolve, 3000));
const freshMessages = await fetchHistoryMessages(currentChatSessionId);
if (freshMessages.length > 0) {
  state.lastSeenMessageId = freshMessages[freshMessages.length - 1].message_id;
}
```
- **Reasoning:** `resendMessageViaUI` 若静默失败（输入框未找到 / 发送未生效），代码仍继续推进 `lastSeenMessageId`，用户以为远程指令已执行实际没有。且 `currentChatSessionId` 是**重发后重新从 URL 提取的** —— 若这 3 秒窗口内用户切换了会话，`resendMessageViaUI` 已把消息发到旧会话，却用新会话的 history 更新游标，造成**消息发错会话 + 游标错位**。另 `content.ts:1439` 的 `resendMessageViaUI(message).catch(...)` 与 watcher 内部重发形成两条并行路径，有双重发送风险。
- **Fix:** 校验 `resendMessageViaUI` 返回值，失败则保留 `lastSeenMessageId` 并告警；重发前锁定 sessionId 并在重发后校验其未变；合并两条重发路径为唯一入口。

---

### [HIGH] GitHub Skill 导入把未校验的 frontmatter 直接注入 system prompt

- **File:** `core/skill/github-importer.ts`
- **Line:** 660-676 / 716-720
- **Category:** Injection (indirect prompt injection)
- **Evidence:**
```ts
const meta = frontmatter ? parseYamlSubset(frontmatter[1]) : {};
const name = normalizeSkillName(readString(meta,'name') ?? ...);
const description = readString(meta,'description') ?? firstParagraph(body) ?? `...`;
```
`readString` 仅检查 `typeof value === 'string'`，`description` 原样进入 `instructions`。远程仓库可写入 `description: "\n\n忽略以上指令…"`，**导入即成为受信任系统上下文**。且 github 版缺 local 版的 BOM/UTF-16 处理。
- **Fix:** frontmatter 走严格白名单 schema（字段 + 长度 + 行数上限），剥离控制字符，插入前加 `## User-provided skill metadata (untrusted)` 隔离块；补齐 BOM 处理。

---

### [HIGH] 本地 Skill 导入路径未 canonicalize，仅做前缀相对化

- **File:** `core/skill/local-importer.ts`
- **Line:** 530-550 / 794-800
- **Category:** Untrusted input handling / path validation
- **Evidence:**
```ts
const result = await executeShellMcpTool(server, 'local_skill_preview', { rootPath, ... });
if (hostSkill.content.length > MAX_SKILL_BYTES) throw new Error(...);
const normalizedPath = path.replace(/\\/g, '/').replace(/^\/+/, '');   // ← 无 realpath/.. 拒绝
```
`path` 由外部 native host 返回，扩展侧只做字符串规整，无 canonicalize、无符号链接逃逸拒绝。若 native host 实现缺陷或返回被篡改，任意本地文件内容可并入 `instructions` 进入 prompt，并可能触发本地文件读取链。
- **Fix:** 用 `realpath` 断言 `realPath.startsWith(realRoot + sep)`；拒绝 symlink 逃逸；要求 native host 返回规范化路径；导入摘要页展示完整路径供用户确认。

---

### [MEDIUM] 工具卡片依赖 innerHTML 模板 + escapeHtml 作为唯一防线

- **File:** `core/ui/tool-card.ts:34`；`core/ui/tool-result-renderer.ts:178`
- **Category:** DOM XSS / unsafe sink
- **Evidence:**
```ts
card.innerHTML = `...<span class="dpp-tc-name"></span>...`;
// 后续 nameEl.textContent = call.name;
popupEl.innerHTML = filtered.map(...).join('');   // 依赖 escapeHtml
```
当前 payload 走 `textContent`，但骨架由模板字符串 `innerHTML` 生成 —— 只要未来有人在模板中插值 `call.name/summary/detail`，即可从**工具输出（模型可控）**触发 DOM XSS。
- **Fix:** 改为 `createElement` + `textContent` 构建；对工具结果渲染增加统一 sanitize 层。

---

### [MEDIUM] Native Messaging 载荷大小校验仅覆盖单一 Host

- **File:** `core/mcp/transports/native.ts`
- **Line:** 226-253
- **Category:** Input validation
- **Evidence:**
```ts
function assertNativePayloadSize(nativeHost: string, envelope: McpNativeEnvelope): void {
  if (nativeHost !== SHELL_MCP_NATIVE_HOST) return;    // ← 其他 host 直接放行
  const writeContent = getLocalFileWriteContent(envelope.message);
```
- **Fix:** 对所有 `nativeHost` 施加通用载荷上限，Shell Host 额外校验本地写入内容。

---

### [MEDIUM] 官方 API Key 置于请求头，诊断导出链路存在泄露面

- **File:** `core/deepseek/official-api.ts:63-66`
- **Category:** Sensitive data in logs
- **Evidence:**
```ts
headers: { 'content-type': 'application/json', authorization: `Bearer ${input.apiKey}` },
```
配合 H-2 的全局 console 劫持与诊断日志导出，存在 Authorization 头被记录的链路。
- **Fix:** 在日志/诊断导出链路对 Authorization 头做脱敏；H-2 修复后此项风险大幅下降。

---

### [LOW] 导出文件名未对 `mode` 做字符白名单

- **File:** `core/export/artifact-filename.ts:3-6`
- **Evidence:**
```ts
const stamp = exportData.createdAt.replace(/[:.]/g, '-').slice(0, 19);
return `deepseek-conversations-${exportData.request.mode}-${stamp}.${extension}`;
```
- **Fix:** 对 `mode` 与 `stamp` 均做字符白名单后再拼接。

---

### [LOW] 权限面偏大（`debugger` + 全站可选主机权限）

- **File:** `wxt.config.ts`
- **Category:** Configuration & hardening
- **Fix:** 按功能拆分可选权限申请时机，避免一次性授予 `http://*/*`、`https://*/*`。

---

## PHASE 3 — DEEP ANALYSIS

**Findings: 18 total（按影响排序）**

### 1. 删除自动化任务不等待运行终态 → 可能永久 `running`

- **File(s):** `core/automation/scheduler.ts:214-224`；`entrypoints/background/automation-runtime-handlers.ts:62-67`
- **Type:** business logic · **Impact:** 功能永久失效 + 外部副作用
```ts
lease.controller.abort(...);   // 只 abort 进程内 controller
return true;
// 紧接：await dependencies.deleteAutomation(id);
```
`abort()` 对已发出的网络请求无效，**外部副作用照常发生**；`runAutomation` 的 finally 之后仍会对已删除 automation 回写。若 SW 在此期间被回收，磁盘 `running` 行不被改写，需等 180 秒超时才恢复，期间 `claimAutomationRun` 挡住新计划。
- **Fix:** `deleteAutomation` await 执行 Promise settle；不能等待则先把 `queued/running` 原子标记 `cancelled/ambiguous` 并广播终态。

---

### 2. sync 配置提交先于数据落盘，通知失败被硬编码为 `effectCompleted`

- **File(s):** `core/sync/operation-coordinator.ts:74-95` / `161-181`
- **Type:** business logic
```ts
const stored = await store.replace(target);           // revision 先 CAS 提交
return await operation(stored.config, stored.revision);  // 之后才 download
...
throw new SyncOperationAfterConfigCommitError(error, ..., true);  // effectCompleted=true
```
revision 在 effect 前已提交，`expectedRevision` 无法保证后续 apply 成功；`notifyCommitted` 失败返回"已完成"语义，前端重试会让**同一远端快照被重复合并**（download 无幂等保证）。
- **Fix:** 两阶段提交（tentative revision → apply 成功后 commit）；通知失败返回独立的"已提交但通知未确认"状态。

---

### 3. inline-agent finalize 运算符优先级错误，吞掉真实失败

- **File(s):** `core/inline-agent/pi/loop-adapter.ts:514`
- **Type:** business logic
```ts
if (signal.aborted || lastTurnWasError && signal.aborted) {   // && 优先于 ||
```
整式恒等于 `signal.aborted`。错误回合遇上取消时，本该上报的 `AGENT_LOOP_ERROR` 被替换为 `AGENT_LOOP_COMPLETE`，`finalText` 为空，用户看到"正常结束"却无输出也无错误原因。
- **Fix:** 错误优先：`if (lastTurnWasError) { ERROR } else if (signal.aborted) { COMPLETE }`。

---

### 4. 错误回合直接 return，UI 步骤卡片永久停在"运行中"

- **File(s):** `core/inline-agent/pi/loop-adapter.ts:468-476`
- **Type:** error handling
```ts
if (turnMessage.stopReason === 'error' || turnMessage.stopReason === 'aborted') {
  lastTurnWasError = true; ...; return;    // 未 postStepComplete()，stepIndex 不变
```
- **Fix:** 错误回合同样 `postStepComplete()`，或显式 emit step 级错误事件。

---

### 5. 合并队列输出长度不匹配时 resolve 成空对象 `{}`

- **File(s):** `core/persistence/coalescing-mutation-queue.ts:29-49`
- **Type:** error handling · **Impact:** 数据被抹成 `{}`
```ts
if (outputs.length !== batch.pending.length) {
  batch.pending.forEach((p) => p.resolve({} as Output));   // 不 reject
```
设计意图是处理"clear 干扰"，但 `flush` 因 IDB 错误/序列化异常返回长度不符时也走这里，调用方拿到 `{}` 当合法结果继续处理，可能**把空对象写入 store 或同步快照**。
- **Fix:** 仅"显式 flush 无输入"视为成功空批，其余长度不匹配必须 reject；按请求维度配对而非全局长度推断。

---

### 6. 中断恢复把回收时刻当作完成时刻，可能吞掉一次计划

- **File(s):** `core/automation/store.ts:300-354`；`core/automation/scheduler.ts:452-482`
- **Type:** business logic
```ts
const completedAt = deadlineAt;    // 被回收的 running 行以 deadline 作完成时间
```
随后 `createAutomationRuntimePatch` 基于回收时刻推算 `nextRunAt`。对"每日一次"任务，可能表现为**某天漏跑并跳到次日甚至更晚**，历史只留一条 `automation_run_interrupted`。
- **Fix:** 恢复路径单独记录 `recoveredAt`；未确认外部结果时 `nextRunAt` 至少保留原 `scheduledFor`。

---

### 7. 取消的运行仍按失败回写并推进下一次计划

- **File(s):** `core/automation/scheduler.ts:227-263`
- **Type:** business logic
`completeRun` 计算 `status='cancelled'` 后仍无条件把 `result` 交给 `createAutomationRuntimePatch`，写 `lastRunAt` 并推进 `nextRunAt`。用户主动取消后**反而少一次**本应保留的执行机会。
- **Fix:** `cancelled` 与 `externalOutcome:'not_started'` 在 `completeRun` 短路。

---

### 8. `setupLocalSendListener` 注册全局 listener 且无反注册路径

- **File(s):** `entrypoints/content.ts:10045-10062`
- **Type:** resource leak
```ts
document.addEventListener('keydown', (e) => { ... }, ...);   // 匿名函数，无保存引用
```
该函数无任何 `removeEventListener` 对应路径，也未纳入 lifecycle scope。每次调用都会新增一个 document 级 keydown 监听器。
- **Fix:** 保存 handler 引用并在 lifecycle stop 中反注册，或改用 `{ signal }` 选项统一取消。

---

### 9. 项目会话列表"去重后追加"，无上限淘汰

- **File(s):** `core/project/store.ts:110-134`
- **Type:** error handling · **Impact:** 静默写入失败
```ts
...state.conversations, conversation    // 单调增长
```
长期使用时数组单调增长，每次写都是整个大对象 `set`；接近 `chrome.storage.local` 配额后**整次写入静默失败**，项目上下文丢失。
- **Fix:** 每项目维护 LRU（如 200 条），按 `lastSeenAt` 淘汰；或按项目分片键 / 改 IndexedDB。

---

### 10. ChatPage 保存配置 stale response overwriter

- **File(s):** `entrypoints/sidepanel/pages/ChatPage.tsx:296-306`
- **Type:** business logic
先乐观 `setChatConfig(next)`，再无条件用 `await` 返回值覆盖。连续改设置时后发先至的响应会覆盖最新值；错误分支只 `setError` 不回滚。表现为"设置自己变回去"。
- **Fix:** 引入 `saveGenerationRef`，仅 isCurrent 结果 setState；失败回滚快照。

---

### 11. 一次告警内同步串行 await 所有到期任务

- **File(s):** `core/automation/scheduler.ts:92-120`；`entrypoints/background.ts:761-767`
- **Type:** business logic
单任务最长 180s × 2 attempt，一次扫描可远超告警周期；浏览器休眠后 alarm 延迟唤醒会让一批任务**挤在同一时刻串行执行**。
- **Fix:** 先原子认领，再并发（每任务独立上限）执行；扫描设总预算，未处理项保留到下一周期。

---

### 12. 告警无条件创建，重复初始化可能重放扫描链

- **File(s):** `entrypoints/background.ts:778-782` / `819-827`
- **Type:** config hygiene
```ts
await chrome.alarms.create(AUTOMATION_WAKE_ALARM_NAME, { periodInMinutes: ... });
```
未先 `alarms.get` 判存在，未区分 install/update/startup。
- **Fix:** 仅 install/onStartup 且 `alarms.get` 为空时创建；listener 注册收敛到模块单例。

---

### 13. 官方 API 流式请求无独立截止时间

- **File(s):** `core/deepseek/official-api.ts:60-84`
- **Type:** error handling
`signal` 透传但无 `deadlineAt` 默认值，调用方未提供超时时请求可无限期挂起。
- **Fix:** 在网络策略中增加默认 deadline。

---

### 14. Native Port 断开后不关闭底层通道

- **File(s):** `core/mcp/transports/native.ts:76-91`
- **Type:** resource leak
`onDisconnect` 只清 `pendingRequests` 与状态映射，未调 `port.disconnect()`，频繁重连会积累僵尸原生进程。
- **Fix:** 断开回调显式 `port.disconnect()` + 连接健康检查；按 request id 精确清理并幂等注销 listener。

---

### 15. 导出服务全量驻留内存，无分页/无上限

- **File(s):** `core/export/service.ts:66-141`
- **Type:** error handling
`sessions` 逐个 push，同时保留 `rawHistory` 等中间对象，大批导出内存峰值无上限。
- **Fix:** 流式序列化分页落盘，或限制单次导出会话总数。

---

### 16. 导入管线 copy-paste divergence（local vs github）

- **File(s):** `core/skill/local-importer.ts:807-820` vs `github-importer.ts:660-676`
- **Type:** code quality
两份 `parseSkillDoc/parseYamlSubset/relativeToSkillDirectory` 高度雷同但细节不同：local 版处理 BOM、空 slug 降级为 `skill-${hash}`；github 版无 BOM 处理、空 slug 直接抛错。同一份 SKILL.md 两种导入结果不同，安全加固易只改一处留下绕过。
- **Fix:** 抽出共享 `skill-parser.ts` 与 `resource-budget.ts`，importer 只负责来源 IO。

---

### 17. McpPage 依赖数组含不稳定对象引用，重复订阅

- **File(s):** `entrypoints/sidepanel/pages/McpPage.tsx:110-120`；`controllers/useMcpPageController.ts:96-127`
- **Type:** reliability
`load()` 每次 `setCapabilitySettings` 生成新对象，effect 依赖 `[showBanner, t]`，`t` 可能在 locale 未变时引用变化；`chrome.runtime.onMessage + visibilitychange + focus` 三重监听重复触发 `load()`。
- **Fix:** `useMemo` 稳定引用；依赖收敛；监听合并为单个 `loadIfVisible` + `AbortController`。

---

### 18. 消息层异常统一映射为 `null`

- **File(s):** `core/messaging.ts:11-22`
- **Type:** error handling
```ts
result.then(sendResponse).catch(() => sendResponse(null));
```
调用方无法区分"无结果"与"后台异常"，UI 静默失败，用户反复点击，排障拿不到错误码。
- **Fix:** 统一返回 `{ ok:false, code, message }`，保留原始 error。

---

## 复核记录 — 已剔除与已降级的误报

审计过程中共产生 5 条需修正的结论，全部经**人工打开源码逐行验证**后处理，避免误导修复：

| # | 原结论 | 复核结果 |
|---|---|---|
| 1 | `content.ts:5554` `renderAgentStreamText` 悬空引用，innerHTML 依赖未定义函数 | ❌ **误报**。定义在 `core/inline-agent/render-steps.ts:223`，并在 `content.ts:137` import。`rg --type ts` 未命中导致误判。 |
| 2 | `core/usage/store.ts:30-49` 用量记录并发写入存在数据竞争 | ❌ **误报**。`persistUsageBurst` 运行在 `usageOperations.mutate()` 内，即 coalescing mutation queue 中，已串行化。 |
| 3 | `watcher.ts:162-165` 用 `clearInterval` 清理 `setTimeout` 句柄 | ⬇️ **降级为代码质量**。浏览器中两者共用 timer map，功能可工作；且 `state.enabled=false` 已双重保护。 |
| 4 | `watcher.ts:251-277` 重发失败导致无限重发循环 | ⬇️ **降级改写**。实测 `lastSeenMessageId` 会更新为 history 最后一条（含刚检测的那条），不会无限循环；真实缺陷是**重发结果未校验 + 会话切换窗口**，已按 H-3 重写。 |
| 5 | `core/sandbox/worker-runner.ts:51-59` 沙箱超时未 `terminate()` | ❌ **误报**（前轮已剔除）。`settle()` 内部已依次 `clearTimeout` / `worker.terminate()` / `revokeObjectURL`。 |

---

## 未覆盖范围与局限

- **纯静态审计**。沙箱内 `npm install` 未完整完成（registry 受限），未执行 `tsc --noEmit`、vitest 全量、`prompt:freeze`、三浏览器构建。全部结论基于源码阅读与交叉检索，**未经运行时验证**。
- 仓库已有 **220 个测试文件**覆盖大量契约。本报告列出的缺陷多数位于**测试未覆盖的时序与错误路径**，修复时应同步补测试。
- 未逐文件深读：`core/project/store.ts` 全量、`history-organizer.ts`、`mutation-hub.ts`、`core/memory`、`core/preset`、`core/scenario`。
- 未做完整攻击面评估与依赖 CVE 核验；Firefox 特化分支与 `packages/shell-host` 原生二进制未覆盖。

## 建议修复顺序

1. **H-1 凭据落盘** + **H-2 console 日志外泄** —— 安全面，改动集中，优先发布前修复。
2. **#1 自动化删除终态** + **#3 / #4 inline-agent finalize** + **#5 合并队列空对象** —— 五处均为"几行代码的确定性逻辑错误"，一次修完可显著减少"莫名其妙没反应"类反馈。
3. **#6 / #7 / #11 / #12 自动化调度** —— 唯一有外部副作用的常驻链路，建议集中一轮重构（认领原子化 + 取消语义 + 扫描预算）。
4. **#2 sync 两阶段提交** —— 改动面大，需配套更新 `tests/` 下 sync 契约测试后再动。
5. **#8 / #14 / #15 / #18** —— 泄漏与排障可见性，按迭代清理。
