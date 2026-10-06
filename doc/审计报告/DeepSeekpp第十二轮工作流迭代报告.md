# DeepSeek++ 工作流迭代报告（第十二轮）— 补盲区 + 引入外部工具

```
Audit target : github.com/lidicn/deepseekpp
本轮性质     : 不审计，专注工作流迭代
产出         : 2 个新工具封装 + 1 份方法论教训库 + 5 条新增 finding（工具产出）
Audit date   : 2026-10-05
```

---

## 一、先回答你的问题：还需要多少轮？

**结论：静态审计已经到头了。**

| 阶段 | 需要轮次 | 说明 |
|---|---|---|
| **剩余静态维度** | **2-3 轮** | 原生宿主（14 文件）、依赖供应链、Firefox 分支 |
| **运行时验证** | **1 轮（但需换环境）** | 本沙箱做不到，需完整 node_modules |
| **动态审计** | 2-3 轮 | SW 生命周期、网络挂起、内存实测，同样需环境 |

**为什么静态审计到头了** —— 看边际收益曲线：

```
轮次 1-2   人工通读 / skill 四阶段    净新增 53
轮次 3-5   图谱提名 / 全量覆盖        净新增 19
轮次 6-8   focus / 台账 / 装箱        净新增  8
轮次 9-11  测试缺口/聚类/manifest/协议 净新增  8
轮次 12    死代码工具化               净新增  5（全部 P3）
```

连续五轮个位数。更关键的是：**第九、十一轮开的三个新维度里，两个给出的是负面结果** ——
权限比对"零过度声明"、消息协议"完全闭合"。负面结果也是信息，说明这两层质量是好的，
也说明**易发现缺陷确实出清了**。

本轮新工具跑出的 5 条全是 P3 死代码 —— 等级低，但这是工具第一次扫这个维度，
属于"补上盲区"而非"挖出新风险"。

**如果要一个数字：再 2-3 轮可把剩余静态维度做完；但要真正验证已入库的 63 条 finding
哪些真实可达，必须换能跑起来的环境，那是另外的事。**

---

## 二、本轮突破：npm 装包不全这个判断是错的

前十轮我一直认为"沙箱装不了 npm 包"，第九、十一轮的工具选型都受此限制
（dependency-cruiser、madge、ast-grep 全放弃，只能自研）。

**这个判断是错的。**

实测：`npm install` 确实装不全（元数据可达，tarball 不落盘），但 **`npm pack` 能完整下载 tarball**。

写了 `bin/pkginstall`（187 行）：`npm pack` → 解压 → 探测运行 → 递归补齐缺失依赖。

knip 补齐过程（第 7 轮递归成功）：
```
jiti → package-manager-detector → walk-up-path → resolve-pkg-maps
→ @oxc-parser/binding-wasm32-wasip1 → @oxc-parser/binding-wasm32-wasi
→ @oxc-parser/binding-linux-x64-gnu → @oxc-resolver/binding-linux-x64-gnu
→ ✓ knip 6.39.0 运行成功
```

**这意味着之前放弃的第三方工具路线重新打开了。**

---

## 三、引入的工具（GitHub 选型结果）

| 工具 | 用途 | 状态 | 对应盲区 |
|---|---|---|---|
| **knip** | 死代码/未使用导出/未用依赖 | ✅ 已跑通 | 第十轮靠人肉才发现整个文件是死代码 |
| **@typescript-eslint/parser** | 自定义 AST 规则 | ✅ 已跑通（需 NODE_PATH） | 第三轮 ast-grep 装不上放弃的 AST 路线 |
| crx-audit | MV3 manifest/CSP/WAR 组合分析 | 未装 | 可对标自研 manifest.py |
| ThreatXtension | AI 驱动扩展审计 | ❌ 排除 | 需 API key + Docker |

**选 knip 的理由**：第十轮我靠人工通读才发现 `core/messaging.ts` 整个文件是死代码
（要穷举所有 import 才能确认，人肉极难）。这类问题工具一跑就有。

**本轮 knip 独立复现了该发现** —— 这是工具有效性的强验证，也证明这个盲区必须工具化。

---

## 四、工具产出：5 条新 finding（都是 P3 死代码）

`bin/deadcode` 封装 knip，跑出 **6 个 core 死文件**：

| 文件 | 状态 |
|---|---|
| `core/messaging.ts` | ✅ 第十轮已入库（DPP-054），**knip 独立复现** |
| `core/automation/pow.ts` | 🆕 DPP-059（注意：`core/deepseek/pow.ts` 是另一个文件，被正常使用） |
| `core/memory/injector.ts` | 🆕 DPP-060 |
| `core/mcp/index.ts` | 🆕 DPP-061 |
| `core/skill/pi-importer.ts` | 🆕 DPP-062（仅测试引用） |
| `core/ui/tool-card.ts` | 🆕 DPP-063 |

