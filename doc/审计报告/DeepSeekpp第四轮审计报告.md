# DeepSeek++ 第四轮审计 — 工作流优化 + 增量审计

```
Audit target : github.com/lidicn/deepseekpp (DeepSeek++ v1.16.0)
Toolkit      : /data/workspace/audit-kit（本轮完成整合与优化）
Pipeline     : graph → scan → triage → 工单 → 人工定罪 → 复核 → 回填
Audit date   : 2026-10-04
Mode         : Audit-Only（不改代码）
```

---

## 一、本轮做了什么

两件事：**先把工作流优化到位，再跑第四轮增量审计。**

### 1.1 工作流整合（已完成）

把前三轮散落的资产整成一条可复现流水线：

| 组件 | 位置 | 职责 |
|---|---|---|
| 统一入口 | `bin/audit` | `run` 一条命令跑完全流程 |
| 词法器 | `lib/lexer.py` | 剥离注释与字符串，供结构匹配 |
| 依赖图 | `lib/graph.py` | Tarjan 环检测 / orphan / fan-in-out |
| 模式扫描 | `lib/scan.py` | 语法感知候选提名 |
| 问题库 | `lib/registry.py` | 跨轮次记忆（findings + 已证伪） |
| 提名去重 | `lib/triage.py` | 打分 → 去重 → 生成工单 |
| 报告合成 | `lib/report.py` | 报告骨架 |
| 项目规则 | `rules/AUDIT-RULES.md` | 项目不变量、高发模式、已知误报 |
| 方法论 | `skill/deep-audit-SKILL.md` | 工单自包含的方法论副本 |

### 1.2 本轮的三处优化

**① 修复 `reg add` 崩溃**
`bin/audit` 的 `cmd_reg` 在选填参数缺失时把 `None` 直接拼进命令数组，导致 `TypeError: sequence item 12: expected str instance`。已做 None→空串归一、`--sev` 默认 P2、`promote/reject` 缺 `--id` 时报错退出。`lib/registry.py` 补齐 `add/promote/reject` 子命令与 `import sys`。

**② 无界增长规则重写（v2）**
v1 只匹配模块级 `new Map/Set` + 无条件 add/push，扫出 0 条，无法判断"真没有"还是"规则太窄"。v2 改为三档：

- A 档：模块级 add≥3 且零删除 → high
- B 档：模块级 add≥5 且删除次数 < add/3 → medium
- C 档：模块级数组 push≥2 无截断 → medium

作用域限定缩进 0（模块级），正则兼容泛型 `new Map<K,V>()` 与初值 `new Set([...])`。

**验证结论**：扫出仍为 0 条，但这次是**可信的 0**——抽查确认模块级容器均有配对删除（`scheduler.ts:67` activeRunLeases add=1/delete=1、`render-code-runner.ts:162` agentCodeRunStates add=1/delete=1）。

遗留局限：工具能识别"有删除"但无法判断"删除是否覆盖全部退出路径"——这正是 DPP-002（删除自动化不等待终态）的成因，这类仍需人工。

**③ 误报剔除改为 file+行号窗口匹配**
原逻辑要求 `kind` 完全一致才剔除。实测踩坑：FP-022 录入时 kind 写成 `path_traversal`，而候选归一化后是 `silent_failure`，导致已证伪的候选反复出现在工单里。

改为 **file + 行号窗口（±15）** 匹配。理由：同一行代码被证伪一次，就该一直被抑制，与它归到哪个类别无关。修复后验证：**已证伪位置在 NEW 中残留 0 条**。

---

## 二、流水线结果

```
[graph]  451 files / 1019 runtime edges / 475 type-only edges / 0 运行时环 / 52 orphans
[scan]   162 hits / 19 leak candidates / 10 rule-suppressed / 0 unbounded
[triage] total 165 · NEW 160 · MOVED 4 · KNOWN 1
[工单]   4 份（各 116~121 分，文件数 5~10，风险分轮转均衡）
```

关键架构事实（与第三轮一致，无新增）：

- **0 个运行时循环依赖** —— 第三轮报的两个"环"确认为 `import type` 假阳性，v2 图谱已分离类型边
- 枢纽模块：`entrypoints/sidepanel/i18n.tsx`(38 fan-in)、`core/i18n/background.ts`(19)、`runtime-command-registry.ts`(18)
- 跨运行时边均为「运行时 → core」合法方向

---

## 三、四个工单的定罪结果

