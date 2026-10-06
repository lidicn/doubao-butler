# doubao-butler 稳定性 / 功能性审计报告

- **审计对象**：`lidicn/doubao-butler`（main 分支，单提交 `836df79`，2026-10-02 公开快照）
- **代码规模**：283 个 `.py` 文件 / 54,085 行（Python 83.9%、HTML 10.4%、JS 4.9%）
- **审计方法**：① AST 全量静态扫描（未 await 协程、阻塞调用、异常吞没、裸 `create_task`、BOM/语法）；② 关键链路逐行通读（`app.py`、`bus/mqtt_client.py`、`tts/{queue,manager,playback_queue,nowvoice_tts}.py`、`store/db.py`、`core/dialog.py`、`integrations/tv.py` 等）；③ 作用域/调用图交叉验证
- **结论**：**4 个 P0（功能直接失效或大面积失效）+ 11 个 P1（稳定性/严重降级）**。其中 `core/dialog.py` 的技能创建确认流程是 100% 必现的 `NameError`，`app.py` 的调度器作用域问题会让整个定时系统一次性全灭 —— 这两个建议立即修。

---

## 一、P0 级缺陷（立即修复）

### P0-1 `on_wakeup` 技能草稿确认流程必抛 `NameError`

- **位置**：`butler/core/dialog.py:232-291`（`DialogManager.on_wakeup`）
- **现象**：用户提出技能需求 → 管家生成草稿并追问 → 用户答「确认」→ 抛出 `NameError: cannot access local variable 'role'`，确认流程彻底失效。
- **根因**：`role` 在函数体中的赋值点只有 L252、L293、L297、L300（AST 验证）。其中 L252 位于 `if pending is not None:` 分支内，而 L262 起的技能草稿确认块（`if creator and role_id:`）在 L293 之前执行。常态下 `pending_ask_manager.check(room)` 返回 `None`（或该调用本身抛异常被 L256 `except` 吞掉）→ `role` 从未绑定 → L268/274/278/287/291 的 `_quick_reply(role, ...)` 全部 `NameError`。
- **影响**：技能创建确认 100% 失败；异常被上层兜底吞掉后表现为「管家不回应」，用户侧无报错，极难定位。

**修复方案**（把 role 提升为函数级前置赋值，消除条件依赖）：

```python
async def on_wakeup(self, member: str | None, room: str, text: str = "", trace_id: str | None = None):
    rt = self.runtime
    roles = getattr(rt, "roles", None)
    # P0-1 修复：无论 pending 是否存在，role 都先绑定，避免下游 NameError
    role = roles.get("butler") if roles else None
    ...
    try:
        pending = pending_ask_manager.check(room)
        if pending is not None:
            role_id = (pending.get("payload") or {}).get("role_id", "butler")
            # 保留原逻辑：按 pending 的角色覆盖
            if roles:
                role = roles.get(role_id) or role
```

配套建议：L256 的 `except Exception as e: logger.debug(...)` 改为 `logger.warning(..., exc_info=True)`，否则同类问题会继续被掩盖。

---

### P0-2 调度器作用域 Bug：一个作业注册失败 → 整个定时系统全灭

- **位置**：`butler/app.py:546-750`
- **现象**：只要 `add_job` / `reg.register` / `import AsyncIOScheduler` 中任一处抛异常，`sched` 变量就从未赋值；L741 的 `sched.start()` 抛 `NameError`，被 L749 `except` 捕获后仅打一条 error 日志。**所有定时任务（TTS 缓存清理、指纹清理、决策心跳、安全监控）静默不执行**，且 WebUI/健康检查仍显示「正常」。
- **证据**：L546 `try:` 内 `sched = AsyncIOScheduler(...)`（L549）→ L736 `except: logger.warning(...)` → L740 独立 `try:` 内 `sched.start()`（L741）。注释写的「独立 try 避免连带」并未实现隔离。

