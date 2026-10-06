# doubao-butler 第五轮审计：工具链驱动的全仓扫描

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮方法：**用完整工具链（vulture / radon / lizard / ruff / pydeps / pyan3 / gitingest + 自写 AST 规则引擎）做全仓扫描**，再用行为级实验逐条复核
> 与前几轮的关系：前四轮是"人提假设 → 脚本坐实"；**本轮首次由工具主动出候选，人做复核与归因**
> 交付：新增 V17–V19，套件扩到 **28 条用例**（19 正测 + 9 反测）；全仓库 **7 failed / 613 passed**（新增 5 passed，无回归）

---

## 0. 本轮核心结论

**工具链确实挖出了前四轮都没看到的东西，但真正有价值的不是"新 bug 数量"，而是两条更硬的产出：**

1. **V18：一条被 `if False` 掩盖的 P0**——`docker_tools.py:108` 是 async 函数里的 `time.sleep`，而 `time` 根本没 import。当前是死代码所以没人发现，但注释写着"不 sleep，让调用方观察"，暗示未来会改成 True——那一刻立刻 `NameError`。
2. **V19：缺陷密度的量化地图**——`dispatch_tool` CC=93、`on_wakeup` CC=63。前三轮我在 `on_wakeup` 里独立找到 3 个缺陷**不是偶然**，是复杂度的必然。这给下一轮审计提供了优先级。

**同时必须更正我上一轮的一个判断**：`ha.py:313` 不是"HA 播报功能完全失效"。真实的 `notify_message` 在 333 行独立定义、有 4 处调用且正常。313–331 是**被遗弃的旧实现残留**，危害是幽灵代码 + 误导，不是功能中断。

---

## 1. 工具链实测清单

| 工具 | 状态 | 本轮产出的价值 |
|---|---|---|
| `vulture` 2.16 | ✅ | 3 条 100% 置信度全为真缺陷（V17/V18 的来源） |
| `radon` 6.0.1 | ✅ | **V19 复杂度地图**，本轮最有价值产出 |
| `ruff` 0.16.10 | ✅ | F821 未定义名——直接坐实 V18 + 复证 P0-3 |
| `lizard` 1.24.0 | △ | 与 radon 重叠，未采用 |
| `pydeps` 3.0.9 | △ | CLI 沙箱超时，改用自写 AST 做等价分析 |
| `pyan3` 2.8.1 | △ | 调用图生成失败，改用自写分析 |
| `gitingest` 0.3.1 | △ | 单提交仓库，压缩收益有限 |
| `semgrep` 1.179.0 | ❌ | 装上但**静默失败**（exit 2 无输出），改用自写 AST 规则 |
| `deadcode` / `skylos` / `nuanced` | ❌ | pip 安装失败 |

**`semgrep` 的失败值得一提**：它是这个清单里最适合做"模式批量检索"的工具，装上却跑不起来。我改用约 60 行自写 AST 规则引擎做等价替代——反而更可控（规则完全贴合本项目已证实的反模式）。

---

## 2. V18（P0）：`if False` 掩盖的定时炸弹

**位置**：`butler/integrations/docker_tools.py:108`

```python
async def restart(self, container_name: str) -> dict:       # ← async
    ...
    # 验证容器状态
    time.sleep(2) if False else None  # 不 sleep，让调用方观察
```

`ruff check --select F821` 直接报：`F821 Undefined name 'time'`。

**三重问题叠加**：
1. `docker_tools.py` 顶层只 import 了 `asyncio / subprocess / os / Path`，**没有 `time`**；
2. 当前 `if False` 所以不执行 → 死代码 → 没人发现；
3. 它在 **`async def restart`** 体内 → 真执行会阻塞整个事件循环 2 秒。

注释"不 sleep，让调用方观察"暗示这是待启用的逻辑。**改 `False` → `True` 的那一刻，`NameError: name 'time' is not defined`，而且是容器重启流程的中段。**

**为什么前四轮没发现**：死代码不执行就没有任何运行时症状，读代码看到 `if False` 会本能跳过。

