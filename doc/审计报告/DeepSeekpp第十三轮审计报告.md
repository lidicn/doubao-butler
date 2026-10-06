# DeepSeek++ 第十三轮审计 — 首次覆盖 Node 侧原生宿主

```
Audit target : github.com/lidicn/deepseekpp
Coverage     : packages/shell-host/**/*.mjs（18 文件，3142 行）
               前十二轮完全未覆盖（工具链只认 .ts/.tsx）
Optimization : audit-kit 新增 --exts 参数，支持非 TS 扩展名
Audit date   : 2026-10-05
Mode         : Audit-Only
```

---

## 一、本轮为什么派这个

第十二轮评估报告里我把原生宿主列为"下一轮首选"，理由是：

> `packages/shell-host/native/*.mjs` 14 文件 —— 12 轮从未审过。这是 Node 侧代码，
> 涉及文件路径处理、进程执行，**安全风险高于普通 TS 模块**。

而且它是**结构性地被漏掉的**：前十轮的覆盖率台账一直显示"49/49 模块、451/451 文件、100% 覆盖"，
但这个 100% **只统计了 `.ts`/`.tsx`**。18 个 `.mjs` 文件从来不在统计口径里 ——
所以"全覆盖达成"这个说法本身就带着口径盲区。

这是本轮最重要的发现，比具体 finding 更重要：**覆盖率指标的口径本身会骗人。**

---

## 二、工作流优化：`--exts` 参数

```
问题：walk_sources / scan.py / graph.py 默认 exts=('.ts', '.tsx')
     → .mjs 文件从未进入任何自动化流程
修复：bin/audit 新增 --exts {ts,mjs,all}
     scan.py / graph.py 的 main 支持 --exts 逗号分隔并透传给 scan()/build()
```

验证：

```bash
python3 lib/scan.py <target> packages -o /tmp/scan_mjs.json --exts .mjs
# → 18 文件、10 命中
```

**但结果暴露了另一个问题**：命中的 10 条全部是"空 catch"，**TS 规则对 .mjs 基本不触发**。

原因不难理解：`scan.py` 的模式是为 TypeScript 写的（判别联合、`chrome.*` API、TS 特有语法）。
Node 侧的风险模式（spawn 参数、路径 resolve、process.exit）一条都没覆盖。

**所以 .mjs 只能靠人工通读。** 这一点值得记进方法论：**换语言/运行时时，模式库要重写，
不能指望复用。**

---

## 三、定罪结果：净新增 6 条（2 条 P2 + 4 条 P3）

派了 3 个子 agent 分三组通读，全部结论经我回源码复核。

### [P2] `args.shell` 无白名单，直接作为 spawn 的 file 参数

- **位置**：`packages/shell-host/native/session-provider.mjs:42`

```js
const requestedShell = typeof args.shell === 'string' ? args.shell.trim() : '';
const shellBin = requestedShell || DEFAULT_SHELL;
// → spawn(shellBin, ...)
```

- **复核**：`requestedShell` 只做 `typeof` + `trim`，**没有白名单、没有路径校验**，直接进 `spawn`。子 agent 实测相对路径 `sh` 可成功 exec。
- **需要说清的边界**：**命令注入不成立** —— `spawn` 用 `shell: false`，注入载荷会 ENOENT。这条是"可指定任意可执行文件作为持久 shell"，不是 RCE。

### [P2] native 文件工具无 trusted-root 作用域

- **位置**：`packages/shell-host/native/file-provider.mjs:121`

```js
function resolveLocalPath(input) {
  return path.resolve(trimmed);        // ← 裸 resolve，接受绝对路径 / ~/ / ..
}
function resolveUnderRoot(root, input) {
  // 有 .. 逃逸检查 —— 但只用于 Skill 根
}
```

- **复核**：`local_file_stat` / `read` / `write` 全部走 `resolveLocalPath`，**无根校验**。同文件里带边界检查的 `resolveUnderRoot` 存在，但只用于 Skill 根。
- **关键**：`core/trusted-directory/` 那套机制**只被 `entrypoints/sidepanel` 使用，不参与 native 工具授权**。native 侧唯一的闸门是 `core/shell/policy.ts` 的 allowlist。
- **放大链**：这与 **DPP-043**（导入本地 Skill 时静默追加 `local_file_write`）叠加 —— allowlist 被静默扩展后，`local_file_write` 就直接落到一个无根校验的裸 `resolve()` 上了。

### [P3] ×4

| 条目 | 位置 | 说明 |
|---|---|---|
| shellSessions 无数量上限 | `session-provider.mjs:15` | 只有 idle TTL 兜底，非纯泄漏，故 P3 |
| 校验和不可用时静默跳过验证 | `officecli-installer.mjs:133` | 见下方"剔除与降级"说明 |
| PATH 写入子串判重 | `officecli-installer.mjs` | 可能误判相似目录 |
| 无效帧 `process.exit(1)` | `framing.mjs:9` | 一条坏帧终止整个宿主进程，所有会话丢失 |

---

## 四、剔除与定级调整

### ❌ Zip Slip（native 侧）—— 不成立

`file-provider.mjs` 没有解压归档的代码路径，只有文本读写与目录列举。Zip Slip 在这个模块无从谈起。

