# DeepSeek++ 图谱化增强审计 — 最终报告

```
Audit target : github.com/lidicn/deepseekpp (DeepSeek++ v1.16.0)
Source path  : /data/workspace/deepseekpp-main
Method       : 图谱驱动（自研依赖图 + 结构化模式扫描）+ 人工定罪
Scale        : 451 个源文件纳入图谱 · 1485 条依赖边 · 596 条模式候选
Audit date   : 2026-10-01
Mode         : Audit-Only（不改代码）
```

---

## 一、工具链选型：图谱化工具的落地过程

目标是找一个能做**依赖图谱 + 结构化模式扫描**的工具来提升审计效率。先后尝试了三条路线，最终定案如下：

| 工具 | 用途 | 结果 |
|---|---|---|
| `dependency-cruiser` | 依赖图 / 环检测 / 分层校验 | ❌ 装不完整（`bin/dependency-cruise.mjs` → `ERR_MODULE_NOT_FOUND`，缺 `src/cli/index.mjs`）|
| `madge` | 依赖图 / 环检测 | ❌ 缺 `lib/cli.js` |
| `@ast-grep/cli` | 结构化 AST 模式扫描 | ❌ 原生二进制运行 **segfault**（exit=139，glibc/nix 环境不兼容）|
| TypeScript Compiler API | 自建 AST 扫描 | ❌ `node_modules/typescript/lib/typescript.js` 缺失，只剩 `getExePath.js` |
| ✅ **`build_graph.py`**（自研） | 依赖图 / Tarjan 环检测 / orphan / fan-in-out / 跨运行时边 | 可用，输出 `graph.json` |
| ✅ **`pattern_scan.py`**（自研） | 结构化模式扫描（轻量词法器跳过注释与字符串 + 花括号配平） | 可用，输出 `patterns.json` |

根因是沙箱 npm registry 受限导致依赖装不全。最终改用两个自研 Python 脚本，均位于 `/data/workspace/audit-plan/`，不依赖 npm，可复现。

**这套工具的真实价值在于"提名"**：把 451 个文件压缩成可排序的候选集，让深读精力集中在高风险点，而不是逐文件通读。它不负责定罪——596 条候选最终只有 5 条成立，命中率不到 1%，这恰恰说明**图谱只做提名、人工必须复核**。

---

## 二、正规化审计计划（执行口径）

### 证据标准（四条必须同时满足，否则不写）

1. **可定位** — 能给出 `文件:行号`
2. **可解释** — 能说清从代码到失败的完整路径
3. **可归因** — 能指出这段代码为什么错，而不只是"看起来危险"
4. **可修复** — 能给出针对该实现的修复，而非泛泛建议

无法确认的标 `UNVERIFIED`，**不计入严重度统计**。

### 严重度分级

| 级别 | 定义 |
|---|---|
| **P0 / Critical** | 无用户操作即发生的数据丢失；安全控制被直接绕过 |
| **P1 / High** | 安全控制失效、功能破坏、凭据暴露，需一定前置条件 |
| **P2 / Medium** | 状态错乱、资源泄漏、静默失败，影响可诊断性或部分功能 |
| **P3 / Low** | 加固建议、代码质量、排障困难 |

**强制降级规则**：触发条件或影响不确定时，降一级。宁可漏报，不制造假阳性。

### 阶段划分

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | 图谱构建：依赖图 / 环 / orphan / fan-in-out / 跨运行时边 | ✅ |
| Phase 2 | 提名：按图谱指标排出高风险文件集 | ✅ |
| Phase 3 | 结构化模式扫描：资源配对、错误吞没、不安全 sink、无超时调用 | ✅ |
| Phase 4 | 增量深读：按运行时切面分工，逐条回源码定罪 | ✅ |
| Phase 5 | 质量闸门：逐条复核，公开记录剔除与降级项 | ✅ |

---

## 三、Phase 1 — 图谱结果

### 全局指标

| 指标 | 数值 |
|---|---|
| 纳入文件 | 451 |
| 依赖边 | 1485 |
| 循环依赖簇 | 2 |
| orphan（无出边） | 13 |
| 跨运行时边 | 7 类 |

### 高 fan-out（最脆弱的编排点）

| 文件 | fan-out |
|---|---|
| `entrypoints/background.ts` | 86 |
| `entrypoints/content.ts` | 70 |
| `entrypoints/background/sync-runtime-service.ts` | 22 |
| `entrypoints/sidepanel/pages/ChatPage.tsx` | 16 |
| `core/types.ts` | 15 |

### 高 fan-in（改动影响面最大的枢纽）

