# doubao-butler 第八轮审计：快速核心扫描

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮方法：**先给工作流加"快速核心扫描"层，再用它把算力集中到真正的核心代码**
> 交付：新增 **V23（P0）**；工作流新增 `fast_core.py`；套件 32 条；全仓库 **7 failed / 617 passed**

---

## 0. 本轮核心结论

**V23（P0）：`mcp/server.py:389` 的 `_run_coro` 在已有事件循环时仍新建事件循环，导致进程级挂死。**

这是八轮以来**第一条能让整个 butler 进程挂死**的缺陷。它不是抛异常——是**连超时保护都救不回来的死锁**。实测 `timeout 25` 强制杀进程才退出（`exit=124`）。

而且它出现在 `mcp/server.py`（640 行），一个**前七轮完全没读过**的文件——是快速核心扫描把我引过去的。

---

## 1. 工作流完善：快速核心扫描（`scripts/fast_core.py`）

### 1.1 为什么需要

全仓 220 文件 / 41k 行，单进程全量扫描约 **43 秒**（S1 28.6s + S3 11.4s + S2 2.7s）。

43 秒本身不算慢，但审计是**迭代**的：每轮反复跑、改规则后再跑、只看一部分再跑。更关键的是——**每次都把 90% 的算力花在已经读过、或根本不可能有问题的文件上**。

核心洞察：**审计的价值密度极度不均匀**。S2 排名前 20 的文件约占全仓 9%，却承载了绝大多数缺陷。所以"快"的正解不是让扫描更快，而是**少扫**。

### 1.2 两阶段设计

```
Phase A（廉价预筛，纯正则，不解析 AST）
  对全仓 220 文件按 10 类危险信号打分：
    run_coroutine_threadsafe / create_task / except:pass / hasattr / time.sleep
    / json.dump / open("w") / return True / 抑制注释 + 分支密度
  → 廉价、可并行、O(文件数)

Phase B（昂贵精扫，AST，只跑 top-K）
  只对 Phase A 选出的 core 文件跑全部不变量 + 反模式 + 复杂度
```

并行用 `multiprocessing.Pool`（AST 解析是 CPU 密集，GIL 会卡死单进程）。

### 1.3 实测性能与召回率

| focus | 耗时 | 召回率 | 扫描文件占比 |
|---|---|---|---|
| 10 | 1.07s | 37.2% | 4.5% |
| 20 | 1.10s | 54.1% | 9.1% |
| **30（默认）** | 1.18s | **58.1%** | 13.6% |
| 60 | 1.34s | 74.4% | 27.3% |
| 80 | 1.34s | 87.8% | 36.4% |

**必须诚实说明**：上表是**热态**测量。首次调用因 `mp.Pool` 冷启动 + 文件缓存未预热，实测 **16.5 秒**。所以真实收益是：

- 热态 ~2.5s vs 全量 43s → **省 94%**
- 冷态 ~16.5s vs 全量 43s → **省 62%**

我最初汇报的"省 97%"是基于 1.08s 的单次异常测量，**那个数字不成立**，已更正。

### 1.4 顺带修的问题

- `phase_b` 对解析失败的文件返回 `[]` 而非 dict，直接 `d["findings"]` 会崩 → 加类型判断
- **账本 RESOLVED 必须按扫描模式隔离**：上一轮跑全量、这一轮跑 `--fast`，findings 集合天然不同，会刷出上百条假"已修复"。已加 `mode` 字段，模式不同则不计算 RESOLVED。

---

## 2. V23（P0）：`_run_coro` 跨事件循环 → 进程挂死

**位置**：`butler/mcp/server.py:389`

```python
async def _run_coro(self, coro):
    """在 async 上下文中执行协程（通过 wrap_future 避免阻塞事件循环）。"""
    try:
        loop = asyncio.get_running_loop()
        # 已有事件循环，在新线程中跑，通过 wrap_future 非阻塞等待
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return await asyncio.wrap_future(pool.submit(asyncio.run, coro))
    except RuntimeError:
        return asyncio.run(coro)
```

注释写着"已有事件循环…非阻塞等待"，但 `asyncio.run(coro)` 在线程里**创建了一个全新的事件循环**。于是：

```
主 loop id = 140214190030464
新 loop id = 140214179772016      ← 实测确证：确实是两个不同的循环
```

### 2.1 三重后果（全部实测）

| 场景 | 结果 |
|---|---|
| 协程 await 主 loop 上的 Future | `ValueError: The future belongs to a different loop` |
| 协程请求主 loop 已持有的锁 | **进程级挂死**，`timeout 25` 强制杀死，`exit=124` |
| 协程内部创建了自己的 Lock | 侥幸工作（Python 3.10 惰性绑定） |

