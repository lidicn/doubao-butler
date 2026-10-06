# doubao-butler 第十八轮审计报告 · falsyzero / T3 时钟混用 + ha.py & triggers/engine.py

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮主线**：① 新增 `falsyzero` 阶段 ② `timeunit` 新增 T3（wall clock / monotonic 混用）③ 深挖 `integrations/ha.py`(650) + `triggers/engine.py`(488)
- **结论**：**1 个 P2（真实）+ 3 个 P2 级观察**，无 P0/P1

---

## 第一部分：工作流迭代（v2.1 → v2.2）

### 1.1 新增 `falsyzero` 阶段

第十七轮 P2-14/P2-15 是靠人工在 15 处 `X or <常量>` 里挑出来的。本轮固化为阶段。

**判据**：扫描 `X or <常量>`，按**字段名**判断 0 是否有意义：

| 词表 | 内容 | 判定 |
|---|---|---|
| `ZERO_MEANINGLESS` | `max_chars / max_tokens / days / hours / minutes / seconds / limit / count / total / size / width / height / page / page_size` | 0 无意义 → 跳过（噪声） |
| `ZERO_MEANINGFUL` | `temperature / confidence / success_rate / ratio / rate / threshold / top_p / weight / score` | 0 合法 → **high** |

**MEANINGLESS 优先排除**（初版词表顺序反了，导致 13 处全 high）。收敛后：**15 → 5**。

```
butler/locator.py:119                          confidence or 0.5
butler/skills/engines/llm_decide/engine.py:125 temperature or 0.7
butler/skills/engines/llm_decide/mock.py:60    temperature or 0.7
butler/skills/engines/llm_text/engine.py:29    temperature or 0.9
butler/store/repo.py:498                       success_rate or 1
```

后 4 处即第十七轮已报的 P2-14/P2-15 —— **工具独立复现**。

### 1.2 `timeunit` 新增 T3：wall clock / monotonic 混用

第十八轮新缺陷（见 P2-17）促使我补了这条判据。实现分三层：

1. 建立**时钟源映射**：`X = time.time()` → wall；`X = time.monotonic()` → mono
2. **下标传播**：`self._x[k] = fire_start`，而 `fire_start = time.time()` → 记 `self._x` 为 wall
3. **别名解析**：`last = self._last_fired.get(...)` → 解析回 `self._last_fired`

第 2 步是关键——初版只认 `self._x[k] = time.time()` 的**直接形式**，漏掉了经 `fire_start` 别名中转的写法，因此**先误报了 `tts/manager.py:36`、又漏报了真正的缺陷**。加传播后才命中。

**结果**：T3 共 2 处（1 真 / 1 误报）。

### 1.3 v2.2 实测

| 阶段 | 结果 |
|---|---|
| **falsyzero** | **15 处（high 5）** |
| **timeunit** | **10 处**（T1 4 / T2 4 / **T3 2**） |
| kwcontract | 4 处（全真） |
| multisource | 14 对（high 6） |
| concurrency | 45 处（C1 17） |
| atomicity / lifecycle / errpath / identity | 35 / 11 / 2 / 12 |
| dupfiles | 5 个孤儿模块 |

---

## 第二部分：第十八轮审计发现

### P2-17 `TriggerEngine.status()` 时钟混用 —— 冷却状态数值荒谬（当前无调用者，属潜伏）

- **位置**：`butler/triggers/engine.py:475-489`

```python
def status(self) -> dict:
    now = time.monotonic()                      # ← monotonic
    ...
    last = self._last_fired.get(trig["id"], 0)  # ← 存的是 time.time()
    "cooldown_remaining": max(0.0, cd - (now - last)) ...
    "last_fired_ago": round(now - last, 1) ...
```

`_last_fired` 由 `fire_start = time.time()`（engine.py:348）赋值（397/406 行）。

#### 实锤（真实对象）

```
cooldown_remaining = 1791201419.1   （期望 ≈300）
last_fired_ago     = -1791201119.1  （期望 ≈0.0）
```

两字段全错，量级为"自纪元起的秒数"。

#### 当前影响：零（`status()` 无调用者）