**剔除 2 条假阳性**（都写进了工具的已知假阳性表）：

- `core/browser/safe-wxt-browser.ts` —— 被 `wxt.config.ts:298` 的 `resolve.alias` 引用，**构建期引用，静态分析认不出**
- `packages/shell-host/native/*.mjs`（14 个）—— native messaging host 通过**宿主 manifest JSON** 引用入口，非 JS import

---

## 五、工具自身的四个坑（都已修，写进代码注释）

knip 首跑报 **710 个未使用文件**（几乎全库）。逐个修：

| Bug | 症状 | 修复 |
|---|---|---|
| 入口未声明 | WXT 入口在 `entrypoints/` 隐式约定，knip 认不出 → 710 条 | 显式声明 entry |
| `core/**` 设成 entry | 等于"每个文件都是入口"，内部孤立文件永远检测不出 | entry 只设真入口，core 放 project |
| exports 级联 | 加 exports 后文件数 **21 → 86**，把 `scheduler.ts` 这类核心文件也判成未使用 | 默认只跑 files 模式 |
| 配置字段 | `_comment` 字段被 schema 拒绝 | 移除 |

**规律再次验证：工具首跑的结果几乎一定有 bug，必须先小样本验证再全量采信。**

---

## 六、Skill 完善

新增 `/data/workspace/audit-kit/rules/AUDIT-LESSONS.md`（116 行），含：

1. **13 条已证伪论断对照表** —— 从第六轮的 5 条扩展到 13 条，含本轮新增的引号漏匹配
2. **7 条自查清单** —— 比第六轮多 2 条（"无人引用"类结论换引号风格搜；工具报"未使用"先查构建期引用）
3. **9 个方法论盲区表** —— 按发现顺序记录，每个附补法
4. **9 个工具自身 bug 表** —— 含本轮 knip 的 4 个
5. **沙箱环境约束** —— npm pack 绕过、TS 副本位置、NODE_PATH 设置
6. **边际收益曲线** —— 支撑"静态审计到头"的判断

并在 `SKILL.md` 末尾加了指针，要求审计前必读这两份规则。

---

## 七、工作流最终形态

```
audit-kit/
├── bin/
│   ├── audit          统一入口（run/graph/scan/triage/report/workorders/reg）
│   ├── pkginstall     ★ npm 装包绕过器（递归补依赖）
│   └── deadcode       ★ 死代码检测（knip 封装）
├── lib/
│   ├── lexer/graph/scan/triage/report   图谱与模式扫描
│   ├── registry       跨轮次问题库
│   ├── focus          ★ 覆盖驱动工单（含装箱 + 机制自查表）
│   ├── coverage       ★ 覆盖率台账
│   ├── cluster        ★ 根因聚类
│   ├── manifest       ★ 扩展权限/CSP 审计
│   └── typecheck      ★ TS 类型检查（挂系统副本）
├── config/knip.json   死代码检测配置（含入口声明）
└── rules/
    ├── AUDIT-RULES.md     项目专属口径
    └── AUDIT-LESSONS.md   ★ 方法论教训库（12 轮沉淀）
```

问题库：**115 条**（63 finding + 52 误报）。模块级覆盖 49/49，文件 451/451。

---

## 八、下一轮建议（按性价比）

**1. 原生宿主审计（1 轮，可在本沙箱做）**

`packages/shell-host/native/*.mjs` 14 文件 —— 12 轮从未审过。这是 Node 侧代码，
涉及文件路径处理、进程执行，**安全风险高于普通 TS 模块**。本轮 knip 把它们列为
"未使用"是假阳性，但也说明之前没人看过。

**2. 依赖供应链（1 轮，可在本沙箱做）**

依赖树、已知 CVE、许可证。pkginstall 打通后 npm audit 类工具可用性提升。

**3. 换环境跑验证（收益最高，但需外部）**

完整 `npm install` + `wxt prepare` → `tsc --noEmit` + `vitest` + 读真实 manifest.json。
这是唯一能一次性验证多条 finding 可达性的手段，也是 12 轮唯一没做成的事。

**4. P1 清单（7 条，发布前应修，与本轮无关但始终有效）**

DPP-005 凭据明文落盘 / DPP-006 console 钩子写宿主 localStorage / DPP-029 telemetry
promptText 落盘 / DPP-001 sync 无超时 / DPP-002 自动化删除不等待终态 /
DPP-034 deadlineAt 可空 / DPP-043 导入 Skill 静默追加写入授权。