**修复方案**（变量前置 + 存在性判断 + 部分失败不影响已注册作业）：

```python
sched = None                      # ← 关键：前置初始化
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    sched = AsyncIOScheduler(timezone="Asia/Shanghai")

    for job_id, fn, trig, kwargs in (
        ("tts_cleanup", _tts_cleanup_job, "cron", dict(hour=4, minute=10)),
        ("fp_cleanup",  _fp_cleanup_job,  "cron", dict(hour=4, minute=20)),
        ("security_monitor", _security_monitor_job, "interval", dict(minutes=15)),
    ):
        try:                       # 逐个隔离：单作业失败不影响其余
            sched.add_job(fn, trig, id=job_id, **kwargs)
        except Exception as e:
            logger.error("scheduler job %s register failed: %s", job_id, e, exc_info=True)
    ...
except Exception as e:
    logger.error("scheduler init failed: %s", e, exc_info=True)

if sched is not None:              # ← 关键：不再产生 NameError
    try:
        sched.start()
        rt._sched = sched
        rt.scheduler = sched
        agent.scheduler = sched
    except Exception as e:
        logger.error("scheduler start FAILED: %s", e, exc_info=True)
    try:
        if getattr(rt, "cron_task_executor", None):
            rt.cron_task_executor.scheduler = sched
            rt.cron_task_executor.init_from_store(store)
    except Exception as e:
        logger.error("cron_task init failed: %s", e, exc_info=True)
else:
    logger.error("scheduler unavailable, ALL periodic jobs disabled")
```

同时建议把「调度器是否 alive」纳入 `/api/health`，让失效可被观测（当前 health 不反映调度器状态）。

---

### P0-3 `store/db.get_conn()` 双重检查竞态 + 关闭后拿到失效连接

- **位置**：`butler/store/db.py`（`_conn` 单例 + `threading.Lock`）
- **现象**：`if _conn is None:` 的判断与赋值不在同一把锁内。并发下两个线程可各建一个连接，后建者覆盖 `_conn`，先建者**泄漏且永不关闭**；更严重的是 `close()` 之后，另一线程仍可能取到已关闭的旧连接 → `sqlite3.ProgrammingError: Cannot operate on a closed database`，在关停/重载窗口期集中爆发。
- **影响**：数据写入随机失败、连接句柄泄漏、容器重启时报错。

**修复方案**（独立锁 + 关闭标志，避免与写锁互相重入）：

```python
_conn_lock = threading.Lock()   # 专用于连接生命周期，不要复用 _lock（非重入，会死锁）
_closed = False

def get_conn():
    global _conn
    with _conn_lock:
        if _closed:
            raise RuntimeError("db already closed")
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")
        return _conn

def close():
    global _conn, _closed
    with _conn_lock:
        _closed = True
        if _conn is not None:
            try: _conn.close()
            finally: _conn = None
```

> 注意：仓库中所有写操作使用 `with _lock:` 包裹 `c.execute`，若 `get_conn()` 复用 `_lock` 且被嵌在 `_lock` 内调用，将因非重入锁**死锁**。上述方案用独立锁规避。

---

### P0-4 NowVoice 下载最坏阻塞 160 秒 → 播报队列饿死

- **位置**：`butler/tts/nowvoice_tts.py:71-75`（`_gen_and_download`）
- **现象**：`for` 循环最多 5 次 `httpx.get(timeout=30)` + `time.sleep(2)`，最坏 **5×(30+2)=160 秒**。该同步函数经 `run_in_executor` 执行，不阻塞事件循环，但 `TTSQueue.run()` 是**单消费者串行**循环 → 一次慢合成会让整个播报队列停滞 160 秒，安全告警等高优先级消息同样被堵。
- **影响**：TTS 雪崩式延迟，用户感知「管家突然不说话几分钟」。

**修复方案**（缩短重试 + 外层硬超时 + 失败即降级）：

