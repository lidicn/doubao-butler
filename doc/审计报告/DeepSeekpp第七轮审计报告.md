# DeepSeek++ 第七轮审计 — 修正覆盖率度量 + 强化结论质量闸门

```
Audit target : github.com/lidicn/deepseekpp (DeepSeek++ v1.16.0)
Toolkit      : /data/workspace/audit-kit
Coverage     : core/tool(31) + core/messaging(17) 首次深度通读
              累计文件覆盖 396/451 (87.8%)
Audit date   : 2026-10-05
Mode         : Audit-Only（不改代码）
```

---

## 一、本轮工作流优化

### 1.1 修正覆盖率度量：新增独立台账

第六轮我用"问题库里留过记录的文件"反推覆盖率 —— 这是**错误的代理**：

> 通读过但没发现问题的文件不会留记录，于是永远显示"未覆盖"。

实测失真很严重：`core/pet`、`core/shell`、`core/trusted-directory` 在第六轮已完整通读，台账仍显示 0% 覆盖。按这个指标派发，第七轮会把同一批模块再派一遍。

**修复**：新增 `lib/coverage.py`，把"派发过"与"是否发现问题"彻底解耦，单独记账。

```bash
python3 lib/coverage.py record --modules core/tool,core/messaging --round 7
python3 lib/coverage.py report    # 按模块列覆盖率
python3 lib/coverage.py gaps      # 列出未覆盖模块，可直接接 focus 派发
```

回填后真实覆盖率：

| | 修正前（错误代理） | 修正后（台账） |
|---|---|---|
| 模块覆盖 | 10/49 | **33/49** |
| 文件覆盖 | 43/451 (9.5%) | **396/451 (87.8%)** |

### 1.2 强化 focus 工单的结论质量闸门

第六轮 focus 工单剔除率 71%，失败几乎全是**机制判断错误**。本轮在 `lib/focus.py` 生成的工单末尾新增两节：

- **错误论断 vs 实际机制对照表**（5 条真实踩过的坑，含 IDB 事务原子性、preset encode fail-loud、`.env.` 前缀匹配、配额强制、ZIP 路径 filter）
- **5 条自查清单**（原子性语义？例子真的满足匹配条件？上限是真失效还是重复计数？是吞错还是显式降级？清理责任是否被委托？）

效果：本轮两个子 agent 共报 7 条，剔除 2 条（**剔除率 29%**，第六轮为 71%）。

---

## 二、本轮覆盖

按修正后的台账，剩余未覆盖模块中体量最大的两个：

| 模块 | 文件数 | 工具提名 |
|---|---|---|
| core/tool | 31 | 1 |
| core/messaging | 17 | **0** |

`core/messaging` 零提名 —— 正是第六轮发现的"安静模块"典型。共生成 6 张工单（core/tool 按 10 文件切 4 批，core/messaging 切 2 批）。

---

## 三、定罪结果：净新增 5 条

子 agent 共报 7 条，复核后剔除 2 条，净新增 3 条 P2 + 2 条 P3。

### [P2] abort 取消工具执行后，一次性授权预约被永久消耗

- **位置**：`core/tool/runtime.ts:249`（判定点 `authorization-flow.ts:250`）

```ts
// runtime.ts:239-250 —— catch 分支调用时不传 result
} catch (error) {
    diagnosticLogBuffer.record({ ... });
    await completeAuthorizationAfterProvider(authorized.reservation);   // ← 无 result
    throw error;
}
```

```ts
// authorization-flow.ts:250-253
call.state = isRetryableWebFetchPermissionPrecondition(call.descriptorId, result) &&
  !call.retryUsed ? 'retryable' : 'consumed';
// result === undefined → 函数返回 false → state = 'consumed'
```

```ts
// web-fetch-permission.ts:10-12 —— 必须是 ok===false 且特定错误码才 retryable
return descriptorId === WEB_FETCH_DESCRIPTOR_ID && result?.ok === false && ...
```

- **复核**：子 agent 定 HIGH，我**降为 P2**。机制成立（abort 确实把预约推成 consumed），但：① abort 是用户主动取消，不是静默失败；② 影响是"该 callId 需等 grant 过期（30 分钟）才能重试"，非数据丢失或越权。
- **违反的不变量**：项目 AGENTS.md 要求取消语义完整 —— abort 后不应推进授权状态机。

---

### [P2] 授权完成态持久化失败仅 console.error

- **位置**：`core/tool/runtime.ts:317-325`
- **说明**：catch 分支注释解释了设计意图（保留真实 provider 结果而非用存储错误覆盖），这个取舍合理。但**授权状态可能永久停在 executing**，且无任何补偿或告警。

---

### [P2] executing 状态无 TTL 回收

- **位置**：`core/tool/authorization-flow.ts:241`
- **复核**：`completeToolExecutionAuthorization` 只在被调用时推进状态，没有基于时间的回收路径。SW 崩溃时 callId 预约永久阻塞。与 DPP-045 叠加会放大影响。

---

### [P3] PendingRequestRegistry 双层 Map 无 TTL 与上限

- **位置**：`core/tool/pending-request-registry.ts:2`
- **复核**：类只提供 `set/delete/drain/drainAll`，无基于时间的清理。`drain` 在条目清空时会删外层键，正常路径可控，但**异常退出路径下 inner Map 不为空则外层键保留**，会累积。

---

### [P3] EXECUTE_TOOL_CALL 编解码层不校验 call.source 一致性

