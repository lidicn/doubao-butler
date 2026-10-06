# DeepSeek++ 第六轮审计 — 补上"提名驱动"的结构性盲区

```
Audit target : github.com/lidicn/deepseekpp (DeepSeek++ v1.16.0)
Toolkit      : /data/workspace/audit-kit
Coverage     : 10 个从未被派发过的模块（43 文件）首次深度通读
Audit date   : 2026-10-05
Mode         : Audit-Only（不改代码）
```

---

## 一、本轮的核心发现：工作流本身有盲区

前五轮一直按「工具提名 → 打分排序 → 派发工单」跑。这个模式有个结构性缺陷：

**提名少的模块永远排不进工单。**

实测 —— 那些从未被派发过的模块，工具提名数极少：

| 模块 | 文件数 | 工具提名 |
|---|---|---|
| core/project | 5 | 0 |
| core/memory | 8 | 1 |
| core/preset | 2 | 0 |
| core/scenario | 3 | 0 |
| core/pet / platform / saved-items | 11 | 0 |
| core/shell | 3 | 0 |
| core/trusted-directory | 5 | 0 |
| core/artifact | 6 | 0 |

这些模块五轮下来**从未被人工看过**，不是因为确认安全，而是因为不产生候选 → 排不进工单 → 无人问津。

### 修复：新增覆盖驱动（focus）模式

`lib/focus.py` —— 不依赖提名，直接按模块生成工单，要求执行方做**深度通读**（deep-audit PHASE 2/3）而非候选确认。

```bash
python3 lib/focus.py <target> --modules core/project,core/memory
python3 lib/focus.py <target> --uncovered    # 自动算出尚未覆盖的模块
```

工单内自带通读重点清单（schema 迁移幂等、存储配额、并发串行化、取消语义、清理责任覆盖全部退出路径、资源上限），不依赖执行方自己想。

---

## 二、图谱工具

与第三、五轮结论一致：**沙箱 npm registry 装包不全，第三方图谱工具不可用**。

| 工具 | 结果 |
|---|---|
| dependency-cruiser 16.10.4 | `bin/dependency-cruise.mjs` → `src/cli/index.mjs` 缺失 |
| madge | 缺 `lib/cli.js` |
| @ast-grep/cli | 原生二进制 segfault（exit=139） |

自研 Python 图谱工具继续承担全部职责。本轮新增的 focus 模式也基于同一套词法器与文件遍历。

---

## 三、定罪结果：净新增 2 条

三个子 agent 共报 7 条，人工复核后**剔除 5 条**，净新增 1 条 P1 + 1 条 P3。

### [P1 / High] 导入本地 Skill 静默追加 `local_file_write` 到 shell allowlist

- **位置**：`core/shell/policy.ts:67`（调用点 `core/skill/local-importer.ts:625`）

```ts
// policy.ts:67-79
export function buildShellAllowlistUpgrade(allowlist: McpToolAllowlist): McpToolAllowlist | null {
  if (allowlist.mode !== 'allow') return null;
  const names = new Set(allowlist.toolNames);
  const missingLocalSkillTools = LOCAL_SKILL_SHELL_TOOL_NAMES.filter((name) => !names.has(name));
  const missingLocalFileTools = LOCAL_FILE_SHELL_TOOL_NAMES.filter((name) => !names.has(name));
  const missing = [...missingLocalSkillTools, ...missingLocalFileTools];
  ...
}
```

```ts
// policy.ts:22-26 —— 被追加的三个工具
export const LOCAL_FILE_SHELL_TOOL_NAMES = ['local_file_stat', 'local_file_read', 'local_file_write'];
```

```ts
// local-importer.ts:625 —— 导入本地 Skill 时无条件调用
server = await ensureLocalSkillShellToolsAllowed(server);
```

- **复核**：子 agent 定 P1，我确认成立，但要**精确化描述**。导入本地 Skill 确实需要读文件（`local_file_read`/`stat` 合理），问题在于**`local_file_write` 写入能力也一并被追加，而导入操作并不需要写**。
- **前置条件**（必须说明）：需用户已启用 shell MCP 执行、且 allowlist 处于 `allow` 模式。不是零点击漏洞。但"用户授权了某些工具"≠"用户授权了文件写入"。
- **违反项目自述不变量**：AGENTS.md 明确要求"每次生产工具执行必须先走授权路径；调用方提供的元数据绝不是授权证据"。此处由一次导入动作隐式扩权。
- **修复**：只追加 `local_skill_preview` / `local_folder_pick` / `local_file_stat` / `local_file_read`；`local_file_write` 需单独授权。

---

### [P3] addConversationToProject 把目标会话移到数组末尾

- **位置**：`core/project/store.ts:147`
- **复核**：子 agent 定 MEDIUM，我**降为 P3**。核心问题是"顺序被破坏"，但数组顺序在本模块没有文档化的语义依赖；与相邻的 `refreshProjectConversation`（用 map in-place）语义不一致属代码一致性问题。
- **附带发现**：子 agent 顺带验证了第三轮的 **DPP-014**（"会话列表无上限、配额静默失败"）—— 现行代码用 `filter(≠id)` 去重，同一 conversationId 不会膨胀，写入失败沿 `withProjectMutation` 抛出并不吞错。**DPP-014 原始描述已不准确**，已在库中标注，仅保留"跨 id 缺上限"这一条弱化建议。

---

## 四、质量闸门：本轮剔除 5 条

本轮剔除率 71%，是六轮里最高的一次。逐条说明：

### ❌ IDB 事务"先 clear 后 bulkAdd 会丢数据" —— 机制错误

