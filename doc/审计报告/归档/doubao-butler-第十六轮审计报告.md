# doubao-butler 第十六轮审计报告 · multisource 阶段 + tools/registry.py & skills/runner.py

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮主线**：① 新增 `multisource` 阶段（十五轮总结的最高优先级项：多真源漂移）② 深挖计划排期第一站 `tools/registry.py`(1168 行) + `skills/runner.py`(457 行)
- **结论**：**2 个 P1**（1 全新实锤 + 1 孤儿模块），**4 个 P2**

---

## 第一部分：工作流迭代（v1.9 → v2.0）

### 1.1 新增 `multisource` 阶段 —— 多真源漂移检测

十五轮总结指出：28 条结论中**至少 8 条根因相同**——同一概念存在多份真源，每份都不报错只是彼此不一致。本轮固化为阶段。

**方法**：提取跨文件的**字符串序列**（赋值型 `_X = [...]` + 内联型比较中的 tuple/list），两两计算 Jaccard，按漂移排序：

```
j == 1.0 且元素相同 → 重复副本（low，但仍是坏味道）
0 < j < 1.0         → 已发生漂移（high）
```

支持内联序列后：5 对 → **14 对（high 6 / low 8）**。

### 1.2 `concurrency` 的 C1 匹配器放宽

初版只匹配 `^(asyncio\.)?create_task\(`，**漏掉了 receiver 形式**。修正为 `\.?\s*create_task\s*\(` 并覆盖 lambda 体内调用后：

```
C1: 14 → 17
```

新增捕获的两处**都极有价值**：
- `butler/tts/singleton.py:33` —— **第六轮 P0-10 的核心断言（TTS worker Task 未保存引用），由工具独立复现**
- `butler/skills/runner.py:452` —— lambda 内的 `loop.create_task(_run())`

### 1.3 v2.0 实测

| 阶段 | 结果 |
|---|---|
| **multisource** | **14 对（high 6 / low 8）** |
| concurrency | **45 处**（C1 17 / C2 11 / C4 5 / C5 12） |
| atomicity | 35 处（high 33） |
| timeunit | 8 处 |
| lifecycle | 11 处 high |
| dupfiles | 5 个孤儿模块 |

---

## 第二部分：第十六轮审计发现

### P1-44 技能隔离通知**永远发不出去** —— `bark.push()` 传了不存在的 `reason` 参数

- **位置**：`butler/skills/quarantine.py:177-179`

```python
asyncio.get_event_loop().create_task(
    rt.bark.push(f"技能隔离：{skill_id}", reason="技能隔离")   # ← reason 不存在
)
```

#### 实锤

```
真实 Bark.push 参数: [self, body, title, subtitle, level, sound, volume, icon,
                     image, url, group, badge, call, is_archive, ttl, markdown, priority]
含 'reason'? False
含 'title' ?  True

❌ TypeError: got an unexpected keyword argument 'reason'
```

全仓 grep 确认**仅此一处**误用 `reason=`（其余 `bark.push` 调用均正确）。

#### 为什么完全静默（三重掩盖）

1. `create_task()` **返回值丢弃** → 无强引用、无回调、无 `add_done_callback`
2. 协程的 TypeError 在 **await 时**才抛，`create_task` 本身不抛 → 外层 `except Exception: logger.debug(...)` 抓不到
3. 异常最终只在 GC 时可能打出 `Task exception was never retrieved`，**日常日志无痕**

#### 后果

技能因安全违规/超时/熔断被**隔离（quarantine）**时，本该推送到手机的通知永远不会发出。用户看到的现象是：技能突然不工作了，且**不知道它被隔离了、不知道原因**。

对比：`_notify()` 是隔离路径上唯一的对外告知渠道，而它是 100% 失效的。

**修复**（一行）：
```python
rt.bark.push(f"技能隔离：{skill_id}", title="技能隔离")
# 并保存 task 引用 + add_done_callback 记录异常
```

---

### P1-45 `butler/schema.py` 是**第 6 个孤儿模块**，且 `dupfiles` 阶段漏报

- **位置**：`butler/schema.py`（全仓零生产引用）
- **发现途径**：`multisource` 报出 `schema.py:31 _EVENTS` 与 `triggers/schema.py:31 _EVENTS` 漂移

#### 漂移证据

| 文件 | `_EVENTS` 元素数 | 差异 |
|---|---|---|
| `butler/schema.py:31` | 6 | **缺 `heartbeat`** |
| `butler/triggers/schema.py:31` | 7 | 含 `heartbeat` |

#### 孤立性（三重验证）

- `triggers/schema.py` 被 `api/trigger_routes.py`、`app.py`、`mcp/server.py`、`skills/engines/trigger_creator/engine.py`、`triggers/store.py` 引用
- `butler/schema.py` **生产代码零引用**
- 项目自身已钉死：`doc/交付记录_20260930窗口.md` 与 `tests/test_http_contract_top5.py` 均称其为"死副本"（mtime 09-05 20:08 vs 09-30 10:40，缺 `heartbeat` 与 `resources`），文档注明"进他那半张单，⛔ **我删**"——**但至今未删**