| 工单 | 区域 | 候选 | 定罪 | 复核后 |
|---|---|---|---|---|
| worker-1 | core/deepseek, entrypoints/content, browser-control, i18n | 16 | 0 | 0 |
| worker-2 | core/sync, interceptor, sandbox, artifact, inline-agent | 16 | 0 | 0 |
| worker-3 | persistence, mcp, multimodal, background, chat, remote-agent | 16 | 4 | **1 medium** |
| worker-4 | entrypoints, debug, network, automation, prompt | 16 | 2 | **1 high** |

**子 agent 报 6 条，人工复核后剔除 3 条、降级 1 条，净新增 2 条。**

---

## 四、质量闸门：本轮剔除与降级

子 agent 的结论不能直接采信，以下是逐条回源码复核的结果。

### ❌ 剔除 3 条

**① `clearInterval` 清 `setTimeout` 升 HIGH —— 论断错误**

worker-3 称"`clearInterval` 对 `setTimeout` ID 的规范行为是无事发生"。**这条是错的。** 浏览器规范中 `clearTimeout()` 与 `clearInterval()` 操作同一个 active timers map，**可以互换**（MDN 明确记载）。加上 `state.enabled = false` 的守卫，timer 确实会被取消。

维持第三轮判定：**P3 代码质量**，不升级。

**② 启动失败后 `lastSeenMessageId` 留 null 导致批量重发 —— 误报**

worker-3 称"`findNewMessages(messages, null)` 会把历史消息全部当作新消息"。实际 `watcher.ts:328-332`：

```ts
if (lastSeenId === null) {
  // B3 fix: 首次运行 — 把当前所有消息当作"已看过"（不回放历史）
  return [];
}
```

null 恰恰是**安全哨兵**，显式返回空数组。且失败分支（`:119-122`）根本没调 `scheduleNextPoll`，没有 timer 在跑。不存在"批量重发"路径。

剔除。残留的真实问题只有 `enabled` 未置 false（UI 状态不一致），降为 P3 单列。

**③ 中断恢复以回收 deadline 为完成时刻 —— 重复**

与已入库 DPP-011 同一问题（`automation/store.ts:300`），第四轮重复提名。剔除。

### ⬇️ 降级 1 条

**multimodal URL 校验不足（worker-3 报 MEDIUM）→ P3**

worker-3 建议拒绝 loopback/link-local/私网 IP。但这是**用户自填的配置**，本地 Ollama 就是 `http://localhost:11434`，拒绝 loopback 会直接把功能打死。

真实风险仅限"URL 内嵌凭据会传入 native 环境变量"，而这也是用户自己填的。属于**加固建议**，非功能缺陷。降为 P3。

---

## 五、净新增 Finding（2 条）

### [P1 / High] 调试遥测默认开启，将完整用户提示写入宿主页面 localStorage

- **位置**：`core/debug/refactor-telemetry.ts:115`（采集 `:102-118`，挂载 `:435-463`）
- **触发**：生产路径默认触发，无需用户操作

```ts
// request-interceptor.ts:82 —— 核心拦截器，生产路径
mountDebugToWindow();

// refactor-telemetry.ts:443 —— 默认开启，只有显式 '0' 才关
return window.localStorage.getItem("dpp_debug") !== "0";

// :115 —— 写入 window.localStorage
window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));

// RequestMetrics 含 promptText（完整用户提示）+ bodySample（前 2000 字节请求体）
```

- **复核过程**：确认三件事才定级 —— ① `recordRequestFromBody` 在 `request-interceptor.ts:169/312` 被调用，属生产路径；② storage 是 `window.localStorage`，在 content script 中即**宿主页面 chat.deepseek.com 的存储**，该站点任意脚本可读；③ `mountDebugToWindow` 默认挂载，`__DPP_DEBUG__.export()` 可导出到剪贴板。
- **后果**：用户完整对话提示与请求体明文落盘到宿主页面可读区，且可被一键导出。**与 DPP-006（console 日志钩子写 localStorage）同类风险，不同文件不同机制。**
- **修复**：默认不采集 `promptText` 与请求体；调试模式需显式开启并脱敏；敏感字段不得写入页面 localStorage（改用 `chrome.storage.local`）。

---

### [P2 / Medium] pendingRequests 以外部可控 requestId 为键，重复时 Promise 永久悬挂

- **位置**：`core/mcp/transports/native.ts:53`（写入 `:114-146`）
- **与 DPP-024 同源**（`transport-jsonrpc.ts:181` 同类问题），但发生在 native 链路，属独立点位

