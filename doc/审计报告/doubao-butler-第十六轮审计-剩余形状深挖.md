# doubao-butler 第十六轮审计：剩余扩散形状深挖

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮方法：把 `propagate.py` 里三个**标记为 TODO 的形状挖到底**
> 交付：**V30（P1，本轮新发现）**、**V31（P3）**；套件 31 场景 / 62 条用例；全仓库 **7 failed / 649 passed**（新增 4 passed，无回归）；覆盖度 **16.8%**

---

## 0. 一句话

**三个 TODO 形状全部挖完，产出 1 个真 P1、1 个残留文件，外加 2 条证伪。**

而且 V30 揭示了一个此前没被单独命名的问题类型：**兜底方向选错**——条件「无法确认」时被当成「满足」。

---

## 1. 形状排查状态（全部清空）

| 形状 | 上一轮 | 第十六轮 | 产出 |
|---|---|---|---|
| `new-loop` | done @R10 | — | V24 |
| `direct-speak` | done @R11 | — | V26 |
| **`success-err`** | **TODO** | **done @R16** | **V30 + V31** |
| **`blocking`** | **TODO** | **done @R16** | 1 真 1 证伪 |
| **`nonatomic`** | **partial @R6** | **done @R16** | 逐个核实降级行为 |

---

## 2. V30（P1，本轮新发现）：定位服务一挂，管家对着空房间说话

**位置** `butler/triggers/engine.py:302`

```python
try:
    res = await self.rt.locator.find_member(member or "")
    return bool(res.get("found"))
except Exception as e:
    logger.warning("trigger %s require_presence check failed: %s", trig["id"], e)
    return True          # ← 定位服务一挂，"人在不在"就被判成"在"
```

**实测**（真跑 `handle_event`，让 `locator.find_member` 抛 `RuntimeError`）：

```
定位服务抛异常 → require_presence 返回 True → trigger 照常触发（t1）
```

**后果**：memory-agent 不可达 / 超时时，所有带 `require_presence` 的 trigger 全部放行 —— 管家对着没人的房间主动说话，日志里只有一条 warning。

**这是"兜底方向选错"的又一例**：条件**无法确认**时应保守跳过，而不是当成"满足"。与本仓 P0-3、V21、T-2 同源。

**反测**：改 `return False` 后，同样场景下 `触发的 trigger = []`。

⚠ **权衡必须说清**：定位服务长期不可达会导致这类 trigger **全不触发**。所以更稳的做法是「连续 N 次失败才降级」，而不是一次性翻转。我在报告里保留了这个警告，避免机械照改引入新问题。

---

## 3. V31（P3）：`butler/engine.py` 是零引用旧版残留（249 行）

与在役的 `butler/triggers/engine.py`（488 行）**类同名、方法同名**，是迁移后未删除的旧实现。

证据链：
- 全仓 `grep butler\.engine` **零命中**（排除 triggers）
- vulture 报 unused method：`set_runtime` / `handle_event` / `status`
- `app.py:474` 只 import `butler.triggers.engine`
- **决定性指纹**：`engine.py` 内部的 logger 名已经是 `"butler.triggers.engine"` —— 说明它是 triggers 版本的旧身，改名时忘了删

**危害不在运行**（不会被加载），而在**误导**：V30 的同一个 bug 在两处各有一份，改了在役那处，旧文件仍会让人以为已修；排查时 grep 命中两份，容易改错文件。

**反测**：在临时副本里删掉 `engine.py`，`import butler.triggers.engine` 仍正常 —— 无隐藏依赖，可安全删除。（未改动真仓库。）

---

## 4. 两条证伪（同样是结论）

### 4.1 `app.py:264` 的 async 内 `subprocess.run` —— 不是缺陷

第五轮我标过"P1 未核实"。本轮核实：它在 `lifespan()` 内，是**启动期一次性生成 `wake_ding.mp3`**，且 `timeout=10`。此时服务尚未开始响应请求，阻塞无影响，且已有 `timeout` 保护。**诚实降为 P3。**

### 4.2 `nonatomic` 4 处不能一律判 P1

第六轮我做过一轮核实，本轮复查确认：`cron_task.py:110` 用 JSONL 且逐行容错，只丢截断那一行，实际风险接近零（P3）；`fast_routes.py:43` 损坏后静默置空连内置都不恢复（V21，P1）。**同一个形状在不同读取侧降级行为下，严重度差两级。**

这印证了一条方法论：**形状只负责提名，严重度必须逐个核实读取侧**。

---

## 5. 顺带确认：`tts/playback_queue.py` 不是死代码

排查时一度怀疑它是孤儿（只在 `manager.py:67` 被实例化）。核实：`PlaybackQueue(ha)` 是每台设备的播放队列，由 `TTSManager` 持有并实际使用，`app.py:286` 还专门同步 `ha` 给它。**不是死代码，不删。**

（这次幸好没误删——又一次说明"零入度/少引用 ≠ 死代码"。）

---

## 6. 十六轮元观察

| 轮次 | 层 / 方法 | 新缺陷 |
|---|---|---|
| 13 | 代码图谱 | 1 (P3) |
| 14 | 变异测试 | 0（测的是测试本身） |
| 15 | **并发验证** | **2（P0 实证 + 新 P1）** |
| **16** | **扩散形状深挖** | **1 (P1) + 1 (P3)** |

**每换一个"没挖过的形状"就重新产出**——这已经是第三次验证这个规律（R10 new-loop、R11 direct-speak、R16 success-err）。

---

## 7. 诚实边界

1. **V30 的触发频率未量化**：取决于 memory-agent 实际不可达的频率，本轮只证明了"会发生"。
2. **V31 的删除建议未执行**：反测在临时副本上做，真仓库未改动。
3. 覆盖度 **16.8%（37/220）**。本轮只深挖了 2 个文件。
4. **完整 lifespan 仍未跑起来**——真实 MQTT、16 个定时任务，十六轮零进展。
5. 全仓库 7 failed 仍全部来自 `test_v25_pytest_shim.py` 与 deskpilot/tvpilot，与本项目改动无关。

---

## 8. 修复优先级（累计 5 个 P0 + 13 个 P1，仍为零修复）

1. **V23**（P0，5 分钟）`_run_coro` 改同 loop await —— 唯一能挂死进程
2. **V18**（P0，5 分钟）`docker_tools` async 内 `time.sleep` + 缺 import
3. **V25 / V28**（P0，15 分钟）SQLite 发布顺序（第十五轮已实证）
4. **V30**（P1，1 行）`require_presence` 失败改保守跳过 ← **本轮新增**
5. **V29**（P1，20 分钟）写操作加锁或每线程独立连接
6. **V26**（P1，10 分钟）anomaly 补 `device_id`、删 `priority`
7. **V24**（P1，1 行）`await asyncio.to_thread(...)`
8. **V13**（P1，30 分钟）15 处 `.result(timeout=30)`
9. **V21 / V22 / V7 / V20 / P0-1 / P0-3**
10. **V31**（P3，1 分钟）删除 `butler/engine.py` ← 本轮新增

**前三条合计约 25 分钟。**