**修复**：`await asyncio.sleep(2)`（模块已 import asyncio）。反测已验证：修补后既无裸 `time` 也无 `time.sleep`。

---

## 3. V17（P1）：`ha.py` 双函数叠写（更正上一轮判断）

**位置**：`butler/integrations/ha.py:299-331`

```
299| async def intelligent_speaker(self, entity_id, text, execute=False):
313|     """通过 HA notify 平台让小爱音箱播报文本"""      ← 旧函数的 docstring
316|         data = {"message": message}                  ← message 从哪来？
330|         logger.warning("HA notify_message failed...") ← 原名泄露
333| async def notify_message(self, message, entity_id=None):   ← 真身
```

313–331 是一段**旧 `notify_message` 实现的函数体**，`def` 行丢失后被吞并进 `intelligent_speaker`、且落在 return 之后。

**我上一轮说"HA 播报功能完全失效"是错的。** 核实结果：真实的 `notify_message` 在 333 行独立定义，通过 `_notify_message_inner` 工作，有 4 处调用（`role_routes` ×2、`dialog` ×2、`decision/action_router` ×1），功能正常。

**修正后的危害评估**：
- ① 永不可达（幽灵代码）
- ② 引用未定义的 `message`，若有人重构误触必 `NameError`
- ③ 严重误导读者——让人以为 `intelligent_speaker` 内含 notify 逻辑

**修复**：删除 313–331。反测已验证：删除后 `intelligent_speaker` 变为 13 行纯净实现，无未定义名。

**顺带**：全仓"孤立 docstring"扫描只找到 3 处，另 2 处在 `memory_routes.py:98/253` 是**docstring 放错位置**（放在 `if g: return g` 之后变成无效字符串表达式），仅导致 `help()` 看不到文档，功能无碍，列 P3。

---

## 4. V19（量化）：复杂度就是缺陷密度地图

```
CC=93  dispatch_tool        tools/registry.py:882
CC=63  on_wakeup            core/dialog.py:232
CC=62  handle_webhook       api/doubao_webhook.py:442
CC=60  run                  skills/engines/ha_inspection/engine.py:46
CC=55  run                  skills/runner.py:140

共 1828 块，平均 CC 4.88
CC>=15 高危 96 个    11-14 需重构 82 个
```

**交叉验证是本条的价值所在**：前三轮我在 `on_wakeup`(CC=63) 里独立找到 **3 个**缺陷（P0-1 role 未绑定、V4 pending 漏 return、T-1 speak 提前 return），在 `enqueue_item`(CC=32) 找到 2 个（P0-4 过载、V12 熔断），在 `speak`(CC 高) 找到 1 个。**这不是巧合。**

反过来指向下一轮该读什么：**`dispatch_tool` CC=93 是全仓最复杂的函数，所在 `tools/registry.py`(1169 行) 我四轮都没读过。** 按上述相关性，它极可能是缺陷密度最高的区域。

---

## 5. 其他扫描结果

### 5.1 循环依赖 13 组（架构脆弱性，非当前 bug）

```
183 个模块，1359 条依赖边

tools.registry → core.agent → core.tools → tools → tools.registry
core.agent → skills.engines.tool_sequence.engine → core.tools → tools → tools.registry → core.agent
store.write_failures ⇄ store.txn_census
timeseries ⇄ timeseries.api
```

最深的是 4 模块环 `tools → tools.registry → core.agent → core.tools → tools`。**当前靠函数内延迟导入打破**（`registry.py:885` 在 `dispatch_tool` 内部 `from butler.tools.devices import _resolve_entity`），所以运行不炸。但这是脆弱平衡：任何重构改变导入时机就会 `ImportError`。

### 5.2 `create_task` 未保存引用：17 处（扩展 V6）

前几轮我只发现 `tts/singleton.py` 一处。自写 AST 规则扫出 **17 处**，其中 `core/dialog.py` 独占 5 处（229/376/449/505/729）。asyncio 内部只持弱引用，无强引用的 Task 可能在完成前被 GC——**V6 的覆盖面被显著低估了**。

