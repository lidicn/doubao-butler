# DeepSeek++ 第九轮审计 — 换方法论：从"再扫一遍"到"换信号源 + 跑起来验证"

```
Audit target : github.com/lidicn/deepseekpp (DeepSeek++ v1.16.0)
Toolkit      : /data/workspace/audit-kit
Signal       : 测试覆盖缺口（替代已饱和的模块覆盖信号）
New attempt  : 首次实际运行 TypeScript 类型检查（前八轮从未成功）
Audit date   : 2026-10-05
Mode         : Audit-Only（不改代码）
```

---

## 一、为什么要换方法论

第八轮达成模块级全覆盖（49/49 模块、451/451 文件），但净新增降到 1 条。**继续用同一套方法扫同样的区域，边际收益接近零。**

本轮换了两件事：

1. **信号源**：从"模块是否被派发过"改为"**测试覆盖缺口**" —— 零测试的模块意味着缺陷既没被审计发现、也没被测试兜住
2. **首次真正跑起来验证**：八轮都是纯静态阅读，`tsc` 一次都没跑成功过

---

## 二、首次运行 TypeScript 类型检查（有进展，但未达可用）

### 2.1 突破：找到了可用的 TypeScript 副本

项目 `node_modules` 里 typescript 包体不全（`lib/typescript.js` 缺失），`npm install typescript` 也装不全。前八轮因此从未跑过类型检查。

本轮在沙箱里找到了一份**完整的 TypeScript 5.5.2**（code-server 自带），封装成 `lib/typecheck.py` 跑通了编译。

```bash
python3 lib/typecheck.py /data/workspace/deepseekpp-main -o out/typecheck.json
```

### 2.2 两次配置踩坑（都是假阳性，必须记录）

**第一次：4748 条错误，全是噪声**

```
2029  TS2705   An async function or method in ES5 requires the 'Promise' constructor
 343  TS2583   Cannot find name 'Set'
 327  TS2585   'Promise' only refers to a type
```

原因：项目 `tsconfig.json` extends `./.wxt/tsconfig.json`，而 `.wxt/` 是 `wxt prepare` 生成的、本沙箱未跑过构建因而不存在。缺了它 target/lib 回落 ES5。

**修正**：显式指定 `target: ES2022` + 现代 lib。错误降到 **475 条**。

**第二次：19 条 `.error` 假阳性**

```
core/automation/scheduler.ts:235  Property 'error' does not exist on type 'AutomationRunnerResult'
```

这一度看起来像系统性真实 bug（19 条集中在 scheduler/store/UI）。但我做了最小复现验证：

```ts
interface OK { ok: true; a: string; }
interface Err { ok: false; error: { code: string }; }
type R = OK | Err;
r.ok ? 'y' : r.error.code    // ← 最小复现：0 错误
```

最小复现通过，说明是配置问题。根因：**判别联合（discriminated union）窄化在 `strictNullChecks` 关闭时不生效**，而默认配置 `strict: false`。

**修正**：加 `strict: true`。19 条假阳性全部消失 —— 又一次验证"先验证机制再下结论"的必要性。

### 2.3 最终结论：无法给出可信诊断

开启 strict 后有 836 条诊断，剔除依赖缺失（TS2307/2591）后仍有 427 条，但主体是：

```
75  TS7006  Parameter '_input' implicitly has an 'any' type
47  TS7006  Parameter 'handlers' implicitly has an 'any' type
10  TS7016  Could not find a declaration file for module '@earendil-works/pi-agent-core'
```

全是**依赖缺失的级联噪声** —— `@earendil-works/pi-ai`、`vitest`、node 类型等解析不到，导致下游类型全变 `any`。

**结论：这个沙箱跑不出可信的 tsc 结果。** 真实环境需要完整 `node_modules` + `wxt prepare` 生成的 `.wxt/tsconfig.json`。

这个尝试的价值在于：① 排除了"配置问题导致的假阳性"这类陷阱；② 确认了类型检查这条路在沙箱内走不通，不必再试。

---

## 三、换用测试覆盖缺口作为信号源

既然 tsc 走不通，改用**测试覆盖**这个此前从未用过的信号。

统计方式：从 220 个测试文件中 grep 被测模块的 import 路径，反推每个模块的测试引用数。

**零测试引用的模块**：

| 模块 | 源文件数 | 代码行数 | 测试引用 |
|---|---|---|---|
| core/i18n | 27 | 614 | **0** |
| core/sandbox | 6 | 721 | **0** |
| core | 5 | — | 0 |
| entrypoints | 4 | — | 0 |
| core/background / browser / debug / model / remote-agent / theme / tool-loop | 8 | — | 0 |
| sandbox-offscreen / sandbox-runner | 2 | — | 0 |

`core/sandbox`（721 行）是**安全边界** —— 执行不受信任代码的 iframe/worker，零测试尤其值得警惕。选它 + `core/i18n` 作为本轮目标。

---

## 四、定罪结果：净新增 3 条

子 agent 报 3 条 + 自我排除 6 条，我复核后**全部保留但调整 2 条级别**。

### [P2] Pyodide 首次加载失败后，Python 工具永久不可用

- **位置**：`core/sandbox/python-worker.ts:11`