#### `dupfiles` 为何漏报（工具缺陷，已定位）

`dupfiles` 在扫描**待判文件**时排除了 `tests/`，但在收集**引用文件**时**包含了 `tests/`**。于是 `tests/test_http_contract_top5.py` 里"butler.schema 是死副本"这句**点名文字**被误计为"存在引用"，导致漏判。

> 这是本轮最有价值的**工作流自省**：工具会因为"文档里提到了它"而认为"它被使用了"。讽刺的是，恰恰是证明它已死的注释救了它。

**修复**：`dupfiles` 的 `ref_files` 应排除 `tests/`，或对 `.md`/测试中的引用降权。**并补删 `butler/schema.py`**。

---

## 第三部分：P2 级发现

### P2-10 `ha.py:313-331` 死代码 —— AST 独立确认（第二轮已报）

用 AST 判定归属，结论确凿：

```
intelligent_speaker 行范围: 299-331
  302: if not ... : return "no token/entity"
  303: try:
  304-308:  return await self.call_service(...)     ← 路径 1 返回
  309: except Exception as e:
  311:  return f"error: {e}"                        ← 路径 2 返回
  313-331: """docstring""" + if + try/async with   ← 两条路径都返回 → 不可达
```

**try 与 except 均有 return** → 313-331 恒不可达。被吞掉的是一段完整的 **HA notify 平台 HTTP 兜底**（`POST /api/services/notify/send_message`）。

后果：`_notify_message_inner` 实际只有 `intelligent_speaker` 一条路，失败即无降级。这是第二轮 P1 的结论，本轮用 AST 归属分析**独立复现**。

### P2-11 `runner.py:452` MQTT 触发的 `create_task` 返回值丢弃

```python
loop.call_soon_threadsafe(lambda: loop.create_task(_run()))
```

**但——我的对照实验未能复现 GC 丢失**：

```
已完成: ['A-discarded', 'B-kept']
A(discarded) 完成: True
B(kept)      完成: True
```

CPython asyncio 在任务调度期间通过 ready queue / future callback 持有强引用，简单场景下不会丢。

因此**降级为 P2**，只保留可确证的部分：
- 无引用 → **无法取消**（关停时该任务仍在跑，同第十三轮 P1-41 的 `_presence_poll_task`）
- 无 `add_done_callback` → 异常仅在 GC 时可能打出，日常无痕

> 这是"验证优先"方法本轮**阻止的一次假阳性**：若按文档教条直接定 P1，就会报一个复现不出来的 bug。

### P2-12 `_out_lock` / `_queued` 的 1-slot 队列语义泄漏

`runner.py:281-290`：
```python
if self._out_lock.locked():
    if on_busy == "queue" and not self._queued:
        self._queued = True          # 置位
    else:
        meta["on_busy"] = "dropped"; return
async with self._out_lock:
    self._queued = False             # 获取锁后立即复位
```
置位后**立即**在锁内复位，因此后续到达者仍能通过 `not self._queued` 检查并加入等待——`_queued` 标志实质上不起作用，队列深度不受控。属并发卫生问题，非功能失效。

### P2-13 `security_monitor` 与 `aggregator` 的空调状态词漂移

- `security_monitor.py:98` 将 `"auto"` 视为空调运行 → 触发"无人在家但空调运行"告警
- `aggregator.py:130/139` **无 `"auto"` 翻译** → 落到 else，界面显示原始字符串 `"auto"`

`multisource` 捕获。属多真源漂移的标准形态：一处认、一处不认。

---

## 第四部分：`tools/registry.py` 深挖结论（1168 行）

**结论：工具分发链路完整，无缺陷。** 这是少见的"干净"模块，值得记录。

| 检查项 | 结果 |
|---|---|
| `TOOL_SCHEMAS` 工具数 | **58**，无重名 |
| 分发覆盖 | **58/58 全覆盖**（21 直接分支 + 37 前缀委托：`tv_` 9 / `desk_` 20 / `newapi_` 4 / `docker_` 4） |
| 子分发器覆盖 | `_dispatch_tvpilot` 9/9、`_dispatch_deskpilot` 20/20、`_dispatch_newapi` 4/4、`_dispatch_docker` 4/4 |
| 重复 `if name ==` 分支（后者不可达） | **无** |
| schema 声明参数 vs 代码读取 | 0 处"代码读了未声明参数"；10 处"未使用"经核查为**误报**（参数打包传入 `_query_schedule(args, agent)` 等 helper，在 helper 内读取） |

**一处死分支**：`speak_to_speaker` 仅存在于 `registry.py:1135` 的 dispatch 分支，**不在 `TOOL_SCHEMAS`、不在 `fast_routes`、全仓无其他引用** → 不可达（low）。

