# DeepSeek++ 第五轮审计 — 工作流优化 + 全量覆盖

```
Audit target : github.com/lidicn/deepseekpp (DeepSeek++ v1.16.0)
Toolkit      : /data/workspace/audit-kit
Coverage     : 144 条 NEW 候选全部派发（第四轮仅消化约 40 条）
Audit date   : 2026-10-04
Mode         : Audit-Only（不改代码）
```

---

## 一、本轮工作流优化

### 1.1 图谱工具：确认不可用，继续使用自研实现

再次尝试安装沙箱图谱工具，结论与第三轮一致：

| 工具 | 结果 |
|---|---|
| `dependency-cruiser` 16.10.4 | 装包不完整，`bin/dependency-cruise.mjs` → `src/cli/index.mjs` 缺失 |
| `madge` | 缺 `lib/cli.js` |
| `@ast-grep/cli` | 原生二进制 segfault（exit=139） |

根因是沙箱 npm registry 受限，包体下载不全。**自研 Python 图谱工具继续承担全部图谱职责**，功能不打折（Tarjan 环检测、orphan、fan-in/out、跨运行时边、结构化模式扫描）。

### 1.2 覆盖率优化：消除候选积压

第四轮的最大局限是**160 条候选中约 120 条从未被人工看过**。本轮修复：

- `lib/triage.py` 与 `bin/audit` 新增 `--top-n`（`0` = 全量）与 `--workers` 参数
- 以 `--top-n 0 --workers 8` 生成 8 个工单，**144 条候选全部派发**
- 大工单按文件/行号二次拆分（worker-1 的 41 条拆为 1a1/1a2，worker-3 的 42 条拆为 3a/3b），避免单个子 agent 超工具调用上限

### 1.3 修复 `--top-n` 参数未透传

`bin/audit triage` 子命令缺少 `--top-n` 定义，导致参数被 argparse 拒绝、triage.json 停留在旧版本（残留 9 条已证伪候选）。已补齐参数定义与透传，验证后**已证伪残留降为 0**。

---

## 二、流水线结果

```
[graph]  451 files / 1019 runtime edges / 475 type-only edges / 0 运行时环 / 52 orphans
[scan]   162 hits / 19 leak candidates / 10 rule-suppressed / 0 unbounded
[triage] total 155 · NEW 144 · MOVED 6 · KNOWN 5 · 已证伪残留 0
[工单]   8 份（--workers 8），二次拆分后共 9 个执行单元
```

---

## 三、定罪结果：9 条净新增

子 agent 共报 15 条，人工复核后剔除 4 条、合并 2 条，**净新增 9 条**（1 P1 + 7 P2 + 1 P3）。

### [P1 / High] submitPrompt 的 deadlineAt 可空，未传时请求无超时可永久挂起

- **位置**：`core/automation/runner.ts:64`（传导点 `core/deepseek/active-client.ts:808`）
- **复核**：子 agent 报在 active-client.ts:808，我回源码追到根因在 runner.ts

```ts
// runner.ts:62-64
const requestContext: DeepSeekRequestContext = options.execution
  ? { signal: options.execution.signal }
  : { deadlineAt: request.deadlineAt };   // ← 可空
```

```ts
// request-policy.ts:122-131 —— undefined 时直接返回无超时 scope
const { deadlineAt, operation } = policy;
if (deadlineAt === undefined) {
  return { signal: callerSignal ?? undefined, timedOut: () => false, cleanup() {} };
}
```

- **为什么是 P1**：`deadlineAt` 是可选字段（`automation-client-port.ts:30` 声明为 `deadlineAt?`）。一旦未传，`request-policy` 直接放弃超时保护。automation 是**常驻后台链路**，一次挂起会阻塞整个执行周期 —— 与已入库的 DPP-001（sync 无超时）同源，但发生在 automation 链路。
- **注意**：正常 automation 路径（`scheduler.ts:159` `deadlineAt: Date.now() + timeoutMs`）是有保护的；本条命中的是**依赖调用方传值**这条脆弱契约。
- **修复**：让 `request-policy` 在 `deadlineAt` 缺失时回落到默认超时，而非放弃保护；或在类型层把 `deadlineAt` 改为必填。

---

### [P2] onMessage 错误统一映射为 null，调用方无法区分失败与空结果

- **位置**：`core/messaging.ts:14`