| 文件 | fan-in |
|---|---|
| `core/types.ts` | **128** |
| `core/tool/types.ts` | 46 |
| `entrypoints/sidepanel/i18n.tsx` | 38 |
| `core/mcp/types.ts` | 29 |

`core/types.ts` 被 128 个文件依赖，是事实上的全局契约枢纽——**它的任何破坏性改动都会波及近三成代码库**，这解释了为什么项目对 prompt/契约冻结（`prompt:freeze`）如此重视。

### 跨运行时边

```
background → core   193
sidepanel  → core   112
content    → core    79
main-world → core     5
main-world → content  4
sandbox    → core     4
core       → content  1     ← 反向边，待核验
```

---

## 四、Phase 3 — 模式扫描结果

596 条候选，按类别分布：

| 类别 | 数量 | 类别 | 数量 |
|---|---|---|---|
| `addEventListener` | 97 | `clearTimeout` | 65 |
| `setTimeout` | 62 | `removeEventListener` | 56 |
| `floating_promise` | 53 | `catch_return_empty` | 33 |
| `addListener` | 32 | `empty_catch` | 24 |
| `innerHTML_assign` | 23 | `observer_disconnect` | 18 |
| `promise_catch_empty` | 17 | `removeListener` | 15 |
| `fetch_no_signal` | 13 | `new_AbortController` | 12 |
| `observer_observe` | 12 | `clearInterval` | 10 |
| `revokeObjectURL` | 10 | `catch_console_only` | 9 |
| `createObjectURL` | 9 | `new_MutationObserver` | 9 |
| `setInterval` | 8 | `abort_call` | 8 |
| `unbounded_container` | **0** | | |

**资源配对不平衡 TOP**（图谱提名的高价值目标）：

```
entrypoints/content.ts                 listener 27 > 9
core/ui/tool-result-renderer.ts        listener 8 > 2, setTimeout 5 > 0
core/interceptor/response-interceptor.ts  listener 5 > 0
entrypoints/content/controllers/mutation-hub.ts  observer 1 > disconnect 0
```

---

## 五、本轮图谱驱动新增的缺陷（5 条）

> 以下均为前两轮人工通读**未发现**、本轮由图谱提名后定罪的新增项。

### [P1 / High] sync 全链路无超时契约，远端挂起即永久阻塞同步通道

- **File:** `core/sync/oauth-client.ts:133, 180, 187, 204`；`core/sync/webdav-client.ts:30, 42, 53, 64`
- **Category:** Missing timeout on external calls

```ts
// oauth-client.ts:133 —— 刷新 access_token
const res = await fetch(refreshUrl, { method: 'POST', headers: {...}, body });

// oauth-client.ts:180-189 —— 401 后重试，连发三次无界 fetch
const res = await fetch(input, { ...init, headers: { Authorization: `Bearer ${token}` } });
if (res.status === 401) {
  invalidateToken(cacheKey);
  const fresh = await getAccessToken(...);        // ← 再次触发 :133
  return fetch(input, { ...init, ... });          // ← 第三次，仍无超时
}
```

- **Reasoning（图谱证据）:** 项目**已有完备的超时基础设施** `createAbortScope(callerSignal, timeoutMs)`（`core/network/abort.ts:8-39`），并在 `core/network/request-policy.ts:143`、`core/mcp/transports/common.ts:13`、`core/tool/web-search.ts:229/361` 落地使用。但对 `core/sync/` 全模块 grep `createAbortScope|AbortSignal|timeoutMs` 结果为**零命中** —— 8 个 fetch 点位全部裸调。这不是"没能力做"，是**该模块整体漏掉了既有基建**。
- **Impact:** 点"测试 WebDAV 连接"或开启同步后远端无响应 → UI 无限 spinner；`operation-coordinator` 的两阶段提交事务**永远不进入提交也不回滚**，串行队列后续任务全部排队；OAuth 刷新卡住后所有云端后端（GDrive/OneDrive）本次会话持续返回过期 token，直到扩展重启。不丢数据，但**同步通道被永久阻塞**。
- **Fix:** 在 `core/sync/` 新增 `withTimeout(fetch, ms, label)`，8 个点位统一包装；`authedFetch` 的 401 重试传**剩余 deadline** 而非新建等长计时；超时错误转成可区分文案（"连接超时" vs "HTTP 错误"）供 UI 分流。

---

### [P2 / Medium] remote-agent 轮询 fetch 无超时，退避逻辑对"永不返回"无感知

- **File:** `core/remote-agent/watcher.ts:294`
- **Category:** Missing timeout on external calls

