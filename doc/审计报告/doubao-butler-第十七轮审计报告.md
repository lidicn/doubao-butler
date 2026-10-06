# doubao-butler 第十七轮审计报告 · kwcontract 阶段 + skill_routes.py & store/repo.py

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮主线**：① 新增 `kwcontract` 阶段（关键字参数名 vs 函数签名）② 修 `dupfiles` 的 tests/ 误计引用 ③ 深挖 `api/skill_routes.py`(1061 行) + `store/repo.py`(640 行)
- **结论**：**2 个 P1 + 3 个 P2**，无新增 P0

---

## 第一部分：工作流迭代（v2.0 → v2.1）

### 1.1 新增 `kwcontract` 阶段 —— 关键字参数名比对

第十六轮 P1-44（`bark.push(reason=...)` 传了不存在的参数）是**人工读代码发现的**。这类缺陷完全可以自动化：遍历所有调用点的关键字参数，与目标函数签名比对。

**方法**：
1. 收集全仓函数定义签名（含 `*`/`**` 处理）
2. 遍历所有 `f(..., kw=...)` 调用，取 callee 名 → 候选定义
3. 若 kwarg **不在任何候选定义的参数表中** → 报告

**噪声治理**（43 → 4）：

| 过滤层 | 内容 |
|---|---|
| `EXTERNAL_KWARGS` | `headers/params/timeout/cookies/auth/json/data/files/verify/...` 等 httpx/requests 参数 |
| `SKIP_CALLEE` | `get/post/put/patch/delete/request/run/create_task/format/Popen/Session/ClientSession` 等外部函数 |
| 候选定义缺失 | 无法定位定义时跳过（避免误报） |

**结果 4 处**（全部为真缺陷，其中 2 处是上一轮已知、2 处是本轮新增）：

```
butler/af_bridge.py:177        speak(device=)      ← 第六轮 P0-10
butler/modes/engine.py:221     call_service(entity_id=)  ← 🆕
butler/skills/quarantine.py:178 push(reason=)      ← 第十六轮 P1-44
butler/timeseries/anomaly.py:371 speak(priority=)  ← 🆕
```

> 一次扫描同时复现了两条历史结论（P0-10、P1-44），说明该阶段的判定逻辑与人工结论一致。

### 1.2 修 `dupfiles` 的 `ref_files` 含 `tests/`（第十六轮定位的漏报）

第十六轮发现：`dupfiles` 扫待判文件排除 `tests/`，但**收集引用文件时包含 `tests/`**，导致 `test_http_contract_top5.py` 里"butler.schema 是死副本"这句点名文字被误计为引用 → 漏报第 6 个孤儿。

**本轮已修**：`ref_files` 现排除 `tests/`。

**同时修了 harness 自污染**：`drive.py` 在 135/502 行**显式 import 了 `butler.schema`**，让 harness 自己制造了 alive 信号，进一步掩盖孤儿。已移除该导入。

### 1.3 v2.1 实测

| 阶段 | 结果 |
|---|---|
| **kwcontract** | **4 处**（均为真缺陷） |
| multisource | 14 对（high 6） |
| concurrency | 45 处（C1 17） |
| dupfiles | 5 个孤儿模块 |
| atomicity / timeunit / lifecycle | 35 / 8 / 11 |

---

## 第二部分：第十七轮审计发现

### P1-46 `anomaly.py:371` 调 `rt.tts.speak(priority=)` —— 该参数不存在，异常告警永远播不出

- **位置**：`butler/timeseries/anomaly.py:371`

```python
await rt.tts.speak(text=..., priority="critical")
```

#### 实锤

```
TTSManager.speak 参数: [self, text, voice, device_id, member, backend,
                       speed, nowvoice_voice, trace_id, via]
含 'priority'?  False
绑定 priority= → TypeError: got an unexpected keyword argument 'priority'
```

#### 后果

`anomaly.py` 负责**异常检测告警**（家电超时运行、起床异常等）。这个调用在所有告警播报路径上，TypeError 被 `except Exception: logger.warning` 捕获 —— 告警**静默不播出**，只有一行 warning。

与第十六轮 P1-44 完全同构：**传了不存在的 kwarg + 异常被吞 = 100% 静默失效**。

#### 项目内已有正确写法（对照）

`TTSManager` 有独立的优先级机制（`via` 参数与 TTS 队列的 priority），本处应改用正确通道，而非给 `speak()` 加不存在的参数。

**修复**：改用 TTS 队列的 priority 接口；或若确认 `speak` 需支持优先级，则应**先给 `speak` 加参数**再调用（当前顺序反了）。

---

### P1-47 `modes/engine.py:221` 调 `ha.call_service(entity_id=)` —— 潜伏 TypeError，被 `logger.debug` 静默吞掉

- **位置**：`butler/modes/engine.py:221`

#### 实锤

```
HAClient.call_service 参数: [self, domain, service, data]
含 'entity_id'? False
绑定 entity_id= → TypeError: got an unexpected keyword argument 'entity_id'
```

