# DeepSeek++ 第八轮审计 — 模块级全覆盖达成，净新增归零

```
Audit target : github.com/lidicn/deepseekpp (DeepSeek++ v1.16.0)
Toolkit      : /data/workspace/audit-kit
Coverage     : 49/49 模块，451/451 文件（模块级全覆盖达成）
Audit date   : 2026-10-05
Mode         : Audit-Only（不改代码）
```

---

## 一、结论先行

**本轮净新增 1 条（P3，且是已有条目的补充），剔除 7 条。**

这是八轮里第一次出现"几乎零净新增"。结合模块级全覆盖达成，这个结果本身就是一个信号：

> 在当前的静态审计深度下，这个代码库的**易发现缺陷已基本出清**。继续用同样的方法扫同样的区域，边际收益接近零。

这个判断有具体支撑 —— 本轮 8 条子 agent 结论里，7 条被剔除，且剔除原因高度一致：**前提不成立**（用户不可控、硬编码常量、已有超时兜底、字段其实已声明）。不是"看漏"，也不是"看错代码"，是**把推测性风险当成了现实缺陷**。

---

## 二、本轮工作流优化

### 2.1 focus 接入 coverage 台账

第七轮新增了 coverage 台账，但 `focus --uncovered` 仍在用旧的**错误代理**（按 findings 库反推）。本轮改正：

```bash
python3 lib/focus.py <target> --gaps    # 读 coverage 台账（推荐）
python3 lib/focus.py <target> --uncovered   # 已废弃，会高估未覆盖
```

`--uncovered` 保留但标注废弃，避免历史命令静默给出错误结果。

### 2.2 新增模块装箱算法

**问题**：16 个剩余模块里有 10 个只有 1-2 文件。逐模块生成会产生 10 张"单文件工单" —— 派发成本高、上下文浪费，子 agent 在孤立单文件上也不易发现跨模块问题。

**修复**：`pack_modules()` 用首次适应递减（FFD）把小模块合并到同一张工单，大模块仍独占并切分。

效果：**16 个模块从 16 张工单压缩到 7 张**，文件分布更均衡（11/11/4/11/5/11/2）。

### 2.3 新增 `--record` 联动

工单生成后自动记账，避免"派发了但忘记更新台账"导致下轮重复派发：

```bash
python3 lib/focus.py <target> --gaps --record --round 8
```

---

## 三、本轮覆盖

按台账取出的全部剩余模块，共 55 文件：

| 工单 | 模块 | 文件数 |
|---|---|---|
| focus-1 | core/usage + core/background + core/floating-chat | 11 |
| focus-2 | core + core/browser + core/diagnostics + core/history-organizer + core/model + core/theme + core/token | 11 |
| focus-3 | core/tool-loop + core/voice + sandbox-offscreen + sandbox-runner | 4 |
| focus-4/5 | core/skill | 11 + 5 |
| focus-6/7 | core/export | 11 + 2 |

**派发完成后：49/49 模块，451/451 文件 -- 模块级全覆盖达成。**

---

## 四、定罪结果：1 条 P3，剔除 7 条

### [P3] persistUsageBurst 是 DPP-010 的第二个受影响调用方

- **位置**：`core/persistence/coalescing-mutation-queue.ts:43`
- **说明**：DPP-010 已记录"输出长度与输入不符时 resolve 成 `{}`"。本轮补充：`core/usage/store.ts:30-49` 的 `persistUsageBurst` 在 storage 写入抛错或解码截断时同样返回长度不符的结果，走同一条静默成功路径。
- **判定为 P3**：根因与 DPP-010 相同（同一处代码），新增信息只是"受影响调用方多一个"。不重复计为独立 finding。

### 剔除 7 条（全部为"前提不成立"）

| # | 子 agent 论断 | 复核结果 |
|---|---|---|
| 1 | PDF `byteLength` 用字符数当字节数，CJK 导致 xref 偏移错误 | ❌ **PDF 全文经 `toPdfHexString` 转成 ASCII 十六进制**，输出是纯 ASCII，字符数==字节数，偏移正确。函数命名误导是潜在陷阱，但当前无误 |
| 2 | 导出文件名允许目录分隔符，可路径穿越 | ❌ 文件名只由 `createdAt`(ISO) + `mode`(raw/sanitized) + `extension` 固定模板拼接，**无用户可控输入** |
| 3 | ZIP 条目名未归一化，存在 Zip Slip | ❌ 条目名源自上述固定模板。子 agent 自己写明"若未来允许自定义文件名"——**推测性风险** |
| 4 | progress 协调器 LRU 驱逐破坏去重语义 | ❌ 上限 200 + FIFO 是**有界内存设计**；被驱逐的是已完成请求，失败签名本就在 catch 中删除（可重试） |
| 5 | sandbox iframe 无 load 超时，恶意 frameResult 导致泄漏 | ❌ `finish()` 已配对清理（clearTimeout + removeEventListener + frame.remove），`request.timeoutMs` 超时即触发 finish |
| 6 | tool-loop `maxDepth` 无校验，可配极大值 | ❌ `maxDepth` 来自模块级常量 `AUTOMATION_MCP_CONTINUATION_LIMIT = 3`，**非用户可配** |
| 7 | `importName` 字段未在类型中声明 | ❌ 已在 `github-importer.ts:365` 的 item 构造中显式声明，子 agent 未读完类型文件 |