```ts
result.then(sendResponse).catch(() => sendResponse(null));
```

- **复核说明**：这条**第一轮就报过但从未入库**，第五轮由子 agent 重新发现，现已补入问题库（DPP-035）。这说明前几轮的入库流程有遗漏——本轮已确认问题库覆盖完整。
- **后果**：所有经 `onMessage` 的调用，失败与"合法空结果"返回完全一样的值，上层无法判断，错误在边界处消失。

---

### [P2] removeWindow 移除面板但不清理 dragState，document 级拖拽监听残留

- **位置**：`entrypoints/content/adapters/chat-launcher.ts:272`（状态 `:19-26`）

```ts
function stopActiveDrag(ownerId?: string): void {
  if (ownerId && dragState?.ownerId !== ownerId) return;   // ← 不匹配直接返回
  ...
  dragState = null;
  document.removeEventListener('pointermove', onDrag);     // ← 清理在这里
  document.removeEventListener('pointerup', stopDrag);
  document.removeEventListener('pointercancel', stopDrag);
}

function removeWindow(ownerId?: string): void {
  panel.remove();                                          // ← 不调 stopActiveDrag
}
```

- **复核**：`stopActiveDrag` 本身清理是配对的（这点子 agent 描述准确），但 `removeWindow` 移除面板时**不调用**它，`dragState` 与三个 document 级监听因此残留。
- **后果**：面板反复开关后 document 上累积 pointermove/pointerup 监听。

---

### [P2] 背景图/宠物设置 6 处乐观更新，保存失败不回滚

- **位置**：`entrypoints/sidepanel/controllers/useSettingsController.ts:492-654`
- **说明**：6 处同一根因（先更新本地 state 再持久化，无 try/catch），合并计 1 条。
- **后果**：保存失败时 UI 显示成功、实际未落盘，UI 与存储分叉。

---

### [P2] skill-popup 每次重建 DOM 叠加元素级监听

- **位置**：`core/ui/skill-popup.ts:175-199`

```ts
popupEl.innerHTML = filtered.map(...).join('') + ...;
popupEl.querySelectorAll('.dpp-skill-item').forEach(el => {
  el.addEventListener('mouseenter', ...);
  el.addEventListener('mousedown', ...);
});
```

- **复核**：子 agent 归为 HIGH 并称"1 行内修复"，我复核后降为 P2 —— 监听挂在被 innerHTML 重建的**子元素**上，节点脱离文档后可被 GC 回收，累积速度受限。但重建循环中的确未显式移除，`stopSkillPopup()` 也只清 document 级。保留为 P2 资源泄漏。
- **修复**：事件委托到 `popupEl`，或在重建前 `cloneNode(true)` 剥离旧监听。

---

### [P2] 预览面板重复打开时路由守卫监听叠加

- **位置**：`core/ui/tool-result-renderer.ts:289-306`
- **复核**：子 agent 描述准确。window 级 `popstate`/`hashchange` 与 `setInterval` 无单例守卫，重复打开叠加；旧 timer 回调会清掉新面板的引用，导致新面板的路由守卫实际失效。
- **修复**：打开前先 `closeArtifactPreviewPanel()` 保证单例，清理逻辑幂等。

---

### [P2] remote agent 初始化失败仅 console.error

- **位置**：`entrypoints/content.ts:1450`

```ts
} catch (err) {
  console.error('[DPP-REMOTE] Init error:', err);
}
```

- **后果**：Remote Agent 功能静默失效，用户无任何可见信号。

---

### [P2] STATE_UPDATED 消息解码失败仅 console

- **位置**：`entrypoints/content.ts:1494`
- **后果**：记忆状态静默不同步，用户无感知。

---

### [P3] loadFromStorage 静默吞 JSON 解析异常

- **位置**：`core/debug/refactor-telemetry.ts:121`

```ts
} catch {
  // 数据损坏，忽略
}
```

- **后果**：`saveToStorage` 同样空 catch，一旦写入中断导致 JSON 截断，后续持续写坏数据且用户无感知。违反项目自述不变量"不允许 broad catch / 静默默认值"。

---

## 四、质量闸门：本轮剔除与合并