```python
# 1) 降低单次代价：重试 2 次、单次超时 10s
for attempt in range(2):
    r = httpx.get(url, timeout=10, follow_redirects=True)
    ...

# 2) 调用侧硬超时（synthesize 内）
loop = asyncio.get_running_loop()
try:
    data = await asyncio.wait_for(
        loop.run_in_executor(None, functools.partial(self._gen_and_download, text, voice_id)),
        timeout=25,
    )
except (asyncio.TimeoutError, Exception) as e:
    logger.warning("nowvoice synth timeout/fail: %s", e)
    return None   # 交给 manager 的降级链（edge-tts → kokoro → Bark）
```

> `wait_for` 无法真正中断线程池中的阻塞读，但能让调用方不再等待并立即降级；配合缩短后的重试，最坏等待从 160s → 25s。

---

## 二、P1 级缺陷（稳定性 / 严重降级）

### P1-1 技能草稿过期后，普通对话被误当技能描述（逻辑穿透）

- **位置**：`butler/core/dialog.py:328-359`
- **现象**：过期分支 `del pending_ask[room]` 后**没有 `return`**，控制流继续落到 L359 `if creator:` → 用户一句普通闲聊被当作新技能描述送进生成器，凭空创建技能草稿。

**修复**：过期分支处理完立即退出。

```python
if pending_expired:
    del pending_ask[room]
    logger.info("pending skill ask expired, dropped")
    return None            # ← 关键：阻止继续下坠到 creator 分支
```

---

### P1-2 全仓库同步 SQLite 阻塞事件循环（系统性抖动源）

- **位置**：`butler/store/db.py` 全局（`WAL` + `busy_timeout=5000`），被 `app.py`、`core/*`、`decision/*` 在协程中直接调用
- **现象**：写锁争用时**每次 DB 调用最坏阻塞事件循环 5 秒**。在此窗口内 MQTT 心跳、HTTP 响应、TTS 播报、WebSocket 全部停顿；叠加 `sched` 每 30 秒的 `_check_decision_timeouts` 同步查询，会出现周期性卡顿。
- **修复**：短期——统一包装到线程池；长期——迁移 `aiosqlite`。

```python
async def db_call(fn, *a, **kw):
    return await asyncio.get_running_loop().run_in_executor(_DB_EXECUTOR, functools.partial(fn, *a, **kw))
# _DB_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="db")
```

优先改造：事件循环内的**写路径**（对话流水、指纹、事件流）与 30 秒轮询任务。

---

### P1-3 `asyncio.create_task` 返回值被丢弃（16 处，dialog.py 占 9 处）

- **位置**：`dialog.py` L229/342/354/376/391/411/449/505 等；`api/decision_routes.py` 2 处；`cron_task.py`、`quarantine.py`、`ask.py`、`health_api.py`、`singleton.py` 各 1 处
- **现象**：CPython 明确要求持有 Task 的强引用；未保存引用的 Task 可能在完成前被 GC 回收 → **播报静默丢失、定时任务不执行**，且无任何日志。
- **修复**：统一收纳到 Runtime 的背景任务集合。

```python
def spawn(self, coro, name=""):
    t = asyncio.create_task(coro, name=name)
    self._bg_tasks.add(t)
    t.add_done_callback(self._bg_tasks.discard)
    return t

# lifespan finally 中统一收尾
for t in list(rt._bg_tasks): t.cancel()
await asyncio.gather(*rt._bg_tasks, return_exceptions=True)
```

---

### P1-4 `run_coroutine_threadsafe` 返回的 Future 无人处理（15+ 处）

- **位置**：`app.py` L550/554/559/…（调度作业统一形态）
- **现象**：`asyncio.run_coroutine_threadsafe(coro, _main_loop)` 返回的 `Future` 被丢弃 → 协程内异常**静默消失**，定时作业失败后无任何告警，只会表现为「这个任务好像没跑」。
- **修复**：