### 5.3 `except: pass` 55 处

这是项目"兜底文化"的量化证据，与门禁基线的 42 条 `except-pass-broad` 豁免相互印证。**团队知道，我也独立确认了。**

### 5.4 两条 ruff F821 交叉验证

- `api/config_routes.py:23` undefined `logger` ← **第一轮 P0-3，工具早已报出，项目未修**
- `api/skill_routes.py:979` F811 `skill_mock_test` 重复定义——我逐行对比了 953 与 979 两处，**实现完全相同**，行为无差异，降级为 P3 代码卫生。

---

## 6. 本轮新增缺陷总表

| ID | 等级 | 缺陷 | 反测 |
|---|---|---|---|
| **V18** | **P0** | `docker_tools` async 内 `time.sleep` 且 `time` 未 import（`if False` 掩盖） | ✅ 改 `asyncio.sleep` 后无裸 `time` |
| V17 | P1 | `ha.py` 双函数叠写，幽灵函数体引用未定义 `message` | ✅ 删除后变为 13 行纯净实现 |
| V19 | 量化 | 96 个 CC≥15，最坏 `dispatch_tool` CC=93 | — |

**套件现状：28 条用例（19 正测 + 9 反测）。默认 27 passed / 1 skipped（V4 隔离）；`RUN_ISOLATED=1` 全绿 28 passed。全仓库 7 failed / 613 passed。**

---

## 7. 三次自我修正（本轮的方法论成本）

审计过程中我自己犯了三个错，都由反测机制逼出来，如实记录：

1. **V17 反测连修三次才通过**。第一版把 `bool`/`str` 当未定义名；第二版漏了 `except ... as e` 绑定；第三版漏了模块级赋值 `logger = get_logger(...)`。**每版都是分析器的缺陷，不是被审计代码的缺陷。**

2. **V18 反测曾假通过**。第一版用 `or True` 强行通过，输出 23 个"未定义名"（含函数名、参数、内建）全是噪声。我重写成只盯 `time` 这一个名字的精确断言。**留着假通过的反测比没有反测更危险。**

3. **上一轮"HA 播报功能完全失效"是错的**（见 §3）。这是本轮最该记的教训：**工具报出异常时，先确认"真身是否另有其人"，再下功能中断的结论。**

---

## 8. 工具链的真实价值与边界

**价值**：全仓 41k 行，我四轮人工只读了 10.1%。工具把**扫描覆盖率**提到接近 100%，本轮 3 条新发现全部来自工具主动输出，其中 V18 是人工极难发现的（死代码 + 缺 import + async 内阻塞，三重要素同时命中才成立）。

**边界**（必须说清）：

- **工具提高的是覆盖率，不是理解深度**。完整 lifespan、真实 MQTT、SQLite 并发这些四轮未解的问题，工具一个都帮不上。
- **误报需要人工复核**。全仓"return 后不可达"规则产出 1481 条，几乎全是误报；我上一轮自写的这条规则已废弃，ha.py 那条改用 vulture 的专业控制流分析确认。
- **工具不会自己归因**。V17 从"未定义名"到"双函数叠写"的定性，是人做的。

---

## 9. 修复优先级（含本轮，重排）

1. **V18（P0，5 分钟）**——改 `await asyncio.sleep(2)`。**改动极小但风险等级最高**，属"改一行就崩"型。
2. **V13（P1，30 分钟）**——16→15 处 `.result(timeout=30)`。监控在说谎最该先修。
3. **V7 / T-1（P0，20 分钟）**——`dialog.speak` 提前 return 丢副作用。
4. **P0-1 + P0-3（25 分钟）**——技能确认 `role`、config `logger`（**ruff 早已报出，项目未修**）。
5. **V17（P1，10 分钟）**——删除 313–331 幽灵段。
6. **V6 扩展（1 小时）**——17 处 `create_task` 补强引用（`dialog.py` 5 处优先）。
7. **下一轮审计方向**——`dispatch_tool`(CC=93) 与 `tools/registry.py`(1169 行)，按复杂度-缺陷相关性，这是最可能的富集区。