`entity_id` 本应放进 `data` 字典里（HA 的 `call_service` 约定是 `data={"entity_id": ...}`）。

#### 为什么当前无症状（潜伏）

`MODE_ACTIONS` 中 `movie` / `sleep` / `away` 的动作**全部被注释掉**，`guest` / `daily` 是空列表 → `_apply_mode_actions` 的循环体**从不执行** → 该调用不可达。

但这是**定时炸弹**：一旦有人填回 `MODE_ACTIONS`（这显然是开发中的功能），所有模式切换动作都会抛 TypeError，而异常被**内层 `try/except` 以 `logger.debug` 捕获**——生产日志级别下**完全不可见**。

> 与第五轮 P0 的模式完全相同：`hasattr` / 未使用路径让缺陷静默潜伏，直到某天启用就爆。

---

### P2-14 `repo.py:498` falsy-zero：0% 成功率的技能被排到"最好"的位置

- **位置**：`butler/store/repo.py:498`

```python
stats.sort(key=lambda s: (s["success_rate"] is None, s["success_rate"] or 1, -s["total"]))
#                                                    ↑ 0.0 是 falsy → 变成 1
```

#### 实锤（对照实验）

```
实际排序（当前代码）        期望排序（成功率升序）
   B_20%   rate=0.2          A_0%    rate=0.0
   C_50%   rate=0.5          B_20%   rate=0.2
   A_0%    rate=0.0    ←     C_50%   rate=0.5
   D_100%  rate=1.0          D_100%  rate=1.0
   E_None  rate=None         E_None  rate=None

0% 技能实际排在第 3 位（应为第 1 位）
```

#### 影响

`api/skill_routes.py:846` 的 docstring 明确写"**按成功率升序（低成功率在前）**"——而这个面板的用途正是**发现最差的技能**。缺陷让它把最差的排到最后。

`get_low_quality_skills`（repo.py:505）的**过滤逻辑是对的**（直接 `<= max_rate` 比较），但它复用 `get_all_skill_stats` 的排序，所以输出顺序同样错位。

**修复**：
```python
stats.sort(key=lambda s: (s["success_rate"] is None,
                          1 if s["success_rate"] is None else s["success_rate"],
                          -s["total"]))
```

---

### P2-15 `temperature=0.0` 被静默改成 0.7（falsy-zero 同类）

- **位置**：`llm_decide/engine.py:125`、`llm_decide/mock.py:60`、`llm_text/engine.py:29`

```python
temperature=brain.get('temperature') or 0.7
```

#### 实锤

```
配置 temperature=0.0 → 实际使用 0.7   （应为 0.0）
配置 0.3 → 0.3    配置 0.7 → 0.7    配置 1.0 → 1.0
```

`temperature=0.0`（贪心解码、确定性输出）是**极常见且合法**的配置。用户明确设 0 想要确定性，却被静默改成 0.7。

**修复**：`brain.get('temperature', 0.7)`（用默认值机制而非 `or`），或对 `None` 显式判断。

> 全仓扫出 15 处 `X or <数字>`，其中 14 处是合理默认值兜底（0 无意义，如 `max_chars`、`days`），**仅 `temperature` 与 `success_rate` 两处是真缺陷**——0 是合法取值。

### P2-16 `get_all_skill_stats` 注释声称"避免 N+1"，但 N+1 未消除

- **位置**：`butler/store/repo.py:454`（注释）vs `476-489`（实现）

注释写"P1 修复：用一条 SQL 一次性统计所有技能，避免 N+1 查询"，主聚合确实是一条 SQL；但**循环内**仍有：

```python
for ...:
    last_row = c.execute(
        "SELECT status FROM skill_runs WHERE skill_id=? AND ts>=? ORDER BY ts DESC LIMIT 1",
        (skill_id, since)).fetchone()
```

每个技能一次查询 —— **N+1 实际未消除**，注释误导后续维护者以为已优化。当前技能数量少（十几个）影响有限，属正确性问题 + 误导性注释。

---

## 第三部分：`api/skill_routes.py` 深挖结论（1061 行）

**结论：无功能性缺陷。** 与第十六轮的 `tools/registry.py` 一样，是一个"干净"模块。

| 检查项 | 结果 |
|---|---|
| handler 数量 | **52 个** |
| 路由注册 | **52/52 全部注册**，无遗漏 handler |
| 未注册但已定义 | 无 |
| 鉴权 | 每个 handler 首行 `g = guard(request); if g: return g`，无遗漏 |

**一处重复定义**：`skill_mock_test` 在 **953 与 979 行重复定义两次**，经 `diff` 比对**内容完全相同（差异 0 行）**。Python 后者覆盖前者，无功能影响（第二轮已报），但会误导维护者以为有两组逻辑。

**`get_low_quality_skills` 的过滤逻辑正确**（直接 `<= max_rate`），仅排序受 P2-14 影响。

---

## 第四部分：本轮**排除**的误报

