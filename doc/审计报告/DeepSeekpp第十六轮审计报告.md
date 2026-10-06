# DeepSeek++ 第十六轮审计 — Firefox 分支收尾 + 十六轮总结

```
Audit target : github.com/lidicn/deepseekpp
Focus        : Firefox 特化分支（最后一块未覆盖静态维度）
New tooling  : bin/summary（跨轮次汇总生成器）
Finding      : 0 条新增
Audit date   : 2026-10-05
```

---

## 一、Firefox 分支：0 条新增，但差点报出一条 P1

### 差点上报的 P1（已证伪，记 FP-061）

生成的真实 manifest 里，**firefox 没有 `sidebar_action`**，
而 `entrypoints/background.ts:805` 的 `registerFirefoxActionClickOpen()` 依赖它：

```ts
const sidebarAction = readOptionalChromeApi(() => chrome.sidebarAction);
if (!action?.onClicked?.addListener || !sidebarAction?.open) return;   // ← 提前返回
```

看起来是：**firefox 用户点工具栏按钮没反应，侧边栏永远打不开** —— 核心功能全废，P1。

**按纪律去查了 WXT 源码**（`wxt/dist/core/utils/manifest.mjs:165`）：

```js
if (wxt.config.browser === "firefox")
  manifest.sidebar_action = { default_panel: page, ... };
else if (manifestVersion === 3) {
  manifest.side_panel = { default_path: page };
  addPermission(manifest, "sidePanel");
}
```

**WXT 会为 firefox 自动生成 `sidebar_action`** —— chromium 才生成 `side_panel`。
`chrome.sidebarAction` 在 firefox 下是存在的。**假设证伪。**

这是本轮最有价值的一条记录：**又一次证明"先验证机制再下结论"不是形式主义**。
如果没查 WXT 源码，这条会以一个非常自信的 P1 姿态进报告。

### 另外两条同样证伪

| 假设 | 实际 |
|---|---|
| firefox 缺 `tabs` 权限 → `chrome.tabs.*` 抛错 | MV3 下 query 无需权限，仅 url/title 受限；且所有读取点都有 `tab.url ?? ''` 守卫（`browser-control/service.ts` 96/106/234/657/829/838） |
| firefox 无 offscreen → 沙箱静默失败 | `core/sandbox/tool.ts:76-89` 显式返回 `sandbox_runtime_unavailable` + i18n 文案，是 fail-closed |

### Firefox 与 Chromium 的实际差异（确认无缺陷）

```
权限    chromium: storage alarms nativeMessaging contextMenus offscreen debugger tabs identity sidePanel
        firefox : storage alarms nativeMessaging contextMenus identity
```

三个缺失权限（offscreen / debugger / tabs）**全部被能力检测链闭合**：

- `requestOffscreenSandboxRun` 前有 `readOptionalChromeApi` 探测
- `core/browser-control` 经 `capabilities.ts` → `service.ts:isSupported()` →
  `tool.ts:shouldExposeBrowserControlTools()` 三层门控，firefox 下整体不可达

四个含 FIREFOX 分支的文件逐个核验，分支完备无遗漏。

---

## 二、⚠️ 一个必须更正的数据：P1 是 12 条，不是 7 条

**这是本轮新增的 `bin/summary` 工具查出来的，也是我做错了一件事。**

从第三轮起，我在每份报告的结尾都写"**P1 清单（7 条）**"，列的是：

```
DPP-005 凭据明文落盘 / DPP-006 console 钩子 / DPP-029 telemetry /
DPP-001 sync 无超时 / DPP-002 自动化删除 / DPP-034 deadlineAt / DPP-043 Skill 静默授权
```

**但问题库里实际有 12 条 P1。** 漏掉的 5 条：

| ID | 位置 | 内容 |
|---|---|---|
| **DPP-003** | `core/sync/operation-coordinator.ts:74` | sync 配置提交先于数据落盘，通知失败被硬编码为 `effectCompleted` |
| **DPP-004** | `core/inline-agent/pi/loop-adapter.ts:514` | finalize 运算符优先级错误，错误回合遇取消被上报为"完成" |
| **DPP-007** | `core/remote-agent/watcher.ts:251` | Remote Agent 重发不校验结果，且可能发到已切换的会话 |
| **DPP-008** | `core/skill/github-importer.ts:660` | GitHub Skill 导入未校验 frontmatter 直接注入 system prompt |
| **DPP-009** | `core/skill/local-importer.ts:530` | 本地 Skill 导入路径未 canonicalize |

**成因**：那 7 条是我早期手工整理的清单，之后每轮复制粘贴续用。
问题库在后几轮持续新增，但清单没同步 —— **手工维护的摘要与数据源脱节**。

**修法**：已加 `bin/summary`，P1 清单改由问题库自动生成，不再手工维护。

（顺带：其中 DPP-004、DPP-007、DPP-008、DPP-009 都是第一轮就报出来的，
属于最早发现的那批，反而因为"太早"被后续清单遗漏了。）

---

## 三、新增工具：`bin/summary`

十多轮下来问题库 138 条，但每次交付的都是"本轮增量"。
缺一个能回答"**这项目到底有哪些问题、先修哪个**"的入口。

```bash
bin/audit summary -o out/汇总.md
```

四张视图：严重度分布 / P1 清单 / 按子系统 / **证据强度标注**。