### ❌ process-provider 资源泄漏 —— 不成立

`execCommand` 的 `SIGTERM → 3s → SIGKILL` 与 `clearTimers` 在 `error` / `close` **双路径都配对调用**。子 agent 实测确认，我也回源码看了。清理是完整的。

### ⬇️ 校验和 fail-open：从"高危"降到 P3

子 agent 原报这条为中高危，我复核后降级，理由要说清：

```js
// 校验和文件下载失败 → console.log + return（跳过验证，继续安装）
// 条目缺失           → console.log + return（同上）
// 校验和不匹配       → throw（fail-closed ✓）
```

**真正 fail-closed 的那条（不匹配 → throw）是对的。** fail-open 只发生在"拿不到校验和文件"这条路径上，而两个下载源（`d.officecli.ai` 与 GitHub）**都是 HTTPS** —— 要让攻击者命中这条路径，需要先能阻断 HTTPS 请求（MITM 级能力）。

有 MITM 能力时，攻击面远不止"跳过校验和"。所以定为 **P3 纵深防御缺口**，不是可执行漏洞。

---

## 五、覆盖率口径的修正

本轮暴露：此前"100% 覆盖"的统计口径**只含 `.ts`/`.tsx`**，18 个 `.mjs` 从未计入。

已做的修正：

1. `coverage.py` 显式补记 `packages/shell-host`（Node 侧 18 文件）
2. `cluster.py` 新增"原生宿主"子系统归属，避免 6 条新 finding 全落进"其他"兜底桶
3. 台账新增 `extra.node_side_files` 字段，标明 TS 口径外的文件集

**教训**：覆盖率指标必须说清"覆盖的是什么集合"。一个 100% 的数字如果不带口径，可能只是盲区的另一种表达。

---

## 六、问题库与聚类

共 **123 条**：69 条 finding + 54 条已证伪误报。本轮新增 6 条 finding（DPP-064~069）+ 2 条误报（FP-053/054）。

聚类：

```
★★ 整体重写：自动化调度（状态机/事务错序 67%）
★  集中修复：侧边栏 UI、编排入口、工具授权、持久化、原生宿主(6 条，根因分散)
   扩展配置：3 条（P3）
```

**原生宿主 6 条但根因分散**（主导 33%），所以是"逐个修"而非"整体重写"。

---

## 七、十三轮趋势

| 轮次 | 方法 | 净新增 |
|---|---|---|
| 1-2 | 人工通读 / skill 四阶段 | 53 |
| 3-5 | 图谱提名 / 全量覆盖 | 19 |
| 6-8 | focus / 台账 / 装箱 | 8 |
| 9-11 | 测试缺口 / 聚类 / manifest / 协议 | 8 |
| 12 | 死代码工具化 | 5 |
| **13** | **Node 侧原生宿主（新口径）** | **6** |

本轮换了"文件口径"这个维度，净新增 6 条（含 2 条 P2），**是三轮以来最多的一次**。

**但这没有推翻"静态审计到头"的判断** —— 因为这 6 条来自一个此前完全空白的区域，
属于补盲区，不是存量区域挖出新东西。存量区域仍然出清。

---

## 八、局限

**① 模式库对 .mjs 无效**

`--exts .mjs` 跑出 10 条命中全是"空 catch"，Node 特有风险模式（spawn 参数、路径 resolve、process.exit）零覆盖。本轮全靠人工通读，自动化工具在这个口径上基本没帮上忙。

**② native 侧无测试**

`packages/shell-host` 无内嵌测试，根目录 tests 只有 5 个文件涉及 shell-host，且 `package-metadata` / `process-provider` / `session-provider` / `skill-provider` **零测试引用**。所有结论未经运行时验证。

**③ 仍无法运行**

native host 需要真实 native messaging 环境才能跑，本轮全部是静态阅读。DPP-064 的"相对路径 sh 可 exec"是子 agent 实测的，其余均为源码推断。

**④ 未覆盖的 Node 侧**

本轮只看了 `packages/shell-host`。若项目还有其他 `.mjs`/`.js` 构建脚本（`scripts/`），同样未审。

---

## 九、下一步

剩余静态维度（按性价比）：

1. **`scripts/` 构建脚本**（小，快速）—— 同样是 TS 口径外的盲区，本轮的教训直接适用
2. **依赖供应链**（依赖树 / CVE / 许可证）—— pkginstall 打通后可行性提升
3. **Firefox 特化分支** —— manifest 有 firefox 分支，代码侧差异未审

**收益最高的仍是换环境做运行时验证**，但那是外部条件问题，不是轮次问题。

**P1 清单（7 条，始终有效）**：DPP-005 凭据明文落盘 / DPP-006 console 钩子写宿主 localStorage /
DPP-029 telemetry promptText 落盘 / DPP-001 sync 无超时 / DPP-002 自动化删除不等待终态 /
DPP-034 deadlineAt 可空 / DPP-043 导入 Skill 静默追加写入授权。

**建议与 DPP-043 联动修复**：DPP-066（native 无根校验）与 DPP-043（allowlist 静默扩展）
构成一条完整链路，单独修任何一个都不够。