| 疑似 | 核查结论 |
|---|---|
| `kwcontract` 初版 43 处 | ❌ 39 处为 httpx/subprocess/asyncio 外部参数或外部函数，已加 `EXTERNAL_KWARGS` + `SKIP_CALLEE` 过滤 |
| `format(nickname=)` / `format(name=)` | ❌ `str.format()` 接受任意关键字参数，是合法用法 |
| `repo.py` `_OK_STATUSES`(2) vs SQL 硬编码(3) 漂移 | ⚪ **惰性**：SQL 多一个 `'done'`，但全仓扫描确认 `skill_runs.status` **从不写入 `"done"`**（`'done'` 只是 schedule 工具的字段名），故当前无功能差异，仅属同文件内的口径不一致 |
| `count_skill_runs_today` 时区 | ❌ 用 `time.mktime(time.strptime(time.strftime("%Y-%m-%d")))` 算本地午夜，`add_skill_run` 写 `time.time()`（秒），**单位一致**，正确 |
| 14 处 `X or <数字>` | ⚪ 仅 `success_rate`(P2-14) 与 `temperature`(P2-15) 是真缺陷，其余 0 无意义 |

---

## 第五部分：工作流现状与局限

### v2.1 已解决

| v2.0 短板 | v2.1 状态 |
|---|---|
| **kwarg 误用只能人工发现**（第十六轮最高优先级） | ✅ `kwcontract` 阶段，一次扫描复现 P0-10 + P1-44，并新增 2 处 |
| `dupfiles` 因 tests/ 文字误计引用 | ✅ 已修 `ref_files` 排除 tests/ |
| harness 自污染 alive 信号 | ✅ `drive.py` 移除 `butler.schema` 直连导入 |

### 仍存在的短板（诚实）

1. **`kwcontract` 依赖"callee 名 → 候选定义"的名称映射** —— `self.xxx()` / `rt.xxx.yyy()` 等链式调用靠方法名反查，可能跨类误匹配；本轮 4 处已人工核实，但规模化后需类型推断
2. **`multisource` 只覆盖字符串序列** —— 第十六轮 P1-43 的 `DEFAULT_ROLES` 是 dict-list，仍抓不到
3. **C 类（逻辑错误）仍空白** —— P2-14（falsy-zero）本轮靠人工发现，非工具
4. **falsy-zero 未工具化** —— 已扫出 15 处候选，但需人工区分"0 是否合法取值"
5. **代码深挖 ~27%**（本轮新增约 1700 行）

---

## 第六部分：累计与下一轮计划

### 十七轮累计新增（本轮）

| 编号 | 结论 |
|---|---|
| **V35** | **`anomaly.py:371` `speak(priority=)` 参数不存在 → 异常告警全部静默失效** |
| **V36** | **`modes/engine.py:221` `call_service(entity_id=)` 参数不存在 → 潜伏（MODE_ACTIONS 空故不可达），启用即 TypeError 且被 logger.debug 吞** |
| V37 | `repo.py:498` falsy-zero：0% 成功率技能被排到最优位置，与"低成功率在前"的设计相反 |
| V38 | `temperature=0.0` 静默变 0.7（3 处） |
| V39 | `get_all_skill_stats` 注释声称避免 N+1，实际未消除 |
| V40 | `skill_mock_test` 重复定义两次（内容完全相同，无功能影响） |

### 修复优先级

| 优先级 | 项 | 成本 |
|---|---|---|
| **立刻** | **P1-46 `speak(priority=)`** —— 改走 TTS 队列优先级通道 | 低 |
| **立刻** | **P1-47 `call_service(entity_id=)`** —— 移入 `data` 字典 | 极低（1 行） |
| 立刻 | P1-44 `push(reason=)` → `title=` | 极低 |
| **立刻** | P0-15 删 6 个孤儿文件（含 `butler/schema.py`） | 极低 |
| 本周 | P0-10 `speak(device=)`（第六轮） | 低 |
| 本周 | P2-14 修 sort key、P2-15 修 temperature 默认值 | 极低 |
| 排期 | P1-42/43 成员名与角色白名单单一真源 | 中 |

### 第十八轮计划

- **目标模块**：`integrations/ha.py`(650) + `triggers/engine.py`(488)
- **工作流**：
  1. **`falsyzero` 阶段** —— 扫 `X or <常量>`，结合"该字段 0 是否合法"的启发式（数值配置型字段、比率字段优先）
  2. `kwcontract` 增强：对 `self.xxx()` / `rt.a.b()` 做接收者类型推断，降低跨类误匹配
  3. `multisource` 扩展到 dict-list（覆盖 `DEFAULT_ROLES` 类漂移）

> 本轮最值得记的一点：**两条 P1 都是同一形态**——传了不存在的 kwarg，异常被 `except` 吞掉。这个形态现在已被 `kwcontract` 完全覆盖，全仓只剩这 4 处。也就是说，**这类缺陷已从"靠运气读出来"变成"可穷举"**。这是十七轮工作流迭代中收益最大的一次。
