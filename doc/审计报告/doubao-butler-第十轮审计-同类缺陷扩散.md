# doubao-butler 第十轮审计：同类缺陷扩散排查

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮方法：**新增工作流能力「同类缺陷全仓排查器 `propagate.py`」，用已实锤缺陷的形状去搜全仓同类**
> 交付：新增 **V24（P1）**、**V25（P0-2 确证）**；**1 个候选证伪**；工作流新增 `propagate.py`；套件 **36 条用例**；全仓库 7 failed / 621 passed
> 说明：原计划精读 `store/repo.py` + `proactive/engine.py`，但排查器深挖产生了更高价值发现，本轮转为扩散排查

---

## 0. 本轮核心结论

**新增工作流层 `propagate.py`：把已实锤缺陷抽象成"形状"，去搜全仓同类。**

这是十轮以来方法论上最重要的一次升级。前九轮每发现一个缺陷，排查范围止于该处；而 V23（跨事件循环挂死）的性质决定了它**几乎不可能只存在一处**——它是一类写法，不是一个点。

用 V23 的形状（"已有 loop 却 `asyncio.run` 新起循环"）搜全仓，命中 **34 处**（去重后 19 处）。深挖后：

- **V24（P1）实锤**：`cron_task.test_api` 在 async 路由内同步阻塞，实测事件循环停顿 **514ms vs 对照组 11ms（46 倍）**
- **1 个候选证伪**：`runner.py:450` 的 MQTT 触发看似同类，实为安全（详见 §3）
- **V25（P0-2 确证）**：顺带把第一轮遗留的静态推测坐实

---

## 1. 工作流迭代：`propagate.py`

把五条已实锤缺陷抽象为可复用形状：

| 形状 ID | 源缺陷 | 检测目标 |
|---|---|---|
| `new-loop` | V23 | 已有 loop 时仍 `asyncio.run` / `get_event_loop().run_until_complete` |
| `direct-speak` | V15 | 绕过 `TTSQueue` 直接发声 |
| `success-err` | V13 | 捕获异常后仍返回成功 |
| `blocking` | V18 | async 内阻塞调用 |
| `nonatomic` | T-2 / V20 | 状态文件非原子写 |

**这一层的价值上限取决于已实锤缺陷的质量**——它是"放大器"，不是"发现器"。但放大器正是前九轮缺的：我们一直在发现单点，从未系统性追问"还有没有同款"。

---

## 2. V24（P1）：`test_api` 在 async 路由内阻塞整个事件循环

**链路**：

```
api/cron_task_routes.py:175  async def test_api_direct
   └─ L184  result = executor.test_api(api_id)      ← 同步调用，无 to_thread
        └─ core/cron_task.py:171  def test_api（同步）
             └─ L176-183  with ThreadPoolExecutor(...) as pool:
                              fut = pool.submit(asyncio.run, _test_api_async(...))
                              return fut.result()            ← 无 timeout
```

**实测数据**（本轮做的阻塞实验）：

| | 事件循环最大停顿 |
|---|---|
| 原写法（async 内直接调同步） | **514 ms** |
| 对照组（`await asyncio.to_thread(...)`） | **11 ms** |

**相差 46 倍。** 停顿时长取决于被调 API 配置的 `timeout`——配置得越长，butler 停摆越久。

**后果**：用户在 WebUI 点「测试 API」，**整个 butler（TTS、MQTT、对话、定时任务）停摆到该请求超时**。

**与 V23 的关系**：同为"新起事件循环"形状。但 `_test_api_async` 自建 `httpx.AsyncClient`、不触碰主 loop 绑定对象，所以**当前只表现为阻塞，不是挂死**——除非将来它开始依赖主 loop 上的共享状态，那就升级为 V23 级挂死。**这是一颗定时炸弹，不只是性能问题。**

**修复**（1 行）：`result = await asyncio.to_thread(executor.test_api, api_id)`。反测已验证可 apply 且语法成立。

---

## 3. 一个候选被证伪：`runner.py:450` 的 MQTT 触发

`propagate.py` 把 `runner.py:450`（`asyncio.get_event_loop()`）标为 new-loop 候选，注释写着"MQTT 回调在 paho 网络线程"。

**追查结论：安全，候选撤销。**

链路实为：

```
mqtt_client.py:71  _on_message（paho 线程）
    └─ self.loop.call_soon_threadsafe(self.queue.put_nowait, ...)   ← 正确跨线程投递
app.py:96          async def _consume（主事件循环内消费 queue）
    └─ runner.on_mqtt_trigger(...)   ← 此处 get_event_loop() 返回的是运行中 loop
```

`_consume` 是 `async def`，运行在主事件循环内，故 `get_event_loop()` 返回正在运行的 loop，`call_soon_threadsafe` 正常工作。**注释描述的"paho 网络线程"是过时的——消息已被正确投递出网络线程。** 属注释误导，非代码缺陷。

顺带确认全仓**无 `set_event_loop`**，其他 `get_event_loop()` 站点（`ha.py:593/637`、`nowvoice_tts.py:42/53`、`quarantine.py:177`）均由 async 上下文调用，安全。