- **位置**：`core/messaging/tool-runtime-request-codec.ts:255`
- **说明**：见下方剔除说明，这条从 HIGH 降为 P3 纵深防御缺口。

---

## 四、质量闸门：剔除 2 条

### ❌ "外部 ToolCall 载荷校验未阻止未授权工具执行" → 降为 P3

子 agent 定 HIGH，称"`authorizationId` 来自消息，可导致未授权执行"。

**这条把"查找键"当成了"授权证据"。** 我追到实际执行路径：

```ts
// tool-execution-handlers.ts:199-209
const authorization: RuntimeToolAuthorizationContext = context.surface === 'deepseek_content'
  ? { kind: 'grant', grantId: authorizationId ?? '', subject: createToolAuthorizationSubject(context) }
  : createTrustedToolExecutionContext(...);
```

```ts
// authorization-flow.ts:127-131 —— grant 路径的三重校验
const grant = requireGrant(state, context.grantId, now);
assertSubjectMatches(grant, context.subject);        // ← subject 来自浏览器 sender
assertCallSourceMatchesGrant(call, grant);
```

`authorizationId` 只是**用来查 grant 的键**，真正的授权来自：grant 存在且未过期 + subject 匹配（浏览器提供的 sender）+ call.source 与 grant 匹配。消息里声明的元数据不能单独构成授权 —— 项目这条不变量**是被遵守的**。

保留为 P3：编解码层不做一致性校验属纵深防御缺口（a defense-in-depth gap），不是可绕过。

### ❌ "authorizeRuntimeMessage 缺 sender 验证" → 剔除

`runtime-boundary.ts:236-243` 的两个 early return 是**显式白名单**（`extension_context` 放行、`DEEPSEEK_CONTENT_RUNTIME_COMMANDS` 放行），不是缺失校验。

---

## 五、问题库与覆盖状态

问题库共 **89 条**：41 条 finding + 40 条已证伪误报。本轮新增 5 条 finding（DPP-045~049）+ 2 条误报（FP-039/040）。

以 `--top-n 0` 全量验证：**143 条 NEW 候选中已证伪残留 0 条**。

**模块覆盖 33/49，文件覆盖 396/451 (87.8%)**。

**剩余未覆盖（16 个模块）**：`core/skill`(16)、`core/export`(13)、`core/usage`(7)、`core`(5)、以及 `core/background`、`core/floating-chat`、`core/browser`、`core/diagnostics`、`core/history-organizer`、`core/model`、`core/theme`、`core/token`、`core/tool-loop`、`core/voice`、`entrypoints/sandbox-offscreen`、`entrypoints/sandbox-runner` 各 1-2 文件。

---

## 六、局限

**① 覆盖率仍是粗粒度**

台账按**模块**记账，不精确到文件。一个模块被派发即记全覆盖，但工单里的文件可能未被逐行通读。87.8% 应理解为"模块级覆盖"的上界，不是"逐行审过"的比例。

**② 子 agent 未完整交付格式**

第二个子 agent 达到工具调用上限，返回的是结论摘要而非完整 PHASE 2/3 报告。本轮所有 finding 均已由我回源码复核确认。

**③ 纯静态审计**

`tsc --noEmit` / vitest / 构建均未执行（registry 受限）。DPP-045~047 三条授权状态机问题的影响面基于源码推断，未经运行时验证。

**④ 授权链路未能端到端复现**

`authorizeToolExecution` 的完整调用链涉及 background SW 与多个 surface，静态阅读无法覆盖所有组合。DPP-049 定为 P3 正因如此。

---

## 七、七轮累计

| 轮次 | 方法 | 净新增 | 剔除率 |
|---|---|---|---|
| 第一轮 | 模块人工通读 | 25 | — |
| 第二轮 | deep-audit skill 四阶段 | 28 | — |
| 第三轮 | 图谱驱动提名 | 5 | 58% |
| 第四轮 | 整合工作流 + 增量 | 2 | 50% |
| 第五轮 | 全量覆盖（144 条派发） | 9 | 31% |
| 第六轮 | 覆盖驱动 focus（首用） | 2 | **71%** |
| **第七轮** | **修正覆盖度量 + 强化闸门** | **5** | **29%** |

累计约 76 条 finding。**P1 待修复清单（7 条，本轮无新增）**：

1. DPP-005 凭据明文落盘
2. DPP-006 console 日志钩子写宿主页面 localStorage
3. DPP-029 telemetry promptText 落盘
4. DPP-001 sync 全链路无超时
5. DPP-002 删除自动化不等待终态
6. DPP-034 automation deadlineAt 可空
7. DPP-043 导入本地 Skill 静默追加文件写入授权

本轮新增的 DPP-045~047 三条集中在**工具授权状态机**，建议与 DPP-025（预约重复消费）、DPP-043 一起作为授权子系统整体重构。

---

## 八、下一轮建议

```bash
cd /data/workspace/audit-kit
python3 lib/coverage.py gaps      # 拿剩余未覆盖模块
python3 lib/focus.py <target> --modules <gaps>
python3 lib/coverage.py record --modules <已派发> --round 8
```

优先 `core/skill`（16 文件，含 GitHub/本地导入的注入面，DPP-008/009 已入库但模块未整体通读）与 `core/export`（13 文件，全量驻留内存 DPP-020 已入库）。

16 个剩余模块合计仅 55 文件，再派 2-3 轮即可达成模块级全覆盖。