**`show_capabilities` 不是 bug**：虽不在 `TOOL_SCHEMAS`，但被 `fast_routes.py:79` 的 capability_discovery 关键字路由显式使用（"你会做什么"），属设计决策。

**已知项复现**：`get_weather` 硬编码 `http://192.168.2.200:3000/api/weather`（第九轮 P1-33，仍存在）。

---

## 第五部分：本轮**排除**的误报

| 疑似 | 核查结论 |
|---|---|
| `conflict.py:292 llm_engines`(3) vs `sandbox.py:28 SAFE_ENGINES`(4) 漂移 | ❌ **误报**：`conflict.py` 检测 **LLM 资源竞争**，`static_text` 不用 LLM，排除正确 |
| `modes/engine.py:32 MODES` vs `proactive/engine.py:37 DISABLED_MODES` | ❌ 子集关系，语义不同（一个是全集、一个是禁用集） |
| 10 个"schema 声明参数未使用" | ❌ 参数打包传入 helper，在 helper 内读取 |
| `runner.py:452` create_task GC 丢失 | ❌ **实验未复现**，降级 P2 |
| `SAFE_ENGINES` 只含 4/12 引擎 | ⚪ **设计而非缺陷**：`static_text`/`llm_text`/`reminder_find`/`decision` 可自动审批，其余 8 个需人工审批——这是安全边界，符合预期 |

---

## 第六部分：工作流现状与局限

### v2.0 已解决

| v1.9 短板 | v2.0 状态 |
|---|---|
| **R 多真源无工具**（十五轮最高优先级） | ✅ `multisource` 阶段，14 对 |
| C1 漏 receiver 形式 | ✅ 放宽后 14→17，并独立复现第六轮 P0 |
| `dupfiles` 因 tests/ 文字误计引用 | ⚠️ 已定位，**未修**（下一轮） |

### 仍存在的短板

1. **`dupfiles` 的 `ref_files` 含 `tests/`** —— 本轮直接导致漏报第 6 个孤儿，下一轮必修
2. **`multisource` 只覆盖字符串序列** —— P1-43 的 `DEFAULT_ROLES` 是 dict-list，抓不到；需扩展
3. **C 类（逻辑错误）仍空白** —— P1-44 是靠读代码 + 签名比对发现的，非工具
4. **runtime 覆盖 22.07%** —— 371 个方法未执行
5. **代码深挖 23%** —— 本轮新增约 1600 行（registry + runner），进度 ~27%

---

## 第七部分：累计与下一轮计划

### 十六轮累计新增（本轮）

| 编号 | 结论 |
|---|---|
| **V29** | **`quarantine.py:177` bark.push 传不存在 `reason=` → TypeError → 隔离通知 100% 静默失效** |
| **V30** | **`butler/schema.py` 第 6 个孤儿模块，缺 `heartbeat`；`dupfiles` 因 tests/ 文字误计引用而漏报** |
| V31 | `ha.py:313-331` 死代码（AST 确认），HA notify HTTP 兜底不存在 |
| V32 | `runner.py:452` create_task 丢弃（GC 未复现，仅无取消/无异常回调） |
| V33 | `_out_lock` `_queued` 标志实效 |
| V34 | `security_monitor` vs `aggregator` 的 `auto` 漂移 |

### 修复优先级

| 优先级 | 项 | 成本 |
|---|---|---|
| **立刻** | **P1-44 `reason=` → `title=`**（一行）+ 保存 task 引用 | 极低 |
| **立刻** | **P1-45 删除 `butler/schema.py`**（项目文档已批"我删"） | 极低 |
| 立刻 | P0-15 删其余 5 个孤儿文件 | 极低 |
| 本周 | P1-42/43 成员名与角色白名单单一真源 | 中 |
| 本周 | P1-40 收敛 SQLite 连接工厂 | 中 |
| 排期 | P2-10 恢复 `ha.py` 的 notify HTTP 兜底（补 `def` 行） | 低 |

### 第十七轮计划

- **目标模块**：`api/skill_routes.py`(1061) + `store/repo.py`(640)
- **工作流**：
  1. **修 `dupfiles` 的 `ref_files` 排除 `tests/`**（本轮直接导致漏报）
  2. `multisource` 扩展到 dict-list（覆盖 `DEFAULT_ROLES` 类漂移）
  3. 新增 `kwcontract` 阶段——**关键字参数名 vs 函数签名**比对（本轮 P1-44 靠人工发现，应工具化：全仓扫描 `f(..., kw=...)` 与定义签名不匹配）

> P1-44 的形态（传了不存在的 kwarg）是**完全可以自动检测**的：静态遍历所有调用点的关键字参数，与目标函数签名比对。这是下一轮最高优先级的工具项——它能一次性覆盖全仓所有同类误用，而不是靠运气读出来。