```python
def _fire(coro, tag=""):
    fut = asyncio.run_coroutine_threadsafe(coro, _main_loop)
    fut.add_done_callback(lambda f: f.exception() and logger.error("job %s failed: %s", tag, f.exception()))
    return fut
```

---

### P1-5 `presence_poll_task` 关闭时不取消 → 任务泄漏

- **位置**：`app.py:471` 创建 `rt._presence_poll_task`；`finally` 段（L780-794）**只 `consumer.cancel()`**，未取消该任务。
- **现象**：`_presence_poll_loop` 为 `while True`，容器重启 / uvicorn reload 时输出 `Task was destroyed but it is pending`；重装载场景会残留多个 polling 循环重复发 MQTT。
- **修复**：

```python
finally:
    for t in (consumer, getattr(rt, "_presence_poll_task", None)):
        if t: t.cancel()
    await asyncio.gather(*[t for t in (consumer, getattr(rt,'_presence_poll_task',None)) if t],
                         return_exceptions=True)
```
并给 `_presence_poll_loop` 增加 `rt._stop_event` 退出条件（而非裸 `while True`）。

---

### P1-6 TTS 队列：dequeue 侧播放 P1 时不清熔断冷却

- **位置**：`butler/tts/queue.py`（`_clear_breaker` 只在 enqueue 侧调用）
- **现象**：熔断冷却期间，队头 P1（最高优先级，如安全告警）被 `popleft` 播放，但冷却计数未清除 → 冷却结束的瞬间，累积的失败计数立刻再次触发熔断 → **队列在「冷却→播放→立刻再熔断」之间反复横跳**。
- **修复**：在 dequeue 播放 P1 成功后同样清除冷却（与 enqueue 侧共用同一函数）。

```python
# dequeue 分支内，成功播放 P1 后
if item.priority == 1:
    self._clear_breaker()
```

---

### P1-7 TTS 缓存 key 不含 `speed` → 语速设置静默失效

- **位置**：`butler/tts/manager.py`（`_cache_hit` / `key = sha1(f"{text}|{voice}|{engine}")`）
- **现象**：同一文本换语速后命中旧缓存，播放的仍是旧语速音频。
- **修复**：

```python
key = hashlib.sha1(f"{text}|{voice}|{engine}|{speed}".encode()).hexdigest()
```
同时建议在命中时 `os.utime(path, None)` 刷新 mtime，避免常用短语 7 天后被 `cleanup_old` 删除后反复重合成。

---

### P1-8 NowVoice `speed` 参数被丢弃 + `is_available` 恒真

- **位置**：`butler/tts/nowvoice_tts.py`（`synthesize(speed=...)` 未透传给 `generate_speech`）
- **现象**：① 语速配置对该引擎完全无效；② `is_available()` 即使 token 为空且匿名登录失败也返回 `True` → 健康检查与 UI 误报「引擎可用」，实际每次调用都重新登录并失败。
- **修复**：

```python
async def synthesize(self, text, voice=None, speed=1.0, ...):
    ...
    await self._client.generate_speech(text, voice_id, language_code, speed=speed)  # 透传

async def is_available(self) -> bool:
    try:
        await self._ensure_login()
        return bool(self._token)
    except Exception:
        return False
```

---

### P1-9 MQTT `except asyncio.QueueFull` 是死代码

- **位置**：`butler/bus/mqtt_client.py`（`_on_message` → `call_soon_threadsafe(queue.put_nowait, ...)`）
- **现象**：`put_nowait` 在**事件循环线程**中执行，抛出的 `QueueFull` 不会传播回 paho 网络线程的 `_on_message`，`except asyncio.QueueFull` 永远捕获不到。真实表现是事件循环默认异常处理器打印 `Exception in callback`，**消息静默丢弃**。
- **修复**（二选一）：回调内部捕获，或去掉队列上限并改用丢弃最旧策略。