子 agent 报 `core/memory/store.ts:203` MEDIUM，称"中途失败导致整表数据丢失"。

**这条是错的。** IndexedDB 事务是原子的 —— `bulkAdd` 失败会 abort 整个事务，`clear()` 一并回滚，数据不会丢。项目用的是原生 `indexedDB` 事务（`indexeddb.ts:216`），并非 Dexie。

我还顺带核查了事务活性风险（`indexeddb.ts:29-37` 有专门注释警示）：`readValidatedMemoryRecords(tx)` 内部的 `await db.open()` 返回的是**已缓存的 resolved promise**，只产生微任务跳转，不会让事务提前提交。代码合规，无风险。

### ❌ preset codec "encode 用 decode 导致脏数据静默写回" —— 方向反了

`encode` 复用 `decodePresetCollection` 确属分层味道（encode 承担校验职责），但**结论完全相反**：

```ts
function decodePreset(value, path) {
  const object = recordValue(value, path);
  if (object.schemaVersion !== undefined && object.schemaVersion !== PRESET_RECORD_SCHEMA_VERSION) {
    throw new Error(`${path}.schemaVersion is not supported`);   // ← 抛错
  }
  return { ...object, id: requiredString(...), ... };            // ← 保留额外字段
}
```

脏数据会**抛错**（fail-loud），且 `...object` 保留额外字段不丢弃。既非静默，也无字段丢失。仅为代码质量，不构成数据完整性缺陷。

### ❌ trusted-directory 两条 —— 举例不成立

**`.env.` 前缀误匹配**：子 agent 举 `.env.production`、`config.env.json` 为例。前者**本就是敏感的 env 文件**，忽略它正是预期行为；后者不以 `.env.` 开头，根本不会被匹配。两个例子都不成立。

**符号链接绕过配额**：两个上限都仍生效 —— `MAX_TRUSTED_FILES` 按条目计数、`MAX_TOTAL_INDEX_BYTES` 按字节累计字节。符号链接只让同一内容被计两次，属效率问题，不是配额绕过。

### ❌ ZIP 路径规范化缺口 —— filter 已覆盖

子 agent 称 `/./`、`//`、空名条目有缺口。实际 `filter((part) => part && part !== '.' && part !== '..')` 已剔除空段与 `.`/`..`；空名在 `zip.ts:13` 被 `continue`。与 `safeFilename` 的差异仅为 fallback 与 180 字符截断。

---

## 五、问题库状态

回填后共 **82 条**：39 条 finding + 37 条已证伪误报。

本轮新增：2 条 finding（DPP-043/044）+ 6 条误报（FP-033~038）。

以 `--top-n 0` 全量验证：**144 条候选中已证伪残留 0 条**，去重链路正常。

---

## 六、局限

**① 覆盖率指标是弱代理**

"已覆盖"用"问题库里有过记录的文件"衡量，这会低估（很多文件被工单看过但没发现问题，不留记录）。真实覆盖率高于报告显示的百分比，但**相对排序仍有参考值**。

**② 子 agent 结论质量下降**

本轮三个子 agent 共报 7 条，剔除 5 条（71%）。剔除集中在"机制判断错误"——尤其 IDB 原子性那条，是基础认知错误。说明**深度通读模式对子 agent 的要求高于候选确认模式**，后续若继续用 focus 模式，工单需更强调"先确认机制再下结论"。

**③ 纯静态审计**

`tsc --noEmit` / vitest / 构建均未执行，结论未经运行时验证。`shell_exec` 的实际调用链无法端到端复现，DPP-043 的影响面评估基于源码推断。

**④ 仍未覆盖**

`core/project/store.ts` 已完成；剩余：`history-organizer.ts` 全量、`core/i18n`（27 文件）、`core/messaging`（17 文件）、`entrypoints/background`（30 文件）、Firefox 特化分支、`packages/shell-host` 原生二进制。

---

## 七、六轮累计

| 轮次 | 方法 | 净新增 | 剔除 |
|---|---|---|---|
| 第一轮 | 模块人工通读 | 25 | — |
| 第二轮 | deep-audit skill 四阶段 | 28 | — |
| 第三轮 | 图谱驱动提名 | 5 | 7 误报 |
| 第四轮 | 整合工作流 + 增量 | 2 | 3 误报 |
| 第五轮 | 全量覆盖（144 条派发） | 9 | 4 误报 |
| **第六轮** | **覆盖驱动（focus 模式）** | **2** | **5 误报** |

累计约 71 条 finding。**P1 级待修复清单（7 条）**：

1. DPP-005 凭据明文落盘
2. DPP-006 console 日志钩子写宿主页面 localStorage
3. DPP-029 telemetry promptText 落盘
4. DPP-001 sync 全链路无超时
5. DPP-002 删除自动化不等待终态
6. DPP-034 automation deadlineAt 可空
7. **DPP-043 导入本地 Skill 静默追加文件写入授权（本轮新增）**

---

## 八、工作流现状与下一轮建议

`audit-kit` 现在有两种互补的派发模式：

```bash
# 提名驱动：覆盖高命中区域（已知大部分安全）
./bin/audit run <target>
./bin/audit workorders --top-n 0 --workers 8

# 覆盖驱动：补上安静模块的盲区（本轮新增）
python3 lib/focus.py <target> --uncovered
```

**下一轮建议**：继续用 focus 模式消化剩余未覆盖模块 —— `core/i18n`（27 文件）、`entrypoints/background`（30 文件）、`core/messaging`（17 文件）。这三个体量大、提名少，是最可能藏问题的地方。

同时建议在 focus 工单中**强化"先验证机制再下结论"的要求**，本轮 71% 的剔除率说明这是当前最大的质量风险点。