第四张是刻意加的 —— 16 轮下来只有 **1 条** finding 有运行时证据
（DPP-070，第十四轮门禁实测），其余 74 条全是源码推断。
**这个比例必须显式摆出来**，否则维护者会误以为 75 条都是已验证的。

---

## 四、十六轮总结

### 4.1 产出

| 项 | 数量 |
|---|---|
| finding | **75** |
| 已证伪误报 | **63** |
| 问题库总计 | 138 |
| 模块覆盖 | 49/49 |
| 文件覆盖 | 451/451（TS 口径）+ 40 个 .mjs（13/14 轮补记） |

严重度：**P1 12 / P2 30 / P3 33**

### 4.2 各轮方法演进

```
1-2   人工通读 / skill 四阶段        53 条
3-5   图谱提名 / 全量覆盖            19 条
6-8   focus / 台账 / 装箱             8 条
9-11  测试缺口 / 聚类 / manifest / 协议  8 条
12    死代码工具化（knip）            5 条
13    Node 侧原生宿主                 6 条
14    运行时验证（项目门禁）          5 条
15    依赖供应链 + tsc               1 条
16    Firefox 分支                   0 条
```

**最后三轮 5 → 1 → 0。** 这不是工具退步，是**存量挖完了**。

### 4.3 方法论上真正有用的几条

1. **先验证机制再下结论** —— 16 轮剔除 63 条误报，绝大多数是机制判断错误。
   本轮的 `sidebar_action` 是最惊险的一次。
2. **覆盖率指标必须带口径** —— 第十三轮发现"100%"只统计 .ts，18 个 .mjs 从来不在里面。
3. **换运行时要换模式库** —— `--exts .mjs` 跑出的命中全是"空 catch"，
   Node 特有风险一条没命中，只能靠人工通读。
4. **摘要不能手工维护** —— 本轮的 P1 计数错误就是这么来的。
5. **运行时验证优先于静态推断** —— 一个门禁红灯的价值高于十条推断的 P2。

### 4.4 始终没做成的一件事

**220 个 vitest 测试一个都没跑起来。**

第十五轮把 tsc 打通了（补 2 个包 + 手写 tsconfig），
但 vitest 二进制能启动、`run` 必 Bus error(135)，threads/forks 两模式均复现。
**75 条 finding 里只有 1 条有运行时证据**，根源就在这里。

---

## 五、给维护者的结论

### 发布前必修（P1，12 条，自动生成）

| ID | 位置 | 问题 |
|---|---|---|
| DPP-001 | `core/sync/oauth-client.ts:133` | sync 全链路 fetch 无超时，远端挂起永久阻塞 |
| DPP-002 | `core/automation/scheduler.ts:214` | 删除任务不等终态，可能留永久 running 租约 |
| DPP-003 | `core/sync/operation-coordinator.ts:74` | 配置提交先于落盘，`effectCompleted` 硬编码 |
| DPP-004 | `core/inline-agent/pi/loop-adapter.ts:514` | finalize 优先级错误，错误回合被报成完成 |
| DPP-005 | `useSettingsController.ts:832` | refreshToken / 密码 / clientSecret 明文落盘 |
| DPP-006 | `entrypoints/content.ts:5475` | console 钩子把全量输出写宿主 localStorage |
| DPP-007 | `core/remote-agent/watcher.ts:251` | 重发不校验结果，可能发到已切换会话 |
| DPP-008 | `core/skill/github-importer.ts:660` | frontmatter 未校验直接注入 system prompt |
| DPP-009 | `core/skill/local-importer.ts:530` | 导入路径未 canonicalize |
| DPP-029 | `core/debug/refactor-telemetry.ts:115` | 遥测默认开启，完整 promptText 落盘 |
| DPP-034 | `core/automation/runner.ts:64` | deadlineAt 可空，未传时无超时可永久挂起 |
| DPP-043 | `core/shell/policy.ts:67` | 导入 Skill 静默追加 local_file_write |

**另建议加 DPP-070**（i18n 硬编码中文）——虽是 P2，但是唯一有 CI 红灯支撑的。

### 建议整体重写而非逐个修

**自动化调度**（DPP-002/011/012/016/034，状态机/事务错序占 67%）。
根因是这套状态机没有"终态"概念，把取消/中断当普通失败处理，
持久化状态与进程内租约缺少事务性关联。

### 联动修复

**DPP-066（native 无 trusted-root）+ DPP-043（allowlist 静默扩展）** ——
这两条串起来才完整：allowlist 被静默扩展后，`local_file_write` 就落到
一个无根校验的裸 `resolve()` 上。单独修任何一个都不够。

---

## 六、审计到此为止的理由

最后三个维度（死代码 / 原生宿主 / 依赖供应链 / Firefox）分别产出 5 / 6 / 1 / 0 条，
且最后两轮的新增全是 P3。

**继续用同样方法扫同样区域，边际收益接近零。**

要再挖，只有换方法（不是换区域）：

1. **换环境跑测试**（收益最高）—— 完整 `npm install` + `wxt prepare` →
   `tsc --noEmit` + `vitest run`。第十五轮已证明 tsc 只需补 2 个包就能通，
   220 个测试的价值远高于再扫一轮静态。
2. **动态审计** —— SW 生命周期、真实网络挂起、长会话内存实测。
3. **针对性审查** —— 工具授权子系统攒了 6 条，合起来看是同一套状态机的设计问题。