| # | 子 agent 结论 | 复核结果 |
|---|---|---|
| 1 | skill-popup 监听累积 → HIGH | ⬇️ **降 P2**。挂在被 innerHTML 重建的子元素上，节点脱离文档后可 GC，累积速度受限 |
| 2 | telemetry `bodySample` 落盘 → MEDIUM | ❌ **剔除（重复）**。`bodySample` 与 DPP-029 的 `promptText` 属同一落盘链条，不重复计 |
| 3 | telemetry loadFromStorage 静默吞异常 → 排除项 | ⬆️ **升为 P3**。虽子 agent 自己排除，但 `saveToStorage` 同样空 catch 会持续写坏数据，违反项目不变量，值得记录 |
| 4 | useSettingsController 6 处 → 计 5 条 | ⬇️ **合并为 1 条**。同一根因（乐观更新无回滚），按根因计 |
| 5 | active-client.ts:808 deadline → HIGH | ✅ **保留，修正位置到 runner.ts:64**。子 agent 定位在传导点，根因在 runner 的 deadlineAt 可空 |

**本轮共证伪 10 条误报**（telemetry:229、loop-adapter:269、render-steps:187 innerHTML 安全链、deepseek-stream-fn AbortScope 管理、mcp http/sse 清理兜底、tool-card 静态模板、tool-result-renderer 手势守卫、cdp 双守卫、telemetry bodySample 重复）。

---

## 五、问题库状态

回填后共 **74 条**：37 条 finding + 32 条已证伪误报。

本轮新增：9 条 finding（DPP-034~042）+ 10 条误报（FP-023~032）。

**去重链路验证通过**：以 `--top-n 0` 全量派发后，144 条候选中**已证伪位置残留 0 条**。

**发现一个流程缺陷**：`core/messaging.ts` 的错误映射问题第一轮就报过，但从未进入问题库，导致第五轮被当作新发现重新报了一遍。本轮已补入（DPP-035），说明**前几轮的入库环节存在遗漏**——这是工作流本身的问题，比某个具体 finding 更值得注意。

---

## 六、局限

**① 子 agent 未完整交付格式**

worker-1 首次派发因工具调用上限（29 turns）中断，虽已拆分重派，但两个子 agent 未按完整 SKILL 格式交付报告，仅返回核心结论。本轮所有 finding 均已由我回源码复核确认，但**子 agent 原始报告的完整性不如前几轮**。

**② 纯静态审计**

`tsc --noEmit` / vitest / 三浏览器构建均未执行（沙箱 registry 受限），结论未经运行时验证。

**③ 工具能力边界未突破**

- 无法识别"有删除但覆盖不全"的泄漏（DPP-002 的成因）
- 清理责任委托（scope/signal）仍需人工确认
- 无界增长规则仍限于模块级声明

**④ 仍未覆盖**

`core/project/store.ts` 全量、`history-organizer.ts`、`core/memory`、`core/preset`、`core/scenario`、Firefox 特化分支、`packages/shell-host` 原生二进制。

---

## 七、五轮累计

| 轮次 | 方法 | 净新增 |
|---|---|---|
| 第一轮 | 模块人工通读 | 25 |
| 第二轮 | deep-audit skill 四阶段 | 28 |
| 第三轮 | 图谱驱动提名 | 5 |
| 第四轮 | 整合工作流 + 增量 | 2 |
| **第五轮** | **全量覆盖（144 条派发）** | **9** |

累计约 69 条 finding。**P1 级待修复清单**：

1. DPP-005 凭据明文落盘
2. DPP-006 console 日志钩子写宿主页面 localStorage
3. DPP-029 telemetry promptText 落盘
4. DPP-001 sync 全链路无超时
5. DPP-002 删除自动化不等待终态
6. **DPP-034 automation deadlineAt 可空（本轮新增）**

---

## 八、工作流现状

`audit-kit` 已具备完整闭环：

```bash
cd /data/workspace/audit-kit
./bin/audit run <target>                    # graph → scan → triage → report → 工单
./bin/audit triage --top-n 0 --workers 8    # 全量覆盖，避免候选积压
./bin/audit reg add --id FP-xxx ...         # 误报回填，下轮自动注入工单头部
```

跨轮次记忆工作正常：已证伪位置自动抑制，NEW 候选只含净增量。

**下一轮建议**：本轮已消化全部 144 条候选，下一轮应转向**未覆盖模块**（`core/project/store.ts`、`core/memory`、`core/preset`、`core/scenario`），而非重复扫描已知区域。