```python
def _put():
    try:
        self.queue.put_nowait(msg)
    except asyncio.QueueFull:
        try: self.queue.get_nowait()      # 丢最旧，保证新事件不丢
        except asyncio.QueueEmpty: pass
        try: self.queue.put_nowait(msg)
        except asyncio.QueueFull: pass
        logger.warning("mqtt queue full, dropped oldest")
self.loop.call_soon_threadsafe(_put)
```

---

### P1-10 TTS 过载保护把「当前这条」也丢了

- **位置**：`butler/tts/queue.py`（`enqueue_item` 过载分支）
- **现象**：`len(self._q) + 1 >= overload_threshold` 时清空队列并 `return OVERLOAD_RESET(item=None)` —— **触发过载的这条新消息同样不入队**。若这条正好是安全告警，则永久丢失。
- **修复**：清空后保留当前 item 入队；P1 消息豁免过载清理。

```python
if len(self._q) + 1 >= self.overload_threshold and item.priority != 1:
    self._q.clear()          # 仍清理积压
    self._q.append(item)     # ← 但当前这条必须保留
    return "OVERLOAD_RESET", item
```

---

### P1-11 播放队列 worker 异常退出后不重建 → 设备永久哑火

- **位置**：`butler/tts/playback_queue.py`（`_ensure_worker`）
- **现象**：worker Task 若因非 `CancelledError` 退出（如底层异常逃逸、`asyncio.CancelledError` 之外的 `BaseException`），`_workers[device_id]` 留下已结束的 Task，而 `_queues` 仍在 → 该设备**此后完全无法播放**，直到进程重启。当前 `_consume` 有 `except Exception` 兜底，风险中等但不可接受（依赖单点兜底）。
- **修复**：

```python
t = self._workers.get(device_id)
if t is None or t.done():
    if t is not None and t.done():
        logger.error("playback worker for %s died, restarting", device_id)
        try: t.result()               # 把真实死因打出来
        except Exception: logger.exception("worker crash")
    self._workers[device_id] = asyncio.create_task(self._consume(device_id))
```

---

## 三、P2 级隐患（排期修复）

| # | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| P2-1 | `tts/queue.py` | `pause(duration_s=None)` → `float("inf")`，调用方漏传参数即**永久静音** | 默认改为有限值（如 300s），或强制关键字参数 |
| P2-2 | `tts/queue.py` | `run()` 无消息时每 50ms 唤醒，busy-loop 空转 | 改为 `await asyncio.wait([get_task], timeout=...)` 或直接 `await self._q.get()` |
| P2-3 | `core/dialog.py` | 回声抑制 `suppress_echo` 在**合成前**按 `len*0.4+6` 估算，合成慢于该窗口时抑制提前结束 → 自问自答死循环 | 合成完成后按**实际音频时长**重设窗口；TV 侧同样注册抑制 |
| P2-4 | `bus/mqtt_client.py` | `adm_heartbeat` 任务无取消路径；`clean_session=False` + 固定 `client_id` 使新旧容器互相踢 | 保存 Task 并在 `stop()` 中取消；client_id 加实例后缀 |
| P2-5 | `app.py:264` | async `lifespan` 中 `subprocess.run(timeout=10)`，最多阻塞事件循环 10 秒 | 改 `asyncio.create_subprocess_exec` |
| P2-6 | `app.py:932-965` | `_start_evolution_scheduler` 用 daemon 线程 + `time.sleep` 等到凌晨 3 点，且 `evo.analyze()` 在线程内同步执行（若内部触碰 asyncio 会跨线程报错） | 改为 APScheduler 的 cron 作业，与调度器生命周期统一 |
| P2-7 | `app.py:348/745` | `CronTaskExecutor` 创建时 `scheduler=None`，依赖 L746 补救；若 `init_from_store` 抛异常，cron 任务全部不加载 | 补救逻辑加独立 try + 失败告警（已在 P0-2 修复代码中覆盖） |
| P2-8 | `roles/store.py:1`、`tts/edge_tts.py:1` | 文件首字节为 U+FEFF BOM；CPython 可正常运行，但动态 `exec` / 部分 lint 链路会炸 | `sed -i '1s/^\xEF\xBB\xBF//'` 去除 |
| P2-9 | 全局 30+ 处 | `except Exception: pass` 异常吞没（`ha.py:619/629/644`、`doubao_webhook.py:422/427/438`、`audiobook/manager.py:322/389/402`、`core/xiaomi_ear.py:174`、`decision/aggregator.py:167` 等） | 至少 `logger.debug(..., exc_info=True)`；关键路径改 warning |
| P2-10 | `core/dialog.py:311` | `presence_rooms` 越界只 log 不拒绝，语义矛盾 | 明确为「拒绝并提示」或补充说明性注释 |