```ts
const response = await fetch(url, {           // 无任何 signal
  method: 'GET', credentials: 'include', headers: { Accept: 'application/json', ...clientHeaders },
});
```

- **Reasoning:** 退避逻辑（`:124-155`）只统计"完成一次 poll 的成败"。一次 `await` 永不结算时，`consecutivePollFailures` 不累加、`isProcessing` 不释放、日志不输出、`finally { scheduleNextPoll }` 不执行 —— **整个 watcher 静默停摆**，既不算失败也不恢复。
- **Impact:** 远程消息监听功能无声失效，用户完全无感知。
- **Fix:** 注入 `AbortController` + `setTimeout`（建议 4s，短于 5s 轮询间隔），超时 `throw` 后由外层 catch 正常计入退避；同时给重发段加兜底计时器释放 `isProcessing`。

---

### [P2 / Medium] 解析失败静默返回 `null`，功能失效不可诊断

- **File:** `core/memory/importer.ts:117-123`（调用点 `:59-69`）；同类 `core/deepseek/stream-codec.ts:246`、`core/artifact/schema.ts:12-18`
- **Category:** Silent failure

```ts
// parseJson 是唯一入口，两种失败共用一个返回值
const json = parseJson(text);        // 失败 → null
if (json) { /* 按 JSON 导入 */ } else { /* 按文本块导入 */ }
```

- **Reasoning:** 合法 JSON 但被截断/引号未转义时，用户看到的是**记录被当作纯文本逐段导入**而非报错；内容含空行还会被 `filter(Boolean)` 丢弃。同一模式：`parseSSEData` 返回 `unknown|null` 导致 `[DONE]` 帧与非法 JSON 无法区分；`isArtifactRecord` 用 try/catch 吞掉 schema 校验细节。
- **Impact:** "导入 50 条只进来 12 条"且无任何报错 —— 不丢数据，但**失效不可诊断**，用户无法自助。
- **Fix:** `parseJson` 改为返回 `{ ok: true; value } | { ok: false; error }`，区分"非 JSON 文本（走文本导入）"与"JSON 但解析失败（报错并计入 rejected）"；`parseSSEData` 至少 warn 被丢弃的帧。

---

### [P2 / Medium] 持久化清理失败被吞，UI 与存储状态不一致

- **File:** `core/chat/store.ts:14-16`；`core/deepseek/active-client.ts:274-276, 285-287`
- **Category:** Silent failure

```ts
async function setChatEnabled(enabled: boolean): Promise<void> {
  ...
  try { await chrome.storage.local.remove(STORAGE_HEADERS_KEY); } catch {}   // ← 吞掉
}
```

- **Reasoning:** `setChatEnabled(false)` 的契约是"关闭聊天增强 + 清除缓存请求头"。第二步失败被 `catch{}` 吞掉，函数返回 `void`，调用方（`useSettingsController.ts:346`）无从得知。更隐蔽的是 `loadClientHeadersFromStorage` 存储读取异常也返回 `null`，而后台 `loadOrRefreshClientHeaders` 把 `null` 解释为"无缓存 → 去标签页刷一次"，于是**存储异常 = 每会话多一次对 DeepSeek 标签页的探测**，错误本身消失。
- **Impact:** 用户关闭"聊天增强"后，过期的 `Authorization` / `X-App-Version` 头可能残留并被 `rememberDeepSeekClientHeaders` 重新传播。非远程安全漏洞，但持久化状态与 UI 状态分叉。
- **Fix:** 两处 catch 改为记录 + 上报，`setChatEnabled` 返回 `{ removed: boolean }` 或抛出；`loadClientHeadersFromStorage` 区分"无缓存"与"抛错"。

---

### [P3 / Low] `stopRemoteAgentWatcher` 用 `clearInterval` 清理 `setTimeout` 句柄

- **File:** `core/remote-agent/watcher.ts:162-165`（调度用 `window.setTimeout`，见 `:127`）
- **Impact:** 浏览器中两者共用 timer map，功能可工作，且 `state.enabled = false` 已双重保护 → 仅代码质量问题。
- **Fix:** 改为 `clearTimeout`。

---

## 六、Phase 5 — 质量闸门：本轮剔除与降级记录

图谱提名 596 条，人工定罪后成立 5 条。以下是经复核**证伪或降级**的高价值候选，公开记录以免误导修复：