已确认：
- `api/trigger_routes.py`、`api/system_routes.py`、`api/dashboard_routes.py`、`triggers/health_api.py` **均不调用** `status()`
- 前端 `index.html:520` 的"冷却 Xs"用的是 `breakerOf()`（另一套数据），非本字段

**定 P2 而非 P1**：docstring 写"供 API/调试"，说明作者预期它会接入。一旦有人接上，触发器面板就会显示"冷却 1791201419s"。与第十七轮 P1-47（`call_service(entity_id=)`）同属"潜伏定时炸弹"。

#### 与 `_match` 的对照（证明是笔误而非设计）

同文件 `_match`（209 行）读取 `_last_fired` 时用的是 **`time.time()`——正确**。`status()` 用了 `monotonic()`——**仅此一处不一致**。

**修复**：`now = time.monotonic()` → `now = time.time()`。

---

### P2-18 `ha.py:580` `_media_stop_tasks` 是**类属性**（多实例共享可变状态）

```python
class HAClient:
    ...
    _media_stop_tasks: dict[str, asyncio.Task] = {}   # ← 类体层级（缩进 4）
```

而对照 `self._xiaomi_watch_tasks`（51 行）是**实例属性**（`__init__` 内 `self.`）。两处任务表用了不同的声明方式。

类属性意味着**所有 HAClient 实例共享同一 dict**。当前 `app.py` 只创建一个实例，无实际影响。但若未来测试或热重载产生第二个实例，一个实例取消任务会误伤另一个。属卫生问题。

### P2-19 `ha.py` 任务表不清理（两个）

- `self._xiaomi_watch_tasks`（574 行写入，519 行读旧任务并 cancel）
- `_media_stop_tasks`（650 行写入，588 行读旧任务并 cancel）

两者都在**下次调度时**才移除旧任务，完成后**从不主动清理**。长期运行会缓慢累积（每条播报一个 entry）。属轻微泄漏。

### P2-20 `intelligent_speaker` 死代码（第二轮已报，本轮 AST 确认）

用 AST 判定归属：`intelligent_speaker` 行范围 299-331，其中 303-311 的 try/except **两条路径都 return**（308 行 `return await call_service(...)`、311 行 `return f"error: {e}"`），故 313-331 恒不可达。

被吞掉的是完整的 **HA notify 平台 HTTP 兜底**（`POST /api/services/notify/send_message`）。

---

## 第三部分：`triggers/engine.py` 深挖结论（488 行）**—— 干净**

与第十六轮 `tools/registry.py`、第十七轮 `api/skill_routes.py` 一样，这是一个**高质量模块**。逐项验证：

| 检查项 | 结果 |
|---|---|
| **`_in_time_range` 跨午夜** | ✅ **8/8 边界用例通过**（22:00-06:00 的起止点、23:59、05:59、12:00 不命中；09:00-18:00 起止点、18:01 不命中） |
| GATE1 并发锁 | ✅ `_running` 防重入 |
| GATE2 事件防抖 | ✅ 同 event/member/room 30s 窗口，dry_run 不占窗口 |
| GATE3 设备锁 | ✅ `_device_locks` TTL 300s |
| GATE4 冷却 | ✅ **只在成功后写完整冷却**；失败写 60s 短退避 + 熔断计数（阈值 5、熔断 600s） |
| 隔离态处理 | ✅ `_only_terminal()` 判定隔离态人工终态**不计入熔断** |
| 冷却持久化 | ✅ `trigger_cooldowns.json`，加载时只取 7 天内未过期 |
| `_match` 时钟 | ✅ 用 `time.time()`，正确 |
| 冷却判定 | ✅ 与 `_last_fired` 同源 |

**唯一缺陷就是 P2-17 的 `status()`**——而且它是**状态查询方法，不在执行路径上**，所以引擎的实际行为完全正确。

> 值得记：这是三轮里第三个"干净"模块。前两轮的 P0/P1 集中在启动装配、表达层、账本层；**触发引擎与工具分发链路反而是写得最扎实的**。

---

## 第四部分：本轮**排除**的误报