---

## 四、已复核并**排除**的疑似问题（避免误改）

以下项经交叉验证确认为设计如此或误报，不要按 bug 处理：

1. **`nowvoice_tts.py` 的 `time.sleep` / `httpx.get`**：`_gen_and_download` 经 `run_in_executor` 在线程池执行，**不阻塞事件循环**（真正的风险是 P0-4 的耗时过长）。
2. **`TVClient.play_url` 调用点未 `await`**（`dialog.py:514/574/583/703`、`tts_routes.py:177/296/327`、`role_routes.py:79`）：`tv.py:43` 定义为**同步方法**，不 await 正确。
3. **trace ContextVar 双路径**：`core/agent.py:22` 的 `_current_trace_id` 与 `core/trace_ctx.py:8` 的 `current_trace_id` 是**同一对象**（别名），非 bug。
4. **`playback_queue.enqueue` 队列满丢最旧**：显式防内存膨胀设计，非缺陷。
5. **`_CircuitBreaker.is_open` 的副作用**（冷却到期时重置状态）：逻辑正确，仅属代码坏味。

---

## 五、修复优先级路线图

| 阶段 | 内容 | 预期收益 |
|---|---|---|
| **第 1 天** | P0-1（`role` 前置赋值）、P0-2（`sched=None` 前置 + 逐作业隔离）、P1-1（过期分支 `return`） | 恢复技能创建确认；定时系统不再整体失效；消除凭空建技能 |
| **第 2-3 天** | P0-3（连接锁）、P0-4（TTS 超时降级）、P1-6/P1-7/P1-8/P1-9/P1-10 | 消除 DB 随机失败与播报雪崩；语速生效；过载不再丢关键消息 |
| **第 1-2 周** | P1-2（DB 异步化）、P1-3（Task 引用）、P1-4（Future 异常）、P1-5（退出清理）、P1-11（worker 重启） | 系统级抖动消失；后台任务失败可观测；长跑不再泄漏 |
| **持续** | P2 全量 + 将「调度器 alive / MQTT 连接 / TTS 队列深度」纳入 `/api/health` | 故障从「静默失效」变为「可观测告警」 |

---

## 六、审计局限（需说明）

- 本次对 **283 个 Python 文件中约 15 个关键模块**做了逐行通读，其余模块结论来自 AST 静态扫描，**存在误报/漏报可能**。
- 静态扫描标记的 **约 60 处「未 await 协程」、约 30 处「异常吞没」未逐条复核**，仅将已确认上下文的部分写入报告（P2-9 表中列出的是高频命中位置）。
- 未执行单元测试与集成验证（沙盒无 MQTT / Home Assistant / doubao2api 依赖环境）。建议修复 P0 后补充两个回归用例：① 技能草稿确认流程（无 pending 场景）；② 调度器单作业注册失败后其余作业仍运行。
- 部署层面（NAS Docker、固定 `client_id` 互踢、Kokoro 可选镜像）仅从代码推断，未做实机验证。
