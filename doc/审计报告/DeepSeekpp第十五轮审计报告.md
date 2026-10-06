# DeepSeek++ 第十五轮审计 — 依赖供应链 + 类型检查突破

```
Audit target : github.com/lidicn/deepseekpp
Focus        : ① 依赖供应链  ② 让 tsc / vitest 真正跑起来
New tooling  : bin/deps（离线依赖三方对账）
Breakthrough : TypeScript 7.0.2 原生二进制首次成功执行
Audit date   : 2026-10-05
```

---

## 一、突破：TypeScript 7.0.2 跑起来了

**这是 15 轮以来第一次执行到项目自己的 `tsc`。**

三步前置，缺一不可：

```
1. bin/pkginstall typescript@7.0.2
2. bin/pkginstall @typescript/typescript-linux-x64   ← 关键卡点
3. 手工生成 .wxt/tsconfig.json（原版需 wxt prepare）
```

**第 2 步是真正的卡点**：TS7 是原生移植版，`bin/tsc` 只是个壳，
真正干活的是 `@typescript/typescript-linux-x64` 里的原生二进制。
缺它会报 `Command failed: .../tsc --version`。装上后：

```
$ node node_modules/typescript/bin/tsc --version
Version 7.0.2
```

顺带踩了一个坑：**TS7 已移除 `baseUrl` 选项**，我照老习惯写进 tsconfig 直接报 TS5102。

---

## 二、但类型检查结果仍不可信：88% 是环境噪声

844 条诊断的构成：

| 错误码 | 数量 | 成因 |
|---|---|---|
| TS7006 隐式 any 参数 | 337 | 缺 .d.ts 级联 |
| TS2307 找不到模块 | 253 | 包未装全 |
| TS2591 Node 全局缺失 | 153 | 缺 @types/node |
| **合计环境噪声** | **743（88%）** | |

剩余 101 条逐条看过，也全是环境问题：

- `Cannot find name 'defineBackground'` / `defineContentScript` —— **WXT 自动导入**，需 `wxt prepare` 生成 `.wxt/types`
- `Property 'env' does not exist on type 'ImportMeta'` —— 缺 Node 类型
- `Cannot find name 'Buffer'` —— 同上

**结论：没有发现真实类型错误，但也不能据此说"类型干净"。**
这次运行的价值在于证明了路径可行，不在于结论本身。

---

## 三、假阳性：`never[]` 是我自己的配置造成的

```
core/export/service.ts(75): Argument of type 'ExportedSession' is not assignable to 'never'
core/export/service.ts(112): Property 'messages' does not exist on type 'never'
```

这两条**很像真实缺陷**（`const sessions = []` 未标注类型），我差点报上去。

按纪律做了最小复现：

```ts
const sessions = [];
for (const s of [1,2,3]) { sessions.push(s * 2); }
f(sessions);   // f(xs: number[])
```

- `noImplicitAny: false` → **报错**（`[]` 退化为 `never[]`）
- `noImplicitAny: true` → **通过**（evolving array 正常推导）

**是配置假阳性，不是项目缺陷。** 已记入问题库（FP-058）。

这是"先验证机制再下结论"第 N 次生效——前 15 轮剔除的误报里，
这类"工具/配置自身导致的假阳性"占了不少。

---

## 四、vitest：二进制能启动，执行必崩

```
$ node node_modules/vitest/vitest.mjs --version
vitest/4.1.8 linux-x64 node-v20.19.5      ← 正常
```

但 `run` 任何测试都 **Bus error (exit 135)**，
`--pool=threads` 与 `--pool=forks` 两种模式均复现。

疑为 vite 8 / rolldown 原生模块在沙箱崩溃。
**220 个测试文件仍然一个都跑不了**（记 FP-059）。

---

## 五、依赖供应链：走离线路线

`npm audit` 在本沙箱不可用——报 `EAUDITGLOBAL`
（"does not support testing globals"），因为 node_modules 里缺
`.package-lock.json`，npm 把当前目录误判成了全局安装。加 `--package-lock-only` 一样失败。

于是新增 `bin/deps`，做**离线三方对账**（已接入 `bin/audit deps`）。

### 结果

```
声明 19   锁定 701   实际安装 525

真·未安装          1   （wxt，目录为空）
沙箱装包不全       7   ← 环境产物，非缺陷
版本漂移           0
幽灵依赖           0
typosquat 嫌疑     0
package.json ↔ lock 完全同步
```