```ts
let pyodidePromise: Promise<PyodideRuntime> | null = null;

function getPyodide(pyodideBaseUrl: string): Promise<PyodideRuntime> {
  if (!pyodidePromise) {
    pyodidePromise = loadPyodide({ indexURL: pyodideBaseUrl, packageBaseUrl: pyodideBaseUrl });
  }
  return pyodidePromise;      // ← reject 后永久缓存这个失败态
}
```

- **复核**：子 agent 定 HIGH，我**降为 P2**。机制成立且我回源码确认了 —— `pyodidePromise` 只在 `null` 时才赋值，`loadPyodide` reject 后这个 rejected promise 被永久复用，**没有任何重建或重试路径**。
- **降級理由**：影响是"Python 工具功能不可用"，非数据丢失或越权；且 worker 可被重建（非进程级永久损坏）。
- **触发**：Pyodide CDN 首次不可达 / 网络抖动。这是真实可达的场景。

### [P3] onmessage 中 stdin 可被后到请求覆盖

- **位置**：`core/sandbox/python-worker.ts:13`
- **说明**：`self.onmessage` 是 async，`setStdin` 在 `await getPyodide` 之前执行，并发请求交错时后到请求会覆盖前一个的 stdin。属并发竞态，实际触发需要并发 Python 执行。

### [P3] i18n 缺失键 throw，可扩散为整次工具调用失败

- **位置**：`core/i18n/runtime.ts:192`

```ts
const value = readResourcePath(resources[locale.locale], key);
if (typeof value !== 'string') {
  throw new Error(`Locale key "${key}" is not a string message`);
}
```

- **复核**：项目自述不变量要求"未知版本/损坏数据必须显式失败"，这条**符合**不变量。问题在于它用在 `translate` 层 —— 单条文案缺失会让整次工具调用中断。fail-loud 用成了 fail-stop。
- **定为 P3**：符合不变量，属于语义选择问题而非缺陷。

---

## 五、剔除 1 条

### ❌ "sandbox-runner 身份校验可被扩展同源上下文伪造" → 剔除

子 agent 定 MEDIUM，称 runner 是扩展自身页面（非 opaque origin），扩展同源上下文可伪造 `frameResult`。

我查了 `wxt.config.ts`：

```ts
sandbox: { pages: ['sandbox-runner.html'] },
```

`sandbox-runner.html` 是 manifest 的 `sandbox.pages`，**不在 `web_accessible_resources` 列表内**，外部页面不可达。伪造还需猜中 `crypto.randomUUID()` 生成的 requestId。而"扩展同源上下文本就是可信边界的一部分"。

降为加固建议，不计入缺陷。

---

## 六、问题库状态

共 **102 条**：45 条 finding + 50 条已证伪误报。本轮新增 3 条 finding（DPP-051~053）+ 2 条误报（FP-048/049）。

---

## 七、九轮趋势

| 轮次 | 方法 | 净新增 | 剔除 |
|---|---|---|---|
| 第一轮 | 模块人工通读 | 25 | — |
| 第二轮 | deep-audit skill 四阶段 | 28 | — |
| 第三轮 | 图谱驱动提名 | 5 | 7 |
| 第四轮 | 整合工作流 + 增量 | 5* | 3 |
| 第五轮 | 全量覆盖 | 9 | 4 |
| 第六轮 | 覆盖驱动 focus | 2 | 5 |
| 第七轮 | 修正覆盖度量 | 5 | 2 |
| 第八轮 | 台账 + 装箱 | 1 | 7 |
| **第九轮** | **测试覆盖缺口 + tsc 尝试** | **3** | **1** |

*第四轮原始记录 2 条，含后续补录调整为 5。

本轮换信号源后净新增回到 3 条，说明**换维度确实能挖出新东西**，但绝对量仍处于低位。

---

## 八、局限

**① 类型检查仍未跑通**

本轮突破了"找不到 TS 编译器"这一层，但卡在依赖缺失。所有类型层面的缺陷（如未定义引用、类型不匹配的真实 bug）**仍未验证**。这是九轮下来最大的一块空白。

**② 测试覆盖统计是弱代理**

用"测试文件里 grep 到的 import"反推，会漏掉间接测试（通过 barrel 导出、集成测试覆盖）。`core/i18n` 零引用不代表零覆盖 —— 可能通过 UI 集成测试间接覆盖。

**③ 测试本身跑不了**

220 个测试文件存在，但 `vitest` 缺失，一个都跑不了。否则可以用测试反向验证已入库 finding 的可达性。

**④ 子 agent 覆盖面**

本轮只派了 1 个子 agent 覆盖 2 个模块（33 文件），是历轮最少。

---

## 九、下一步建议

按性价比排序：

**① 在有完整依赖的环境跑一次 `tsc --noEmit` + `vitest`（收益最高）**

这是九轮下来唯一没做成、且能直接验证多条 finding 的事。需要：完整 `npm install` + `wxt prepare`。在本沙箱的 registry 限制下做不到，换环境即可。

**② 针对性重构审查：工具授权子系统**

DPP-025 / 043 / 045 / 046 / 047 / 049 六条集中在此，单独看都是 P2/P3，合起来指向同一套状态机设计问题。逐个修不如整体重写。

**③ 剩余零测试模块**

`core`（5）、`entrypoints`（4）、`core/model`、`core/theme`、`core/browser`、`core/background`、`core/remote-agent`、`core/tool-loop`、`sandbox-offscreen` —— 合计约 15 文件，可一轮消化。