```ts
const nativePortStates = new Map<string, NativePortState>();
// ...
state.pendingRequests.set(requestId, { resolve, reject, timer, signal, onAbort });
```

`requestId` 来自外部 `McpJsonRpcRequest.id`（number | string），模块内不保证唯一。两个并发请求携带相同 id 时，后写入覆盖前写入，前一个 `Promise` 永不 settle —— 且因 Map 中已无其条目，`onDisconnect` 的 `clear()` 也不会清理它的 timer 与 signal listener。

- **后果**：请求永久挂起 + timer/listener 泄漏。
- **修复**：改用模块内自增序号做内部键，外部 id 仅用于响应匹配；或写入前检测冲突并 reject 前一个。

---

### P3 三条（记录，不紧急）

| # | 位置 | 问题 |
|---|---|---|
| DPP-031 | `remote-agent/watcher.ts:160` | `clearInterval` 清理 `setTimeout` 句柄（浏览器可互换，仅代码质量） |
| DPP-032 | `remote-agent/watcher.ts:118` | 初始化失败后 `enabled` 仍 true 但无 timer，UI 状态与实际不一致 |
| DPP-033 | `multimodal/settings.ts:105` | `validateHttpBaseUrl` 只校验协议，未限制 URL 内嵌凭据（加固建议） |

---

## 六、问题库状态

回填后共 **55 条**：28 条 DPP 缺陷 + 5 条新增 + 22 条已证伪误报（FP）。

本轮回填明细：

- **新增缺陷 5 条**：DPP-029（telemetry）、DPP-030（native pendingRequests）、DPP-031~033（P3）
- **新增误报 17 条**：FP-006~018（worker-1/2 全部证伪候选）、FP-019~022（本轮复核剔除与降级项）

去重效果验证：**已证伪位置在 NEW 候选中残留 0 条**，去重链路工作正常。

---

## 七、局限（必须说明）

**① 本轮覆盖率不完整 —— 这是最重要的一条**

流水线产出 **160 条 NEW 候选**，但四个工单只派发了约 40 条（triager 默认 `top_n=60` 后聚类分配）。剩余 **120+ 条候选从未被人工看过**，状态是 UNTRIAGED，**不代表已确认无问题**。

要全覆盖需把 `top_n` 调大并增加工单数（如 8~10 个），或分多轮消化。

**② 纯静态审计**

沙箱 npm registry 受限，`tsc --noEmit` / vitest / 三浏览器构建**均未执行**。所有结论基于源码阅读，未经运行时验证。

**③ 工具能力边界**

- 无法识别"有删除但覆盖不全"的泄漏（DPP-002 的成因）
- 不做类型系统分析，清理责任委托（scope/signal）仍需人工确认
- `unbounded_container` 规则仍限于模块级声明，函数内与类成员容器不覆盖

**④ 未覆盖模块**

`core/project/store.ts` 全量、`history-organizer.ts`、`core/memory`、`core/preset`、`core/scenario`、Firefox 特化分支、`packages/shell-host` 原生二进制。

---

## 八、四轮累计

| 轮次 | 方法 | 净新增 |
|---|---|---|
| 第一轮 | 按模块人工通读 | 25 |
| 第二轮 | deep-audit skill 四阶段 | 28（含交叉复核） |
| 第三轮 | 图谱驱动提名 + 定罪 | 5 |
| **第四轮** | **整合工作流 + 增量审计** | **2**（1 high + 1 medium） |

累计约 60 条 finding，其中 5 条 P1 级需发布前修复：

1. DPP-005 凭据明文落盘
2. DPP-006 console 日志钩子写宿主页面 localStorage
3. **DPP-029 telemetry promptText 落盘（本轮新增）**
4. DPP-001 sync 全链路无超时
5. DPP-002 删除自动化不等待终态

---

## 九、下次审计的起点

```bash
cd /data/workspace/audit-kit
./bin/audit run /data/workspace/deepseekpp-main
# 关注 triage.json 的 NEW —— 已证伪项会自动抑制，不会重复出现
# 建议把 top_n 调大到 160 并派 8 个工单，消化剩余 UNTRIAGED
```

工作流已具备跨轮次记忆：确认的缺陷 `reg add` 后下轮自动去重，新误报用 `FP-` 前缀回填后**自动注入每个工单头部**，子 agent 命中即跳过。