| 疑似 | 核查结论 |
|---|---|
| **`tts/manager.py:36` `monotonic() - self._opened_at`** | ❌ **误报**：`_opened_at = time.monotonic()`（54 行），同源，正确。T3 初版因 `_at` 词形命中而误报 |
| **`runner.py:53` `now - hits[0]`** | ❌ **误报**：`hits` 是 deque，元素由 `hits.append(now)` 写入，均为 monotonic，正确。**工具局限**：无法推断容器元素类型 |
| **`locator.py:119` `confidence or 0.5`** | ⚪ **无功能差异**：全仓阈值判定为 0.6/0.7（`app.py:999`、`auto_switch.py:65`、`morning/routine.py:98`、`presence/inference.py:49`、`proactive/engine.py:539/554`、`anomaly.py:158`），真实 0.0 与改后 0.5 **均被拒绝**。仅"0.0 被谎报为 0.5"暴露给上层（`tts_routes.py:107` 作路由判断），若阈值下调到 ≤0.5 才误接受 |
| `_OK_STATUSES`(2) vs SQL(3) 漂移 | ⚪ 惰性（第十七轮已记） |

---

## 第五部分：工作流现状与局限

### v2.2 已解决

| v2.1 短板 | v2.2 状态 |
|---|---|
| falsy-zero 靠人工挑 | ✅ `falsyzero` 阶段，15→5，复现第十七轮两条结论 |
| **wall/monotonic 混用无工具** | ✅ `timeunit` T3（含下标传播 + 别名解析） |

### 仍存在的短板（诚实）

1. **T3 无法推断容器元素类型** —— `hits[0]` 类误报需人工排除
2. **T3 只做单文件分析** —— 跨模块的时钟源传递抓不到
3. **`falsyzero` 依赖字段名启发式** —— 新字段名需补词表
4. **`multisource` 仍只覆盖字符串序列** —— dict-list（`DEFAULT_ROLES`）连续三轮未解决
5. **C 类（逻辑错误）仍空白**
6. **代码深挖 ~31%**（本轮新增约 1140 行）

---

## 第六部分：累计与下一轮计划

### 十八轮累计新增（本轮）

| 编号 | 结论 |
|---|---|
| **V41** | **`triggers/engine.py:475` `status()` 用 monotonic 减 wall clock → cooldown_remaining 天文数字、last_fired_ago 负数（当前无调用者，潜伏）** |
| V42 | `ha.py:580` `_media_stop_tasks` 类属性（与 `_xiaomi_watch_tasks` 声明方式不一致） |
| V43 | `ha.py` 两个任务表完成后不清理 |
| V44 | `intelligent_speaker` 死代码（AST 确认，第二轮已报） |

### 修复优先级

| 优先级 | 项 | 成本 |
|---|---|---|
| **立刻** | P1-46 `speak(priority=)` / P1-47 `call_service(entity_id=)` / P1-44 `push(reason=)` | 极低（各 1 行） |
| **立刻** | P0-15 删 6 个孤儿文件 | 极低 |
| 本周 | **P2-17 `status()` monotonic → time.time()** | 极低（1 行） |
| 本周 | P2-14 排序键 / P2-15 temperature 默认值 | 极低 |
| 本周 | P1-42/43 成员名与角色白名单单一真源 | 中 |
| 排期 | P2-18/19/20 ha.py 三项 | 低 |

### 第十九轮计划

- **目标模块**：`api/doubao_webhook.py`(849) + `api/memory_routes.py`(468)
- **工作流**：
  1. **`multisource` 扩展到 dict-list**（连续三轮承诺未兑现，本轮必做）
  2. T3 增加跨模块时钟源传播
  3. `falsyzero` 词表按新字段名补充

---

## 附：一个值得注意的模式变化

**连续三个模块（registry 1168 行、skill_routes 1061 行、triggers/engine 488 行）深挖后几乎无缺陷**。

这与前几轮"每挖一个模块必有问题"形成对比。合理解释：

- 这三块是项目的**核心执行链路**，作者投入最多、且有 `.gates.toml` 门禁覆盖
- 缺陷集中在**装配层**（app.py）、**表达层**（tts/、notify/）、**账本层**（store/）—— 即"外围胶水"

**对后续审计的启示**：剩余 69% 代码中，应**优先挖"外围"而非"核心"**。下一轮的 `doubao_webhook.py`(849) 属于外围接入层，预计产出率高于核心模块。