**最要命的是第二种**——不是报错，是**静默挂死**。而且 `fut.result(timeout=N)` 也救不回来：`ThreadPoolExecutor.__exit__` 会 join 那个永久阻塞的工作线程，主线程一起陪葬。

### 2.2 真实触发路径（不是理论风险）

```
MCP 工具调用
  → mcp/server.py:374  await self._run_coro(runner.run(...))
  → skills/runner.py:44  self._out_lock = asyncio.Lock()   ← 锁在主 loop 创建
  → 主 loop 上若已有技能在跑（定时任务/主动播报）持有该锁
  → MCP 的这次调用在新 loop 里请求该锁
  → 新 loop 永远等不到主 loop 的释放通知（wakeup 机制不跨循环）
  → 整个 butler 进程挂死
```

三处调用点全部命中：`runner.run`(374)、`runner.execute`(377)、`bark.push`(416)。

### 2.3 修复（对照实验已验证）

```python
async def _run_coro(self, coro):
    try:
        asyncio.get_running_loop()
        return await coro          # 已有 loop：直接 await，不开新线程
    except RuntimeError:
        return asyncio.run(coro)   # 无 loop：才用 asyncio.run
```

**对照实验**：
- 原写法 → `exit=124`（强制超时杀死）
- 修复后 → `[修复后] 结果 = acquired -> 锁争用被正确等待，holder 释放后恢复`

同 loop 内 await 能正常被唤醒；跨 loop 不能。这就是全部差异。

---

## 3. 快速核心扫描给出的 core 名单（top 15）

```
 1. butler/app.py                     1027 行
 2. butler/mcp/server.py               640 行   ← V23 在此，前七轮未读过
 3. butler/api/skill_routes.py        1061 行
 4. butler/core/dialog.py              770 行
 5. butler/skills/runner.py            457 行
 6. butler/self_evolution.py           433 行
 7. butler/core/xiaomi_ear.py          240 行
 8. butler/decision/aggregator.py      282 行
 9. butler/tools/desk_pilot.py         413 行
10. butler/triggers/health_api.py      203 行
```

注意第 2、3、6、7、10 名**都是前七轮完全没读过的文件**。这才是快速扫描最大的价值——它把"我读哪里"从拍脑袋变成了可计算的。

`api/skill_routes.py`（1061 行，第 3 名）是当前最大的未读盲区，建议下轮优先。

---

## 4. 本轮未解决

- S2 地图仍有多个 ★ 未读文件；本轮只读了 `mcp/server.py` 与 `skills/runner.py` 的关键片段。
- **完整 lifespan、真实 MQTT、SQLite 并发——八轮均未覆盖。**
- 冷启动 16.5s 仍偏慢，可考虑把 Phase A 的正则预编译或改用文件级 mtime 缓存。

---

## 5. 修复优先级（累计更新）

1. **V23（P0，5 分钟）**——`_run_coro` 改为同 loop await。**本轮新增，唯一能挂死进程的缺陷**
2. **V18（P0，5 分钟）**——`docker_tools` async 内 `time.sleep` + 缺 import
3. **V13（P1，30 分钟）**——15 处 `.result(timeout=30)`
4. **V21（P1，3 分钟）**——`fast_routes` 损坏回退内置
5. **V22（P1，10 分钟）**——`handle_webhook` 占位符加 finally
6. **V7 / T-1（P0，20 分钟）**——`dialog.speak` 提前 return
7. **V20（P1，20 分钟）**——4 处原子写
8. **P0-1 + P0-3（25 分钟）**——技能确认 `role`、config `logger`

---

## 6. 八轮的元结论

| 轮次 | 方法 | 新缺陷 | 证伪 / 撤回 |
|---|---|---|---|
| 1–3 | 读代码 + 推断 | 14 | 2 条撤回 |
| 4 | 套件做发现 | 4 | 1 条降级 |
| 5 | 工具链扫描 | 3 | 1 条误判 |
| 6 | 工作流驱动 | 2 | 1 条假设证伪（CC≠缺陷） |
| 7 | 账本 + 未读区 | 1 | 1 个检测器被证伪（INV-5 高噪声） |
| **8** | **快速核心扫描** | **1（P0）** | 1 个性能数字被更正（97%→62~94%） |

本轮只新增 1 条，但它是**八轮里唯一能让进程挂死**的缺陷，且出现在完全没读过的文件里。

同时必须说清覆盖率：八轮下来逐行读过的仍只有 ~10%。**V23 恰恰藏在我从未看过的 `mcp/server.py` 里**——这说明"读得多的地方缺陷已被挖尽，剩余风险集中在盲区"，而快速核心扫描正是把算力导向盲区的工具。