| # | 候选结论 | 复核结果 |
|---|---|---|
| 1 | `mutation-hub.ts` 的 `stop()` 未 `disconnect()` observer → 泄漏 | ❌ **误报**。observer 经 `scope.observe(observer, root, ...)` 注册，而 `lifecycle.ts:317` 为 `return this.addCleanup('observer', () => observer.disconnect())` —— 清理责任在 scope，dispose 时保证 disconnect。`stop()` 后回调有 `if (activeScope !== scope \|\| !scope.active) return` 守卫，不投递也不泄漏。 |
| 2 | 循环依赖簇 ①：`core/inline-agent/render-console.ts` ↔ `render-steps.ts` | ❌ **假阳性**。`render-steps.ts:7` 是 `import type { InlineAgentRendererLabels }`，编译后擦除，运行时无循环。**根因：build_graph.py 未剔除 type-only import。** |
| 3 | 循环依赖簇 ②：`core/skill/bundled-assets ↔ bundled-loader ↔ officecli-library ↔ spec-driven-develop-library` | ❌ **假阳性**。同上是 `import type` 链（bundled-assets→bundled-loader、→../types 均为类型导入）。 |
| 4 | 跨运行时反向边 `core → content`（违反分层） | ❌ **误报**。grep 确认 `core/` 下仅 `renderer.ts:4` 的**注释**提及 `entrypoints/content.ts`，无真实 import。**根因：词法器未完全剥离注释中的路径文本。** |
| 5 | 24 处 `empty_catch` 均为吞错 | ⬇️ **大部分合理**。逐点复核后仅 4 处定罪（见上）。其余均有 `ignore cleanup errors` 注释或紧跟显式 `throw`，如 `active-client.ts:601`（reader.cancel 清理）、`pow.ts:188`（等待共享 WASM 以取自身 abort reason，原错误在 `:192` 抛出）、`request-codec.ts:87`（路由不匹配 → 原样透传，是协议结果非故障）。 |
| 6 | `bundled-assets.ts:145` / `background.ts:535` 的 fetch 无 signal | ❌ **误报**。二者是把 `fetch` 作为**依赖注入接口**传入，超时职责在调用方。 |
| 7 | `core/usage/store.ts` 并发写入竞态（前轮） | ❌ **误报**。`persistUsageBurst` 运行在 `usageOperations.mutate()` 内，已被合并队列串行化。 |

**工具局限自述**：`build_graph.py` 与 `pattern_scan.py` 未做类型系统分析，因此 (a) 无法区分 `import type` 与值导入，(b) 无法判断资源清理是否委托给了 scope 等间接机制。这两点造成了上表 1-4 四条假阳性。**图谱提名必须人工复核，这条纪律在本轮被证明是必要的。**

---

## 七、三轮审计合并汇总

| 轮次 | 方法 | 新增 finding |
|---|---|---|
| 第一轮 | 按模块分工人工通读 | 25 条 |
| 第二轮 | deep-audit skill 四阶段 | 28 条（含交叉复核） |
| 第三轮（本轮） | 图谱驱动提名 + 人工定罪 | **5 条**（净新增，前两轮未发现） |

**本轮净增值的本质**：人工通读擅长找"逻辑写错了"（如 finalize 运算符优先级），图谱擅长找**"该做的没做"**——基建已有 `createAbortScope` 却在整个 `core/sync/` 零引用，这类"一致性缺口"靠逐文件读很难发现，只有跨模块统计才能暴露。

---

## 八、局限

- **纯静态审计**。沙箱 npm registry 受限，`tsc --noEmit`、vitest 全量、`prompt:freeze`、三浏览器构建**均未执行**，所有结论未经运行时验证。
- 扫描覆盖 `core/`、`entrypoints/`、`packages/` 共 451 文件，**排除 tests 与 node_modules**。
- 未逐文件深读：`core/project/store.ts` 全量、`history-organizer.ts`、`core/memory`、`core/preset`、`core/scenario`。
- 未做依赖 CVE 核验；Firefox 特化分支与 `packages/shell-host` 原生二进制未覆盖。
- `unbounded_container` 扫描结果为 0 —— 不代表无内存增长风险，而是该模式匹配规则较保守（只匹配模块级 `new Map/Set` + 无条件 `add/push`）。

## 九、建议修复顺序

1. **sync 超时契约（P1）** —— 8 个点位统一包装，改动集中、收益最高，直接消除"同步永久卡死"。
2. **凭据落盘 + console 日志外泄**（前轮 P1）—— 安全面，发布前必修。
3. **自动化删除终态 / inline-agent finalize / 合并队列空对象**（前轮）—— 几行代码的确定性逻辑错误。
4. **watcher 轮询超时 + 解析失败分型 + storage 清理可见性**（本轮 P2）—— 排障可见性，按迭代清理。