另有 2 条直接重复：合并队列问题（= DPP-010）、messaging 静默吞错（= DPP-035）。

---

## 五、八轮趋势：边际收益递减

| 轮次 | 方法 | 净新增 | 剔除 |
|---|---|---|---|
| 第一轮 | 模块人工通读 | 25 | — |
| 第二轮 | deep-audit skill 四阶段 | 28 | — |
| 第三轮 | 图谱驱动提名 | 5 | 7 |
| 第四轮 | 整合工作流 + 增量 | 2 | 3 |
| 第五轮 | 全量覆盖（144 条派发） | 9 | 4 |
| 第六轮 | 覆盖驱动 focus（首用） | 2 | 5 |
| 第七轮 | 修正覆盖度量 + 强化闸门 | 5 | 2 |
| **第八轮** | **台账驱动 + 装箱** | **1** | **7** |

两条曲线交叉了：第三轮剔除 7 条、新增 5 条；第八轮剔除 7 条、新增 1 条。**同样的剔除量，产出的真实缺陷从 5 降到 1**。

结合"模块级全覆盖已达成"，我的判断是：**当前方法论下的易发现缺陷已基本出清**。

---

## 六、问题库与工作流最终状态

问题库共 **97 条**：42 条 finding + 47 条已证伪误报。

以 `--top-n 0` 全量验证：**142 条 NEW 候选中已证伪残留 0 条**。

`audit-kit` 现有组件：

| 组件 | 职责 |
|---|---|
| `bin/audit` | 统一入口（run / graph / scan / triage / report / workorders / reg） |
| `lib/lexer.py` | 剥离注释与字符串的词法器 |
| `lib/graph.py` | 依赖图、Tarjan 环检测、orphan、fan-in/out |
| `lib/scan.py` | 结构化模式扫描（含无界增长 v2） |
| `lib/registry.py` | 跨轮次问题库（findings + 误报） |
| `lib/triage.py` | 提名打分、去重、工单生成 |
| `lib/focus.py` | **覆盖驱动工单（含装箱 + 机制自查表）** |
| `lib/coverage.py` | **覆盖率台账** |
| `lib/report.py` | 报告骨架 |
| `rules/AUDIT-RULES.md` | 项目专属口径 |

---

## 七、如果要继续深挖，得换方法

模块级全覆盖达成后，继续用现有方法扫的边际收益很低。若还要往下挖，我建议换三条路（按性价比排序）：

**① 跑起来验证（收益最高）**

八轮全部是静态阅读，`tsc --noEmit`、vitest、三浏览器构建**一次都没跑过**（沙箱 registry 受限）。已入库的 DPP-045~047（授权状态机）、DPP-030（Promise 悬挂）这类问题，写个最小复现用例就能确认影响面。当前所有"影响"都是源码推断。

**② 动态/运行时审计**

- MV3 Service Worker 生命周期：SW 被回收时的状态恢复路径，静态读很难覆盖
- 真实网络挂起下的超时行为（DPP-001、DPP-034 的实际后果）
- 长会话内存增长实测（DPP-048、DPP-020 的实际阈值）

**③ 针对性代码审查而非全量扫描**

对已识别的高风险子系统做整体重构级审查 —— 特别是**工具授权子系统**（DPP-025/043/045/046/047/049 六条集中在此）。这些条目单独看都是 P2/P3，合起来指向的是同一套状态机设计问题，逐个修不如整体重写。

---

## 八、局限

**① 模块级覆盖 ≠ 逐行审过**

台账按模块记账。一个模块被派发即记全覆盖，但工单里的文件未必被逐行通读。100% 应理解为"每个模块都至少被派发过一次"，不是"每行代码都被人看过"。

**② 子 agent 多次未完整交付**

本轮三个子 agent 中，core/skill 那个因工具调用上限中断，未完成类型文件交叉验证即返回。我对其标记的待验证项做了复核（确认 `importName` 是误判），但**该模块的整体通读深度低于预期**。core/skill 是已知注入面（DPP-008/009），建议后续单独补一轮。

**③ 纯静态审计**

全部结论未经运行时验证。

**④ 未覆盖维度**

Firefox 特化分支、`packages/shell-host` 原生二进制（C++ 侧，非 TS，工具不适用）、构建产物与依赖供应链。
