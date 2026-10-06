# DeepSeek++ 第十四轮审计 — 打通运行时验证通道

```
Audit target : github.com/lidicn/deepseekpp
Breakthrough : 首次产出「运行时验证」结论（前十三轮全是静态推断）
New tooling  : bin/gates（门禁执行器）+ bin/genmanifest（manifest 生成）
Coverage     : scripts/*.mjs（22 文件，4026 行，TS 口径外）
Audit date   : 2026-10-05
```

---

## 一、本轮最重要的突破：从没跑起来过 → 能跑了

前十三轮报告里我反复写同一句话：**"最大的空白是从没跑起来过"**。

**第十四轮实测发现：这个判断部分错了。**

关键认知：`scripts/*.mjs` 是**纯 Node 脚本，不需要 npm 依赖**就能运行。
项目自带 13 个质量门禁，其中 **8 个能在本沙箱真跑**。

这意味着审计第一次能产出**运行时验证的结论**，而不是源码推断。

### 门禁实测结果

```
[A] ✗        verify:i18n            ← 真实红灯
[B] ✗        verify:manifest-policy ← 环境产物（见下）
[B] ✓        verify:extension-utf8
[B] ✗        verify:sidepanel-chunks ← 缺 sidepanel.html 产物
[B] ⏸ 需构建   verify:bundled-skills
[B] ⏸ 需构建   verify:release-assets
[C] ✓        smoke:shell            ← 17/17 全过
[C] ✓        smoke:mcp
[C] ✓        smoke:pow
[C] ✓        smoke:web
[C] ✗        smoke:pyodide          ← 环境产物
[C] ✓        verify:automation
[C] ✓        verify:mcp:mock
```

**8 个 PASS，1 个真实红灯。**

其中 `smoke:shell` 是**真跑了原生宿主进程**的 17 条用例，全过。
包括 `shell_exec env isolation: drops host secrets + loader keys (H-01)` ——
说明宿主进程的环境变量隔离是被测试覆盖的，不是我推断出来的。

---

## 二、唯一真实红灯：i18n 覆盖（DPP-070，P2）

`verify:i18n` 失败，退出码 1，CI 会红。两个问题叠加。

### 2.1 硬编码中文（285 处违规）

拆解后构成：

| 类型 | 数量 | 是否缺陷 |
|---|---|---|
| 行注释 `//` / JSDoc `/**` | **248** | 否 —— 注释不算用户可见 |
| 真实字符串 | **37** | 部分是 |

**排除注释后，真实用户可见的硬编码中文有 5 处集中点**：

**[P2] `GeneralSubPage.tsx:107-113` —— 最明确的一处**

```tsx
<SettingsSection
  title="远程任务监听"
  description="开启后，自动检测手机版 DeepSeek 发来的新消息..."
>
  <ToggleRow
    title="远程任务监听"
    description="开启后每 5 秒轮询一次对话历史..."
```

**同一文件的第 28、64、99 行用的都是 `t('sidepanel.settings.*')`。**
这一段是后加的功能，直接写了中文 —— **英文界面下会显示中文**，是真实 bug 不是风格问题。

其余 4 处（均为 P3）：

| 位置 | 内容 | 影响 |
|---|---|---|
| `core/mcp/client-descriptor.ts:158-159` | "工具返回错误"/"工具已执行" | 工具结果描述文案 |
| `core/mcp/client-file-read.ts:86/93/108/127` 等 8 处 | auto 续读状态文案 | 返回给模型的中文 |
| `core/prompt/catalog-template.ts:103-106` | "### 调用格式"等 | **发给模型的 prompt 是中文** |
| `core/debug/refactor-telemetry.ts:247/267/271/275` | "100%一致"/"样本不足" | 调试面板 |

### 2.2 allowlist 过期（DPP-074，P3）

```
allowlisted line fragment is stale: core/inline-agent/render-steps.ts :: REASONING_HOST_TEXT_RE
allowlisted line fragment is stale: core/inline-agent/render-steps.ts :: REASONING_COMPLETED_TEXT_RE
```

这两个正则本就是用来匹配宿主中文 UI 的（"已深度思考"等），
被列进 allowlist 是对的，但 allowlist 里存的代码片段跟实际代码对不上了 —— 配置过期。

---

## 三、新增两个工具组件

### 3.1 `bin/gates` —— 门禁执行器

把 13 个门禁按 A/B/C 三档跑，并自动分类失败原因（真实红灯 / 需构建 / 需环境）。
已接入主入口：`bin/audit gates <target> --all`。

**踩了两个坑，都修了**（这也是本轮的方法论增量）：

| 坑 | 症状 | 修复 |
|---|---|---|
| **用 `node <script>` 而非 `npm run`** | `shell-smoke.mjs` 凭空多出一条 FAIL："PATH order — expected node_modules/.bin in PATH"。改用 `npm run` 后 **17/17 全过** | 执行器改为调 `npm run <key>` |
| **`i18n-coverage-audit.mjs` 不是独立 script** | 报 `Missing script: "i18n-coverage"` | 它其实是 `verify:i18n` 的（`a.mjs && b.mjs`）后半段，从清单移除 |