**唯一值得记的是 DPP-075（P3）**：19 个依赖里 18 个用 `^` 浮动版本，
包括 `pyodide` / `react` / `react-dom` 这类运行时依赖。
lockfile 存在且完全同步可以缓解，但意味着 CI 与用户安装可能取到不同版本。

### 工具自身的三个误报（都已修）

| 误报 | 成因 | 修复 |
|---|---|---|
| "8 个声明但未安装" | 目录在但缺 package.json，是沙箱装包不全特征 | 区分「目录不存在」与「目录在但缺 package.json」 |
| 23 条版本漂移 | `endswith` 命中了嵌套的旧版本目录（`yargs/node_modules/ansi-regex`） | 只匹配顶层 `node_modules/<name>` |
| "normal completion"、"legacy frozen snapshot" | 正则把注释里的英文短语当成包名 | 加包名形状校验 + 传递依赖排除 |

第三条尤其典型——`normal completion` 是从
`// distinguish "stream interrupted" from "normal completion"` 这句注释里抓出来的。

---

## 六、工作流整合

```
audit-kit/
├── bin/
│   ├── audit         统一入口，新增 `deps` 子命令
│   ├── deps          ★ 依赖供应链审计（离线三方对账）
│   ├── gates         项目门禁执行器（第十四轮）
│   ├── genmanifest   真实 manifest 生成（第十四轮）
│   ├── pkginstall    npm 装包绕过器
│   └── deadcode      死代码检测
└── rules/AUDIT-LESSONS.md  ★ 新增第八章：tsc/vitest 突破与残余边界
```

问题库：**135 条**（75 finding + 60 误报）。

---

## 七、十五轮趋势与轮次估算

| 轮次 | 方法 | 净新增 |
|---|---|---|
| 1-2 | 人工通读 / skill 四阶段 | 53 |
| 3-5 | 图谱提名 / 全量覆盖 | 19 |
| 6-8 | focus / 台账 / 装箱 | 8 |
| 9-11 | 测试缺口/聚类/manifest/协议 | 8 |
| 12 | 死代码工具化 | 5 |
| 13 | Node 侧原生宿主 | 6 |
| 14 | 运行时验证（门禁） | 5 |
| **15** | **依赖供应链 + 类型检查** | **1** |

**本轮净新增只有 1 条（P3）。** 这是十五轮最低。

### 轮次估算（再更新）

- **剩余静态维度**：**0-1 轮**（依赖供应链已做，只剩 Firefox 分支）
- **运行时验证**：本轮把 tsc 打通了，但结果仍被环境噪声淹没；
  vitest 卡在原生崩溃

**结论：静态审计维度已基本穷尽，建议收尾。**
再继续扫新维度，产出大概率是个位数的 P3。

---

## 八、局限

**① tsc 结果不可信**

844 条诊断里 88% 是环境噪声，且 `.wxt/tsconfig.json` 是我手写的
（非 `wxt prepare` 产物）。这次运行证明路径可行，但结论不能作为"类型干净"的证据。

**② vitest 完全没跑起来**

220 个测试文件仍是零运行时证据。这是十五轮下来最大的单块空白。

**③ 依赖供应链只做了结构对账**

没有 CVE 比对（`npm audit` 不可用），没有许可证检查。
typosquat 检测只做了编辑距离 1 的粗筛。

**④ deps 的"实际安装 525"不含嵌套包**

只扫了 `node_modules` 顶层与 scope 展开，嵌套的 `a/node_modules/b` 未计入，
所以 525 与 lock 的 701 不可直接比较（这也是版本漂移初版误报的根源）。

---

## 九、下一步与最终建议

**建议收尾。** 十五轮累计 75 条 finding，其中：

**发布前应修（7 条 P1）**：
DPP-005 凭据明文落盘 / DPP-006 console 钩子写宿主 localStorage /
DPP-029 telemetry promptText 落盘 / DPP-001 sync 无超时 /
DPP-002 自动化删除不等待终态 / DPP-034 deadlineAt 可空 /
DPP-043 导入 Skill 静默追加写入授权

**另建议加 1 条**：**DPP-070**（i18n 硬编码中文）——
它是 75 条里**唯一有 CI 红灯支撑**的缺陷，第十四轮实测 `verify:i18n` exit=1。

**若要继续挖，唯一值得做的是换环境**：
完整 `npm install` → `wxt prepare` → `tsc --noEmit` + `vitest run`。
本轮已证明 tsc 路径只需补 2 个包 + 1 个 tsconfig 就能跑通，
在真实环境里这 220 个测试的价值远高于再扫一轮静态。