`tools/schedule.py:68` 是**正确范例**：`_safe_fire` 注册为 APScheduler 任务，在调度线程内 `run_coroutine_threadsafe(...).result(timeout=30)`——有 timeout 且阻塞的是工作线程，不是事件循环。

---

## 4. V25（P0-2 确证）：SQLite 懒初始化竞态

**第一轮标为"静态推测"，十轮未证实。本轮读 `store/db.py:33-63` 确证。**

```python
def get_conn():
    global _conn
    if _conn is None:                 # ① 检查（锁外）
        c = sqlite3.connect(...)      # ② connect + PRAGMA
        with _lock:
            _conn = c                 # ③ 锁只包了「赋值」
        _init(_conn)                  # ④ 建表，在锁外
    return _conn
```

三重问题：

1. **check 在锁外** → 两线程同时看到 `None`，各建一个连接，后者覆盖前者，前者泄漏（未 close）
2. **`_conn` 赋值先于 `_init` 完成** → 其他线程可能拿到"表还没建好"的连接 → `no such table`
3. **写操作完全无锁**：`repo.py` 有 **38 处** `get_conn()` / **0 处** 带锁，只靠 `busy_timeout=5000`

### 反测抓到了我自己写错的修法

这是本轮最有价值的一段——**反测证明我给的第一版修复方案是错的**：

| | 创建连接 | 半初始化连接被取用 |
|---|---|---|
| 原结构（8 线程） | 1 | **7** |
| ✗ 第一版修法（仅加双重检查，仍先赋值后 init） | 1 | **7** ← 没改善 |
| ✓ 正确修法（init 完成后才发布） | 1 | **0** |

原因：其他线程根本不进锁——外层 `if _conn is None` 已经是 False，直接取走了还没 init 完的连接。**加双重检查不够，必须"初始化完成后再发布"。**

```python
with _lock:
    if _conn is None:
        c = connect(...); ...PRAGMA...
        _init(c)        # 建表
        _conn = c       # ← 发布（此时才对外可见）
```

**这正是"反测不许假通过"这条纪律的价值**——如果反测写得宽泛，这个错误修法会被当成已验证通过，写进报告误导维护方。

⚠ 该反测为 **pattern-level**（复刻控制流起真线程），非真跑 `db.py`。真跑属 L4。

---

## 5. 本轮未做 / 边界

- **原计划的 `store/repo.py` + `proactive/engine.py` 精读未完成**——排查器深挖占用了本轮。后者含已知 `via="direct"` 绕过队列（V15），建议下轮补上。
- V25 的**运行时多线程复现仍未做**（L4）。当前证据是源码结构 + pattern-level 实验，不是真跑。
- 完整 lifespan、真实 MQTT —— **十轮零进展**。
- 覆盖度：21/220 文件（9.5%）。

---

## 6. 修复优先级（累计更新）

1. **V23（P0，5 分钟）**——`_run_coro` 改同 loop await（唯一能挂死进程）
2. **V18（P0，5 分钟）**——`docker_tools` async 内 `time.sleep` + 缺 import
3. **V25（P0，15 分钟）**——SQLite 发布顺序 + 写操作加锁 ← **本轮确证**
4. **V24（P1，1 行）**——`await asyncio.to_thread(...)` ← **本轮新增**
5. **V13（P1，30 分钟）**——15 处 `.result(timeout=30)`
6. **V21（P1，3 分钟）**——`fast_routes` 损坏回退内置
7. **V22（P1，10 分钟）**——`handle_webhook` 占位符加 finally
8. **V7 / T-1（P0，20 分钟）**——`dialog.speak` 提前 return
9. **V20（P1，20 分钟）**——4 处原子写
10. **P0-1 + P0-3（25 分钟）**——技能确认 `role`、config `logger`

---

## 7. 十轮元结论

| 轮次 | 方法 | 新缺陷 | 证伪 |
|---|---|---|---|
| 1–3 | 读代码 + 推断 | 14 | 2 条撤回 |
| 4 | 套件做发现 | 4 | 1 条降级 |
| 5 | 工具链扫描 | 3 | 1 条误判 |
| 6 | 工作流驱动 | 2 | CC≠缺陷 |
| 7 | 账本 + 未读区 | 1 | INV-5 高噪声 |
| 8 | 快速核心扫描 | 1（P0） | 性能数字更正 |
| 9 | 混合聚焦 | 0 | 性能数字更正 |
| **10** | **同类扩散排查** | **2** | **1 个候选证伪 + 1 个修法被反测推翻** |

**本轮打破了两轮的产出递减（1→0→2）。** 这印证了路线图里的判断：产出递减不是"缺陷挖完了"，而是**方法触顶**——换方法（从"找新形状"到"搜已知形状的同类"）就能重新产出。

这也修正了我在路线图里的一个过度悲观判断：不必急着放弃静态分析，**扩散排查这一层还没挖完**。目前只深挖了 `new-loop` 一个形状，`direct-speak` / `success-err` / `blocking` / `nonatomic` 四个形状尚未系统性排查。
