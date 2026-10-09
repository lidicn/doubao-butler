# doubao-butler 审计报告 · 第五轮

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-005`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-005/`
- **报告日期**：2026-10-07

---

## 一句话结论

**RSC-02（无预算递归）7 条 high 里，5 条是假阳性——全部源于「函数名 == 被调用名」就判自递归。修掉后剩下 2 条，其中 1 条确证为 D12（技能渲染栈溢出，且异常穿出 try）。本轮的实质产出是：这 5 条假阳性暴露了一条判据缺陷，而这条缺陷在 AutoForge 二十轮里一直没被触发过。**

---

## 一、确证缺陷（本轮新增）

### D12【P2·medium】技能引擎 `_render` 无深度预算、无环检测，且异常穿出 `run()` 的 try

`butler/skills/engines/tool_sequence/engine.py:31`：

```python
def _render(value, params: dict):
    if isinstance(value, dict):  return {k: _render(v, params) for k, v in value.items()}
    if isinstance(value, list):  return [_render(v, params) for v in value]
```

**实测（深度阶梯 + 自引用双重）**：

| 输入 | 结果 |
|---|---|
| depth=50 / 200 | ok |
| **depth=500 / 1000 / 2000** | **RecursionError** |
| **自引用 dict** | **RecursionError** |

**两道防护全缺**：既无深度预算（挡线性深嵌套），也无环检测（挡循环引用）。这与 AutoForge 第十三轮实测确认的结论一致——**预算和环检测是正交的两道，只装一道，缺陷不会消失，只会换个触发条件**。

**更关键的是异常位置**：

```python
for i, step in enumerate(steps):
    args = _render(raw_args, params)      # ← 57 行，try **外面**
    try:
        result = await dispatch_tool(...)  # ← try 从 58 行才开始
```

RecursionError 必然穿出 `run()`，**不进 `SkillResult(ok=False)` 的失败通道**，也不会被「技能失败回退 ReAct」的逻辑兜住（`core/agent.py:253`）。

**输入可达性**：`args` 来自技能定义 JSON，而 `/app/data/skills/user/*.json` 可由用户推送（`push_routes.py`）⇒ 一条深嵌套或自引用的 args 即可触发。

---

## 二、7 条 RSC-02 的核验结果：5 条假阳性

| 位置 | 判定 | 原因 |
|---|---|---|
| `api/agent_routes.py:163` `analyze_failures` | **假阳性** | 函数内 `from butler.core.failure_learn import analyze_failures`，调的是**别的模块的同名函数** |
| `:174` `infer_scenes` | **假阳性** | 同上 |
| `:185` `list_failure_reports` | **假阳性** | 同上 |
| `:195` `list_scene_reports` | **假阳性** | 同上 |
| `skills/store.py:131` `list` | **假阳性** | `list(self._index.values())` 调的是**内建 `list`**，不是自己 |
| `tool_sequence/engine.py:31` `_render` | **真（D12）** | 实测崩溃 |
| `scripts/audit_1002/...:168` `ann_name` | 真但低优先 | PoC 判 safe（AST 深度实际有界），一次性审计脚本 |

**共同点**：前 5 条都是「函数名 == 被调用名」，但被调者**不是本函数**——一类是函数内 import 遮蔽，一类是内建同名。

---

## 三、工作流迭代：W61（本轮核心）

`boundary_defects.py` 的 `_self_recursive()` 原先只看「调用名 == 函数名」：

```python
if isinstance(f, ast.Name) and f.id == fn.name:
    return True
```

W61 补两类排除：

1. **函数内导入同名**（`_imported_names()`）：`from X import y` 在本函数作用域内遮蔽了同名模块级函数
2. **内建同名**：`def list(self)` 里 `list(...)` 调的是 `__builtins__.list`

**为什么这条缺陷二十轮都没暴露**：AutoForge 里恰好没出现「函数内 import 同名」这种写法，也没人以 `list`/`dict`/`str` 命名方法。规则换到 doubao-butler 才第一次触发——**这说明规则的假阳性率高度依赖目标项目的命名习惯，而我们在单一项目上做了二十轮，等于一直在同一片语料里验证规则。**

这也是本项目第十七次「工具静默失效」，方向是**误报**（前十六次都是漏报）。

---

## 四、环境note：外部工具跨调用丢失再次发生

本轮第一次跑门禁失败：

> `[FAIL] external_tool_defects 在 dirty 上 0 命中 —— 原因：外部工具全部不可用`

**这是第十六轮记录过的现象再次出现**：pip 装的外部工具（bandit / detect-secrets）在**每次 bash 调用内有效，跨调用即失效**。按当时定的硬约束，把 `ensure_tools.py` 与 `run_selftest.py` 放进**同一次 bash 调用**后，门禁恢复 ok=True。

（这次门禁的失败原因提示是准确的——W41 加的 `status` 字段起了作用，直接指出是"工具不可用"而不是"规则失效"。）

---

## 五、审计结果

| 指标 | 数值 |
|---|---|
| RSC-02 命中 | **7 → 2**（W61 后） |
| 递归 PoC | 2 目标 → **cyclic_crash 1 / safe 1** |
| 本轮新增确证 | **D12** |
| 本轮判假阳性 | **5** |
| 台账累计 | **D1–D12** |
| 门禁 | ok=True，analyzers=33 |

---

## 六、按根因合并（更新）

| 根因 | 缺陷 | 计数 |
|---|---|---|
| 读路径的失败被当成「没有数据」 | D1 D2 D3 D4 D6 D7 D8 D9 | 8 |
| 配置面不一致 | D5 | 1 |
| 失败路径没兜住 / 无超时 | D10 D12 | 2 |
| 观测语义矛盾 | D11 | 1 |

D12 归入「无预算递归」后，第二族（护栏缺失）升到 2 项——这两条的共性也是**失败处理不完整**，只是发生在递归与网络路径上，不是状态读写路径。

---

## 七、未覆盖 / 如实说明

- **`llm.py` / `newapi.py` / `feeder.py` 三条 PoC 仍 `unavailable`**：需 `Settings` / `db_path` / `rt` 真对象
- **`cron_task` 三条 `no_write`**：D8/D9 靠手工构造确证，非自动 PoC
- **依赖 CVE 面未扫**；**semgrep 不可用**
- **1339 条通用命中**：本轮仅核验 RSC-02 全族 7 条，其余未处理
- **D12 的传播路径未端到端实测**：只确认了 `_render` 崩溃 + 调用点在 try 之外；`run()` 外层是否还有兜底未逐层追（README 承诺全局兜底，可能在外层被接住，但那样也已偏离「技能失败回退」的设计意图）

---

## 八、下一轮方向

1. **W61 的同类排查**：把「同名遮蔽」思路外推——方法名与属性名、类名与函数名同名，是否还有别的规则在按名字字面匹配
2. **核验 ERRH-05 超时 4 条**（`scene_routes` / `event_stream` / `xiaomi_ear`；`tts_baidu` 已确证为 D10）
3. **核验 PAT-04 模块级 env 解析 2 条**
4. **继续分诊 AFS-01 剩余约 12 条**
5. **给分诊层加「调用方如何处理失败返回值」信号**（第四轮遗留，本轮 3 + 本轮 5 条假阳性都靠人工读出）
