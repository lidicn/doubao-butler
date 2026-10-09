# doubao-butler 审计报告 · 第九轮

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-009`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-009/`
- **报告日期**：2026-10-07

---

## 一句话结论

**本轮做了两件"降噪"和一件"扩面"：W68 把「混合返回」的 27 条 high 按返回类型重分级（bool 降到 low、dict/list/tuple 保留 high），high 从 27 降到 11；以及首次审计 vendored 私有依赖 homesdk，实测确证三处无预算递归（D16）——而它正是项目 `gates.sh` 的 CI 门禁。依赖 CVE 面仍然扫不了（pip-audit 需联网 + 需 venv，两者都缺）。**

---

## 一、确证缺陷：D16【P2·medium】vendored homesdk 三处无预算递归

`vendor/homesdk-0.3.1-py3-none-any.whl` 内 `homesdk/gates/scan.py`：

| 函数 | 行 |
|---|---|
| `_numeric_literal` | 375 |
| `_dotted` | 385 |
| `_literal_secret` | 405 |

**实测**（隔离加载三个函数后喂深度阶梯）：

| 深度 | 结果 |
|---|---|
| 800 | ok |
| **1000 / 1500 / 2000 / 3000** | **RecursionError** |

**为什么这条重要**：`gates.sh:29` 直接调 `python -m homesdk.gates "$REPO" --no-smoke` **作为 CI 门禁**。所以含深嵌套表达式的源码文件可以让**整个 CI 门禁崩溃**，而不只是让某个检查失败。

**只扫自家源码，永远发现不了"我的门禁被我的依赖打崩"。**

**跨项目交叉验证**：这三个函数与 AutoForge F16 是**同一个 wheel 的同一处缺陷**。两个项目共享同一个私有依赖 ⇒ 修上游一处，两边受益。这是把审计面扩展到依赖源码的直接价值。

---

## 二、W68：混合返回的严重度必须按返回类型分级

`AF-AST-MIXED-RETURN` 一条规则产出 27 条 high，**占全部 high 的 56%**。原判据只看"标注非 Optional 却会返回 None"，一律 high。

但按标注类型拆开，后果完全不同：

| 标注 | 条数 | None 的实际后果 | 新定级 |
|---|---|---|---|
| `bool` | 6 | `if x:` 下 None ≡ False，**不炸** | **low** |
| `str` | 7 | `if s:` 安全，`.strip()`/`len()` 会炸 | medium |
| `dict` | 9 | `d["k"]` → TypeError | **high** |
| `list[str]` | 1 | `for x in l` → TypeError | **high** |
| `tuple[bool,str]` | 1 | `a,b = f()` → TypeError | **high** |
| `Any` | 3 | 未知 | medium |

**结果：high 27 → 11，medium 98，low 6。**

**为什么这条值得做**：一条规则吃掉一半 high，等于把人工分诊的注意力**引向最不危险的那部分**。这和第六轮发现的"全局异常兜底让 HTTP 命中整体降权"是同一类问题——**严重度必须反映实际后果，而不是"契约被违背"这个抽象事实**。

---

## 三、依赖 CVE 面：仍然扫不了（第九轮）

试了三种方式，全部失败：

| 方式 | 失败原因 |
|---|---|
| `pip-audit -r requirements.txt` | 需要创建 venv，`python3-venv` 未安装 |
| `python -m pip_audit -r ...` | 同上 |
| `pip-audit`（扫已装环境） | 需访问 PyPI/OSV，**网络 403** |

`dep_audit.py` 给出了它能给的部分：

```json
{"private_wheels": [{"path": "vendor/homesdk-0.3.1-py3-none-any.whl",
  "risk": "私有 wheel 不在公共索引，无法用 pip-audit/OSV 匹配 CVE"}],
 "ci_findings": 0, "pip_audit": "ok"}
```

**结论不变：依赖 CVE 面是"风险未知"，不是"无风险"。** 本轮通过扫依赖**源码**（而非 CVE 库）补上了其中一块（D16），但二进制/传递依赖的漏洞面仍未覆盖。

---

## 四、一个过程发现：vendor 源码目录不完整

第一次扫 `vendor/homesdk/src/` 得到 **0 命中**，与 AutoForge 上的 58 条（high 3）矛盾。

原因：`vendor/homesdk/src/homesdk/` 下只有 4 个文件（`__init__` / `consent` / `gates`），**缺 `auth.py`、`http.py`、`time.py`、`mqtt.py` 等 9 个模块**。而 wheel 里有完整的 13 个。

**所以"扫源码目录"和"扫 wheel"不是等价的**——前者是不完整快照。正确做法是从 wheel 解包（本轮解到 `/tmp/hsk2` 后命中 6 条）。

这提醒：**依赖审计的目标应该是"实际会被安装/使用的产物"，而不是仓库里恰好存在的那份源码**。

---

## 五、审计结果

| 指标 | 数值 |
|---|---|
| MIXED-RETURN | **high 27 → 11**（W68） |
| homesdk wheel 扫描 | RSC-02 3 / RSC-01 1 / RSC-03 2 |
| 本轮新增确证 | **D16** |
| 台账累计 | **D1–D16** |
| 门禁 | ok=True，analyzers=33 |

---

## 六、按根因合并（更新）

| 根因 | 缺陷 | 计数 |
|---|---|---|
| 读路径的失败被当成「没有数据」 | D1 D2 D3 D4 D6 D7 D8 D9 | 8 |
| 失败路径没兜住 / 无超时 | D10 D12 D13 | 3 |
| 并发初始化竞态 | D14 D15 | 2 |
| **无预算递归（依赖侧）** | **D16** | 1 |
| 配置面不一致 | D5 | 1 |
| 观测语义矛盾 | D11 | 1 |

---

## 七、未覆盖 / 如实说明

- **依赖 CVE 面未扫**（pip-audit 缺 venv + 网络阻断）
- **11 条 high MIXED-RETURN 未逐条核验**（只做了类型分级，未确认调用方是否真会炸）
- **DO-05 六条**（aliases / cron_task×3 / ilink / roles）尚未全部确证为独立缺陷——其中 cron_task 三条已确证为 D8/D9
- **1033 条命中仅完成范围收口与部分规则重分级，内容分诊未完成**
- **`vendor/homesdk/src/` 是不完整快照**，依赖审计结论以 wheel 解包为准

---

## 八、下一轮方向

1. **核验 11 条 high MIXED-RETURN**（dict/list/tuple 那批，判断是否真会炸调用方）
2. **核验 DO-05 剩余条目**，与 D1–D9 台账对齐去重
3. **核验 FO-01 两条 + CONC-08 一条 + CONC-03 一条**
4. **跑四类 PoC 回归**（本轮未跑，因本轮改动只涉及规则分级与依赖审计）
5. **考虑写补丁副本（db-patched）**——D1–D16 已有足够证据支撑进入修复阶段

---

## 九、本轮最该记住的一条

**"一条规则吃掉一半 high"是分诊系统的失败，不是规则的失败。**

W68 之前，27 条 high 里有 6 条 bool（None 等价 False，完全无害）和 7 条 str。任何按"命中数"排序的分诊都会先看它们，而真正会 TypeError 的 dict 那 9 条排在同样位置。

这和第六轮"全局异常兜底让 HTTP 命中整体降权"、第七轮"规则指错位置但指对区域"是同一条主线的三个面：**静态规则的命中数不等于风险量，中间必须有一层把"命中"翻译成"后果"。** 这层翻译目前只能靠人工读 + 逐规则补判据，还缺一个统一的后果模型。