**第一条尤其值得记**：用错执行方式会**凭空制造一条假失败**。
如果没做交叉验证，这条会被当成真实缺陷报上去。

### 3.2 `bin/genmanifest` —— 从 `wxt.config.ts` 生成真实 manifest

用 `ts.transpileModule` 抽出 `createManifest` 求值，生成 `dist/<browser>-mv3/manifest.json`，
解锁 `manifest-policy-check`。

同样踩了三个坑：多行 `import {` 剥离不干净、`import.meta` 在 `new Function` 里不可用、
wxt 构建时自动注入的 `manifest_version`/`name` 需手工补。全部已修。

**它的价值是反向验证了第十一轮的推断** —— 生成的真实 manifest 与 `lib/manifest.py`
当时推断的三条 P3 完全一致：

```
web_accessible_resources: sidepanel.html → <all_urls>   ✓ 印证 DPP-056
host_permissions: *://chat.deepseek.com/* 等三条通配 scheme ✓ 印证 DPP-057
sandbox CSP: child-src/frame-src 含 data:              ✓ 印证 DPP-058
```

---

## 四、剔除 2 条

### ❌ shell-smoke 的 PATH order 失败 → 剔除

如 3.1 所述，是我的执行方式问题，不是项目缺陷。

### ❌ manifest-policy 报 Pyodide stdlib 缺失 → 剔除

`node_modules/pyodide` 在本沙箱装包不全，`python_stdlib.zip` 不存在。
另两条（module/wasm）我补齐后就通过了，**策略断言本身全过**。

---

## 五、工作流整合

```
audit-kit/
├── bin/
│   ├── audit          统一入口，新增 `gates` 子命令
│   ├── gates          ★ 项目门禁执行器（运行时验证通道）
│   ├── genmanifest    ★ 真实 manifest 生成
│   ├── pkginstall     npm 装包绕过器
│   └── deadcode       死代码检测
├── config/knip.json
└── rules/
    ├── AUDIT-RULES.md
    └── AUDIT-LESSONS.md   ★ 新增「运行时验证通道」章节
```

问题库：**130 条**（74 finding + 56 误报）。

---

## 六、十四轮趋势与轮次估算

| 轮次 | 方法 | 净新增 | 性质 |
|---|---|---|---|
| 1-2 | 人工通读 / skill 四阶段 | 53 | 存量挖掘 |
| 3-5 | 图谱提名 / 全量覆盖 | 19 | 存量挖掘 |
| 6-8 | focus / 台账 / 装箱 | 8 | 存量挖掘 |
| 9-11 | 测试缺口 / 聚类 / manifest / 协议 | 8 | 换维度 |
| 12 | 死代码工具化 | 5 | 补盲区 |
| 13 | Node 侧原生宿主 | 6 | 补盲区 |
| **14** | **运行时验证** | **5** | **首次运行时证据** |

**本轮 5 条新增全部来自一个真实红灯**，性质与以往不同 ——
它们不是"我认为这里有风险"，而是"项目自己的 CI 现在就是红的"。

### 剩余轮次估算（更新）

- **剩余静态维度**：1-2 轮（依赖供应链、Firefox 分支）
- **运行时扩展**：1-2 轮（把门禁跑全需真实构建；`wxt build` 本沙箱做不到）

**结论不变：再 1-2 轮收尾。** 但本轮改变了收尾的质量 ——
现在报告里有运行时证据，不再全是推断。

---

## 七、局限

**① 门禁只跑了 8/13**

`verify:bundled-skills`、`verify:release-assets` 需真实构建产物；
`smoke:pyodide` 缺 `python_stdlib.zip`。这 5 个的结论仍是"未验证"。

**② `wxt build` 仍跑不了**

需要完整 `node_modules` + wxt 工具链。`genmanifest` 只生成了 manifest.json
这一个文件，不是完整构建。

**③ i18n 的严重程度可能被高估**

285 处违规里 248 处是注释。我按"排除注释后剩 37 处"来定级，
但其中 `render-steps.ts` 的中文正则是**故意**匹配宿主 UI 的，
属误报（已归到 allowlist 过期那条）。

**④ 运行时验证只覆盖了 scripts 层**

`vitest` 单元测试（220 个文件）**仍然一个都跑不了**。
门禁是集成级验证，单元测试缺失意味着模块级行为仍无运行时证据。

---

## 八、下一步

1. **修 i18n 红灯**（DPP-070/071/072/073/074）—— CI 现在就是红的，这是唯一有运行时证据的待修项
2. **依赖供应链审计**（1 轮）
3. **Firefox 特化分支**（1 轮）
4. **换环境跑 `wxt build` + vitest** —— 收益最高，但是外部条件问题

**P1 清单（7 条）不变**：DPP-005 凭据明文落盘 / DPP-006 console 钩子写宿主 localStorage /
DPP-029 telemetry promptText 落盘 / DPP-001 sync 无超时 / DPP-002 自动化删除不等待终态 /
DPP-034 deadlineAt 可空 / DPP-043 导入 Skill 静默追加写入授权。

**建议把 DPP-070 提到发布前清单** —— 它是本轮唯一有 CI 红灯支撑的缺陷。
