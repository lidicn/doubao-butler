# doubao-butler 全面代码审计报告

> 审计对象：`lidicn/doubao-butler`（main, `836df79`, 2026-10-02 公开快照）
> 审计范围：`butler/` 全部 220 个 Python 文件 / 41,211 行 + 45 个测试文件 + 项目自带质量门禁 `.gates.toml` / `.gates-baseline.txt`
> 审计方法：静态检查（全量规则扫描）+ 核心链路逐行精读（app.py、store/db.py、bus/mqtt_client.py、bus/inbox.py、core/dialog.py、tts/queue.py、tts/manager.py、integrations/ha.py、integrations/llm.py、api/deps.py 等）+ 并发/资源/降级路径模式扫描
> 审计目标：只挑**影响稳定性与功能性**的真实缺陷，每条给出可落地的修复方案

---

## 0. 结论速览

| 等级 | 数量 | 定义 |
|---|---|---|
| **P0** | 4 | 核心功能 100% 失效 / 启动即崩 / 全站不可用 / 长时间静默 |
| **P1** | 10 | 稳定性、并发、资源泄漏、降级链被绕过、事件丢失 |
| **P2** | 10 | 死代码、静态错误、边界未校验、可维护性风险 |

**一句话结论**：项目的容错设计（全局异常兜底、三级 TTS 降级、过载保护、熔断）在**架构层面是完整的**，但多处**实现细节把容错变成了故障源**——熔断对成功出队计数（正常对话即触发静默）、过载保护清空队列时把告警一起丢掉、全局异常兜底把 4 处 `NameError` 级别的硬 bug 藏成了"不响"。最严重的是 **技能创建确认路径必抛 `UnboundLocalError`** 与 **SQLite 单连接跨线程共享**。

静态检查总体面：599 处盲捕 `except Exception`、47 处 `except: pass`、4 处 async 内阻塞 IO、96 处未用导入。异常吞噬面过大是本次审计最大的系统性风险（详见第 5 节）。

---

## 1. P0 致命缺陷

### P0-1 技能创建"确认/取消"路径必崩：`role` 未绑定

**位置**：`butler/core/dialog.py:261-291`（4 处调用点在 274 / 278 / 285 / 287-291）

**现象**：用户通过语音创建技能 → 管家生成草稿并问"要创建吗？" → 用户回答"确认" → 该轮对话抛 `UnboundLocalError: local variable 'role' referenced before assignment`，HTTP 侧返回 500，**技能永远无法被确认创建**。

**根因**：`on_wakeup` 的签名是 `(self, role_id, room, message, member, source_device, source)`——**没有 `role` 参数**。函数内 `role` 只在两处赋值：

- `dialog.py:253`（Ask 挂起分支内，条件命中才赋值）
- `dialog.py:300-302`（角色解析，**在本分支之后**）

技能草稿确认分支位于二者之间，直接引用 `role`：

```python
creator = getattr(rt, "skill_creator", None)
if creator and role_id:
    pending = creator.get_pending(role_id)
    if pending:
        verdict = classify_answer(message)
        ...
        if verdict == YES:
            result = creator.confirm(role_id)
            ...
            return await self._quick_reply(role, room, member, reply, source_device)  # ← role 未绑定
```

同时该分支**没有 try 包裹**，异常直接穿透到 API 层。讽刺的是 `_quick_reply` 自身已经处理了 `role=None`（`dialog.py:223-225` 回退 butler），说明作者清楚这个边界，只是调用处没传。

**影响**：技能创建这一核心卖点 100% 不可用；异常被全局兜底吃掉后只表现为"管家不说话"，无任何日志指向真实原因。

**修复**（在分支入口解析角色，`role_id` 一定非空）：

```python
creator = getattr(rt, "skill_creator", None)
if creator and role_id:
    role = rt.roles.get(role_id) or (rt.roles.get("butler") if rt.roles else None)  # ★ 新增
    pending = creator.get_pending(role_id)
    if pending:
        ...
```

**加固**：给整段技能草稿处理加兜底，避免"创建流程出任何错 → 管家不响"：

```python
try:
    ...  # 技能草稿确认全流程
except Exception:
    logger.exception("skill draft consent failed, falling through to normal dialog")
```

---

### P0-2 SQLite 全局单连接跨线程共享，存在启动期竞态与写入冲突

**位置**：`butler/store/db.py`（`get_conn` / `_conn` / `_lock` / `_init`）

**现象（三类）**：

1. **懒初始化竞态**：`if _conn is None:` 的判定在锁外，两个线程（MQTT 回调线程、scheduler 线程、事件循环）可同时通过判定，各建一条连接，先建的那条被覆盖后**永不关闭**（句柄泄漏），WAL 下两条连接各自持读视图。
2. **半初始化连接对外可见**：现有顺序是先赋值 `_conn` 再跑 `_init`（建表 / ALTER 迁移）。`_init` 执行期间，其他线程已可从 `get_conn()` 拿到**表还没建完**的连接 → `no such table: dialog_turns`，且这类失败发生在启动期，此后连接被复用，错误持续。
3. **无保护的并发 execute/commit**：`_lock` 只保护"创建"和"关闭"，不保护 DML。写操作在 WAL 下多写者并发 → `database is locked`；`busy_timeout=5000` 只能缓解，无法覆盖 `isolation_level` 隐式事务跨线程交错的情况。

**修复**：改为**线程本地连接 + 双检锁 + 先初始化后发布**，并把写操作统一走带重试的封装。

```python
import threading, sqlite3, time
from pathlib import Path

_local = threading.local()
_lock = threading.RLock()

def _new_conn() -> sqlite3.Connection:
    p = Path(get_settings().data_dir) / "butler.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=15.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn

def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    with _lock:                       # 双检：避免并发各建一条
        conn = getattr(_local, "conn", None)
        if conn is not None:
            return conn
        conn = _new_conn()
        _init(conn)                   # ★ 先跑完建表/迁移，再对外可见
        _local.conn = conn
        return conn

def write(sql: str, params=(), retries: int = 5):
    """所有 INSERT/UPDATE/DELETE 走这里，遇 SQLITE_BUSY 指数退避重试。"""
    last = None
    for i in range(retries):
        try:
            c = get_conn()
            cur = c.execute(sql, params)
            c.commit()
            return cur
        except sqlite3.OperationalError as e:
            last = e
            if "locked" not in str(e) and "busy" not in str(e):
                raise
            time.sleep(min(0.05 * (2 ** i), 0.5))
    raise last
```

**配套**：`close()` 要遍历并关闭所有线程本地连接（可用 `WeakSet` 登记），否则线程退出后连接不释放。

---

### P0-3 配置读取接口在 `config.json` 损坏时全站 500，且无法自愈

**位置**：`butler/api/config_routes.py:23`

**现象**：`_load_cfg` 的 `except` 分支调用 `logger.error(...)`，但该模块**从未导入/定义 `logger`**。当 `config.json` 解析失败（断电写半、手工编辑错、磁盘满）时，本应"记日志 + 返回空配置降级"，实际抛 `NameError` → 所有配置读取接口 500 → WebUI「人格/设置」页打不开 → **无法用 WebUI 修回配置**，只能 SSH 上手改文件。

**影响**：一个可降级的小故障被放大成"配置面全瘫 + 无自愈路径"。

**修复**：

```python
from butler.logging_setup import get_logger
logger = get_logger("butler.api.config")

def _load_cfg() -> dict:
    try:
        return json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("config.json parse failed, returning defaults: %s", e)
        # ★ 自愈：把损坏文件挪走，让 WebUI 能重新写入一份干净的
        try:
            bad = _CFG_PATH.with_suffix(f".bad.{int(time.time())}")
            _CFG_PATH.replace(bad)
            logger.warning("corrupted config moved to %s", bad)
        except Exception:
            logger.exception("failed to quarantine corrupted config")
        return {}
```

**加固**：把 `F821`（未定义名称）在项目门禁中从告警提升为 **error**，杜绝此类"except 里再抛异常"的写法再次入库。

---

### P0-4 TTS 过载保护触发后，清空整个队列并静默 600 秒

**位置**：`butler/tts/queue.py`（入队路径的 `overload_threshold` 分支）

**现象**：入队后队列长度 `>= threshold`（默认 20）时，代码执行"**清空全部 + 暂停 600s + 播报过载通知**"，并且**当前这条消息自身也被丢弃**（返回 `REASON_OVERLOAD_RESET`）。

**问题**：

1. 清空是**无差别**的——P1 告警（漏水/门未关/老人跌倒）与 P3 闲聊一起被丢；
2. 过载通知本身也要入队播出，若它在清空之后入队，同样会被后续逻辑影响；
3. 600 秒静默对"家庭管家"是灾难级——一次设备异常刷屏即可让管家失声 10 分钟，且期间**没有任何日志级别的外部可见告警**（Bark 兜底未接）。

**修复**：改为**按优先级裁剪 + 短暂停 + 保底告警**，且当前消息不丢：

```python
if len(self._q) >= self.overload_threshold:
    dropped = [it for it in self._q if it.priority >= 3]
    self._q = [it for it in self._q if it.priority <= 2]   # ★ 只保 P1/P2
    self._pause_until = time.time() + min(self.overload_pause_s, 60.0)
    logger.error("TTS_OVERLOAD kept=%d dropped=%d pause=%ss",
                 len(self._q), len(dropped), self.overload_pause_s)
    if self.on_overload:
        self._safe_callback(self.on_overload, len(self._q), len(dropped))
    # 当前消息：P1/P2 照常入队；P3+ 才丢弃
    if cur.priority >= 3:
        return False, REASON_OVERLOAD_RESET
    return True, REASON_QUEUED
```

**加固**：过载事件必须走 Bark 文字推送兜底（ currently 只有 TTS 播报），否则"过载"这件事本身也说不出口。建议阈值 `BUTLER_TTS_OVERLOAD_THRESHOLD` 默认提到 50，暂停默认 60s。

---

## 2. P1 严重缺陷

### P1-1 技能描述 pending 过期后，误把普通对话当技能描述喂生成器

**位置**：`butler/core/dialog.py:358-377`（缩进层级错误）

**现象**：`creator = getattr(rt, "skill_creator", None)`（358 行，缩进 16）写在 `else:` 分支内，而 `if creator:`（359 行，缩进 12）却落在 `if _pending_exp:` 块内、与 `if time.time() > _pending_exp:` **平级**。

后果：当 pending **已过期**时，代码执行完 `del self._pending_skill_desc[role_id]` 后**没有 return**，继续落入 `if creator:` 分支（`creator` 在 261 行已绑定），把用户当前这句话（例如"今天天气怎么样"）当成技能描述喂给生成器 → 凭空生成一个垃圾技能草稿并反问"要创建吗？"，**正常 LLM 对话被吞掉**。

代码注释明确写着"过期：清除，走正常流程"，实现与意图不符。

**修复**：过期分支显式禁用生成器，并把 `creator` 赋值收进 `else`：

```python
if _pending_exp:
    if time.time() > _pending_exp:
        del self._pending_skill_desc[role_id]
        logger.info("skill create pending expired for role=%s", role_id)
        creator = None                     # ★ 过期即不进生成器
    else:
        ...  # 取消词 / 二次触发
        creator = getattr(rt, "skill_creator", None)
    if creator:
        ...
```

---

### P1-2 TTS 熔断对"成功出队"计数，正常连续对话 7 条即静默 25 秒

**位置**：`butler/tts/queue.py`（`_note_trigger("playback")` 调用点、熔断默认阈值）

**现象**：`dequeue` 中**每次出队都调用 `_note_trigger("playback")`**，无论播放成功还是失败。默认阈值 7 次 / 7 秒窗口 → 冷却 25 秒，冷却期内**非 P1 消息一律不播**（消息保留但不发声）。

**影响**：一次正常的多轮对话（问天气 → 追问 → 再问设备 → 播报 3 条通知）在 7 秒内累计 7 次出队即触发熔断，之后 25 秒内管家**对家人说话不回应**。这是把"防故障"做成了"制造故障"——熔断本该只统计**播放失败**。

**修复**：

```python
# play_one 内：只在真正失败时计数
try:
    await speaker(...)
    self._breaker.record_success()      # ★ 成功要清零连续失败计数
except Exception as e:
    self._breaker.record_failure()      # ★ 仅失败计入熔断
    logger.warning("TTS_PLAY_FAILED ...")
```

并把默认参数调整为「5 次失败 / 30 秒窗口 / 冷却 20 秒」，冷却期放行 P1 与 P2（只丢弃 P3+ 闲聊），保证告警与主动关怀不受影响。

---

### P1-3 MQTT 队列满时事件静默丢失，`except QueueFull` 根本捕获不到

**位置**：`butler/bus/mqtt_client.py`（`_on_message`）

**现象**：`_on_message` 运行在 **paho 网络线程**，通过 `loop.call_soon_threadsafe(...)` 把事件投递到 `asyncio.Queue(maxsize=2000)`。队列满时，`put_nowait` 抛出的 `QueueFull` 发生在**事件循环回调里**，网络线程的 `except asyncio.QueueFull` 永远捕获不到 → 事件被静默丢弃，只在日志里留下一行 `Exception in callback`，且无计数、无告警。

**影响**：TV 人脸识别 / VLM 场景事件洪峰时，感知事件成片丢失，表现为"管家有时认不出人"，且事后无法定位。

**修复**：把异常捕获放进回调内部，并把丢件做成**可观测计数**：

```python
def _submit(evt):
    try:
        q.put_nowait(evt)
    except asyncio.QueueFull:
        self.dropped += 1
        if self.dropped % 100 == 1 or self.dropped < 5:
            logger.error("MQTT_QUEUE_FULL dropped=%d (consumer lagging)", self.dropped)

def _on_message(client, userdata, msg):
    ...
    self._loop.call_soon_threadsafe(_submit, evt)   # 危险操作全部在回调内 try
```

**加固**：把 `dropped` 暴露到 `/api/health` 的 `details` 里；消费者侧增加"批量 drain"（一次取 N 条）以降低滞后概率。

---

### P1-4 16 个定时任务异常全部静默：`Future.result()` 从不调用

**位置**：`butler/app.py`（`TriggerRegistry.wrap_scheduler_job`）、`butler/scheduler/*`

**现象**：scheduler 在**独立线程**运行，通过 `asyncio.run_coroutine_threadsafe` 把协程投递到主事件循环，但返回的 `concurrent.futures.Future` **从未调用 `.result()` / `.exception()`**。

**影响**：定时任务的异常被 Future 吞掉（Python 只在 GC 时打印一句 `Future exception was never retrieved`，无 traceback、无业务日志）。凌晨 3:15 的记忆抽取、04:10 TTS 清理、07:20 早报、21:30 晚报、异常检测、设备巡检——**全部失败无声**。这是本项目"看起来在跑其实早死了"的最大来源。

**修复**：

```python
def wrap_scheduler_job(fn):
    def _runner(*a, **kw):
        fut = asyncio.run_coroutine_threadsafe(fn(*a, **kw), _MAIN_LOOP)
        def _done(f):
            try:
                f.result()                 # ★ 主动取回异常
            except Exception:
                logger.exception("SCHED_JOB_FAILED job=%s", getattr(fn, "__name__", fn))
        fut.add_done_callback(_done)
    return _runner
```

**加固**：定时任务执行结果落 `trigger_runs` / `schedules` 表（表结构已存在 `last_status` 语义），WebUI 上可见"上次成功时间"。

---

### P1-5 后台轮询任务在停机时未取消，且循环无退出条件

**位置**：`butler/app.py`（`_presence_poll_loop`）

**现象**：`while True` 每 10 秒轮询 HA 更新用户位置；`shutdown` 中只 cancel 了 consumer、停了 MQTT，**未 cancel `rt._presence_poll_task`**。

**影响**：容器停止/重启时打印 `Task was destroyed but it is pending!`；在 `docker compose restart` 场景下，旧任务的 HA 请求可能跨越停机窗口，写入已关闭的 DB 连接。同时循环内无 `stop_event`，无法优雅退出。

**修复**：

```python
async def _presence_poll_loop(rt):
    stop = rt._stop_event                     # 与 app 生命周期共用一个 Event
    while not stop.is_set():
        try:
            ...
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("presence poll failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            pass
```

```python
# shutdown 中，在 db.close() 之前
for t in (getattr(rt, "_presence_poll_task", None), getattr(rt, "_presence_ha_task", None)):
    if t:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.gather(t, return_exceptions=True), timeout=3.0)
```

---

### P1-6 两套 TTS 队列并存，5 条旁路绕过静默期/去重/优先级/过载保护

**位置**：`butler/tts/queue.py`（`TTSQueue`，经 `enqueue_tts`）vs `butler/tts/playback_queue.py`（`PlaybackQueue`，经 `TTSManager.speak`）；旁路调用点：`proactive/engine`、`timeseries/anomaly`、`af_bridge`、`morning/routine`、`notifier/router`（`via="direct"`）

**现象**：`dialog.speak` 走 `TTSQueue`（有优先级、去重、夜间静默、TTL、过载保护、熔断），而 5 处业务代码直接 `manager.speak(..., via="direct")` 走 `PlaybackQueue`（只做设备串行播放）。

**影响**：

- **夜间静默被绕过** → 凌晨 2 点异常检测/主动关怀直接出声；
- **bigram 去重被绕过** → 同一句关怀在一天内反复播；
- **过载/熔断被绕过** → 队列已熔断时旁路仍在播，保护形同虚设；
- 两个队列各自持播放锁，**可能同时向同一设备播出**，声音叠加。

**修复**：砍掉旁路，统一出口。`PlaybackQueue` 降级为 `TTSQueue` 的**设备级串行执行器**（只负责"同一设备不并发"），不再作为独立入口：

```python
# tts/adapter.py —— 唯一对外出口
async def speak(text, *, priority=3, room="", member="", override_quiet=False,
                trace_id="", device_ids=None, dedupe_key=""):
    return await get_tts_queue().enqueue(
        text, priority=priority, room=room, member=member,
        override_quiet=override_quiet, trace_id=trace_id,
        device_ids=device_ids, dedupe_key=dedupe_key)
```

把 5 处 `via="direct"` 改为调用 `adapter.speak(...)` 并带上合理 priority（异常检测=2、主动关怀=3、早报=2）。**保留一个逃生开关** `BUTLER_TTS_ALLOW_DIRECT=0`，默认关闭并加断言日志。

---

### P1-7 事件循环被同步 SQLite 阻塞（93 处调用点，仅 1 处做了线程包裹）

**位置**：`butler/store/repo.py` 全部方法（文件头注释自称"调用方在 async 上下文中用 `run_in_threadpool` 包裹"）、`butler/api/memory_routes.py:433` 等

**实测**：`repo.*` / `store.repo` 引用 **93 处**，`run_in_threadpool` 仅 **1 处**，`asyncio.to_thread` 49 处（多为其他用途）。典型：

```python
# butler/api/memory_routes.py:433 —— async 路由里直接同步查 1000 行
all_facts = repo.list_facts(status="approved", limit=1000)
```

**影响**：单条慢查询（WAL checkpoint、`COUNT(*)` 全表扫描、磁盘 IO 抖动）即可阻塞整个事件循环 → MQTT 心跳延迟、SSE 断流、`/api/health` 超时 → 容器被健康check判定为不健康而重启。在 NAS 的机械盘/低端 SSD 上尤其明显。

**修复**（两步走）：

1. **短期**：所有 `async` 函数内的 `repo.*` 调用包 `await asyncio.to_thread(...)`，优先修 API 路由与 `limit >= 500` 的查询；给 `list_facts(limit=1000)` 加索引覆盖与分页上限。
2. **中期**：在 `store/` 增一层 async 门面（保持 repo 同步实现不变）：

```python
# butler/store/async_repo.py
async def list_facts(**kw):
    return await asyncio.to_thread(repo.list_facts, **kw)
```

门禁侧：把 `ASYNC230`（async 内阻塞 IO）纳入 critical 规则，禁止新增。

---

### P1-8 播报时长估算两处不一致，导致尾部被截断

**位置**：`butler/tts/queue.py`（0.2s/字 + 2s 缓冲 + 全屋 3s）vs `butler/core/dialog.py:_est_speak_secs`（0.32s/字）

**现象**：同一句话，队列估算 3.2s（10 字），dialog 估算 3.2s… 但对长句分歧放大：30 字 → queue 8s / dialog 9.6s。队列估算**偏短**且被用于 `media_stop` 轮询阈值时，会在音频尚未播完时下发停止/解锁，表现为**长句后半截被吞**。

**修复**：抽公共函数，取**保守偏大**值，并加实测校准：

```python
# butler/tts/util.py
def est_speak_secs(text: str, *, whole_home: bool = False) -> float:
    base = max(2.0, len(text) * 0.34)      # 中文 TTS 实测 ~0.33s/字
    return base + (3.0 if whole_home else 2.0)
```

`queue.py` 与 `dialog.py` 统一引用；在 `/api/tts/test` 增加"实际时长 vs 估算时长"埋点，两周后据实测回归系数。

---

### P1-9 登录无速率限制，SSE 订阅者队列满时静默丢弃

**位置**：`butler/api/*`（`_login`）、`butler/app.py`（SSE 广播）

**现象**：`_login` 无失败计数 / 退避 / 锁定；SSE 广播在订阅者队列满时直接丢弃事件，无计数、不断开慢订阅者。

**影响**：口令可被暴力枚举（WebUI 暴露在 8095 端口，若做了端口转发即暴露到公网）；SSE 丢事件表现为"WebUI 偶尔不刷新"，且慢客户端会持续拖累广播循环。

**修复**：

```python
_LOGIN_FAIL: dict[str, tuple[int, float]] = {}

def _login_guard(ip: str) -> bool:
    n, ts = _LOGIN_FAIL.get(ip, (0, 0.0))
    now = time.time()
    if now - ts > 900:                 # 15 分钟窗口外重置
        n, ts = 0, now
    if n >= 5:
        return False                   # 锁定，返回 429
    _LOGIN_FAIL[ip] = (n, ts)
    return True
```

SSE 侧：丢弃时 `logger.warning` + 计数，连续丢弃 >10 次则主动 `cancel` 该订阅者并让其重连。

---

### P1-10 MQTT 连接等待失败后仍继续启动，启动期事件全丢

**位置**：`butler/bus/mqtt_client.py`（`wait_connected(15)`）

**现象**：`wait_connected` 超时后仅记录日志便继续装配后续模块；此时订阅尚未注册，TV 人脸事件、小爱语音事件在**启动后到重连成功之间**全部丢失，且没有任何"降级运行"标志。

**修复**：

```python
ok = mqtt.wait_connected(timeout=15)
if not ok:
    rt.degraded = True                              # ★ 显式降级标志
    logger.error("MQTT not connected at startup; running DEGRADED")
    bark_alert("管家启动降级：MQTT 未连通")           # 外部可见
```

并把 `degraded` 暴露到 `/api/health`：`{"ok": true, "degraded": true, "reasons": ["mqtt"]}`（`/api/health` 本身仍返回 200，符合现有"不因下游不可用而失败"的设计）。重连成功后清除标志并补发一次状态同步。

---

## 3. P2 中等缺陷

### P2-1 `ha.py` 存在整段死代码（写在 `return` 之后），静态检查报 `message` 未定义

**位置**：`butler/integrations/ha.py:308-331`

**现象**：一段完整的 `notify_message` 旧实现被写在 `intelligent_speaker` 的 `return f"error: {e}"` **之后**，成为永不可达的死代码；其中引用的 `message` 在该作用域内不存在（F821）。真正的实现在 `ha.py:333-349`（`notify_message` → `_notify_message_inner`），功能正常。

**影响**：运行期无直接故障，但（a）误导维护者——以为有 notify 平台降级，实际没有；（b）静态检查噪声；（c）该死代码与真实现逻辑不一致，一旦有人"恢复"它会引入真实回归。

**修复**：删除 308-331 行整段死代码；用 `ruff --select F` 全量清零后再入库。

---

### P2-2 `docker_tools.py` 引用未导入的 `time`（死代码残留）

**位置**：`butler/integrations/docker_tools.py:108`

```python
time.sleep(2) if False else None  # 不 sleep，让调用方观察
```

`time` 未在文件顶部导入（仅 `asyncio` / `subprocess`）。因条件表达式惰性求值，运行期不会崩，但任何重构（把 `if False` 改成 `if True`）都会立刻炸。

**修复**：删除该行；若确需 sleep，改为 `await asyncio.sleep(2)` 并补 `import asyncio`（已有）。

---

### P2-3 `repo.py` 注解引用未导入的 `sqlite3`

**位置**：`butler/store/repo.py:145`（`def _row_to_dict(r: sqlite3.Row) -> dict:`）

因文件头有 `from __future__ import annotations`，注解不求值，运行期安全。但类型检查 / 文档生成会失败，且掩盖真实意图。

**修复**：`import sqlite3` 补充到顶部导入区（零风险）。

---

### P2-4 收件箱：台账漏记 + 预算"先扣后播"

**位置**：`butler/bus/inbox.py:241-285`

- `handle()` 中 `outcome, reason = await self._dispatch(...)` **无 try**：`_dispatch` 抛异常时 `_record` 不执行 → 该文件头郑重声明的"一次投件恰好一行 `inbox_events`"承诺失效；
- `budget_consume()` 在 `enqueue_tts` 成功入队后立即扣预算，但消息可能在队列里因**静默期 / TTL / 过载**被丢弃 → 预算被扣而**从未播出**，当日报备 3 次额度白白浪费。

**修复**：

```python
async def handle(self, topic, payload):
    try:
        outcome, reason = await self._dispatch(topic, payload)
    except Exception as e:                     # ★ 兜底记账
        outcome, reason = OUTCOME_FAILED, f"dispatch 异常 {type(e).__name__}"
        logger.exception("INBOX_DISPATCH_FAILED topic=%s", topic)
    self._record(topic, payload, outcome, reason)
```

预算改为**播出确认后扣减**：由 TTSQueue 在 `play_one` 成功后回调 `inbox.budget_consume(source)`；或在 `enqueue` 返回时携带"预计播出时间"，由队列侧在真正播出时结算。

---

### P2-5 `notify_message` 无降级链：小爱直读失败即彻底失败

**位置**：`butler/integrations/ha.py:337-349`

`_notify_message_inner` 只调用 `intelligent_speaker`，返回非 `ok` 前缀就直接返回错误字符串。调用方（`dialog.py:617/621`、`action_router.py:80`、`skills/runner.py:322/362`、`tools/registry.py:1136`）多数只记日志 → **小爱播报静默失败**，与项目"TTS 三级降级"的设计承诺不符。

**修复**：补齐降级——`intelligent_speaker` → HA notify 平台 → 电视弹窗 → Bark 文字推送；返回结构带上实际生效通道，供调用方判断是否要进一步兜底。

---

### P2-6 `role` 解析结果未做空值校验

**位置**：`butler/core/dialog.py:300-302` 及后续

```python
role = rt.roles.get(role_id) if role_id else None
```

若 `role_id` 传入了一个已被删除/禁用的角色，`role` 为 `None`，后续 `role.id`（如 `dialog.py:375` 的返回体）抛 `AttributeError`。

**修复**：解析后立即校验并回退：

```python
role = rt.roles.get(role_id) if role_id else None
if role is None:
    role_id, role = "butler", rt.roles.get("butler")
    logger.warning("role %s not found/disabled, fallback to butler", role_id)
if role is None:
    return {"ok": False, "error": "no_role_available"}
```

---

### P2-7 动态 SQL 使用 f-string 拼接列名/子句

**位置**：`butler/store/command_store.py:229`、`butler/store/db.py:490`、`butler/store/repo.py:554`、`butler/store/task_store.py:155`、`butler/tools/schedule.py:199`

形如 `UPDATE memory_facts SET {', '.join(sets)} WHERE id=?`。`sets` 的键若来自外部输入即构成注入面；`db.py:490` 的 `ALTER TABLE ... ADD COLUMN {col} {ddl}` 同理。

**修复**：列名一律走**白名单映射**（`ALLOWED_COLS = {...}`），非白名单直接 `ValueError`；`db.py` 的迁移用固定常量表而非拼接。值一律走 `?` 占位（现状已做到，保持）。

---

### P2-8 自进化分析被调度两次

**位置**：`butler/app.py:935-965`（daemon 线程 `evolution-scheduler`，每日 03:00）+ scheduler 中的 `self_evolve_daily`（03:45）

**影响**：同一份分析跑两遍，重复写 `self_evolution` 状态与建议，且两条路径的错误处理不一致（线程版只 `logger.error`，APScheduler 版走 P1-4 的静默 Future）。同时 daemon 线程无停止条件。

**修复**：删除 daemon 线程版，统一走 APScheduler；若保留，至少加 `rt._stop_event` 退出条件。

---

### P2-9 异常吞噬面过大：599 处盲捕 + 47 处 `except: pass`

**位置**：全仓（项目基线 `.gates-baseline.txt` 已登记 7 处 `except-pass-broad` 与 8 处 `fake-ok-const`，说明团队已知）

**风险**：本次审计发现的 P0-1、P0-3 之所以长期潜伏，正是因为异常被兜底成"不响"而不是"报错"。`fake-ok-const`（永远返回 `{"ok": True}` 的桩）更会让调用方误判成功。

**修复（制度化）**：

1. 关键路径（`critical_globs` 已列的 guard / auth / api / core/dialog / tts / morning / proactive / timeseries / scheduler）**禁止** `except Exception: pass`，必须 `logger.exception` 或返回带 `error` 字段的结构；
2. 引入 `butler/errors.py` 定义 `ButlerError` 分层，兜底只捕获非 `ButlerError`；
3. 基线**只准减少不准新增**（现有机制已具备，坚持执行即可）；
4. `dialog.py` 中 3 处 `fake-ok-const`（`on_wakeup` / `_quick_reply` / `speak`）优先清理——它们正是"管家没反应但日志一切正常"的元凶。

---

### P2-10 MQTT `on_result` 同步回调在网络线程执行

**位置**：`butler/bus/mqtt_client.py`（zap-tv 回执处理）

回执回调运行在 paho 网络线程，若其中含阻塞 HTTP / 同步 IO，会**阻塞 MQTT 心跳与收包**，触发假掉线重连。

**修复**：回执处理改为只做"入队"，业务处理投递到事件循环；网络线程内禁止任何 IO 与锁等待。

---

## 4. 系统性风险专题

### 4.1 容错设计反而掩盖故障（最高优先级）

本项目的三级 TTS 降级、全局异常兜底、过载保护、熔断——每一个都是好设计，但组合起来形成了**故障隐身机制**：

| 设计 | 初衷 | 实际效果 |
|---|---|---|
| 全局异常兜底 | 绝不崩溃退出 | 把 `NameError`（P0-1/P0-3）伪装成"没反应" |
| 过载保护清空队列 | 防刷屏 | 静默 600s，告警一起被丢（P0-4） |
| 熔断 | 防故障扩散 | 对成功出队计数，正常对话即静默（P1-2） |
| `fake-ok-const` | 兼容桩 | 调用方永远以为成功（P2-9） |

**建议**：把"兜底"改成"**兜底 + 响**"——任何兜底分支必须三选一：写 `logger.exception`、发 Bark 告警、或在 `/api/health.details` 累加计数。项目基线里的 `record_errors` 计数（inbox）是正确范式，应推广到全仓。

### 4.2 并发模型：三条线程同时碰同一份状态

- paho **网络线程**：写 `asyncio.Queue`（P1-3 捕获失效）、执行 `on_result` 回调（P2-10）
- **scheduler 线程**：投递协程到主循环，异常静默（P1-4）
- **主事件循环**：同步 SQLite 阻塞（P1-7）、`_presence_poll_task` 未取消（P1-5）

共享的 SQLite 单连接（P0-2）是这三条线的交汇点，也是最容易出事的部位。**建议**：明确"SQLite 只由事件循环侧访问"，网络线程与 scheduler 线程一律通过 `loop.call_soon_threadsafe` 投递；DB 层用线程本地连接兜底（P0-2 方案）。

### 4.3 降级链不完整对照表

| 场景 | 声称 | 实际 |
|---|---|---|
| TTS 主引擎失败 | edge-tts → Kokoro → Bark | ✅ 成立（manager 内） |
| 小爱播报失败 | 三级降级 | ❌ 无降级（P2-5） |
| 队列过载 | 过载通知 | ❌ 通知自身可能被丢（P0-4） |
| MQTT 断连 | 离线感知 | ⚠️ 无降级标志，启动期丢事件（P1-10） |
| 主动播报 | 统一队列 | ❌ 5 处旁路绕过全部保护（P1-6） |

---

## 5. 修复路线图

### 第一批 · 24 小时内（止血，均为小改动）

| # | 缺陷 | 改动量 | 风险 |
|---|---|---|---|
| 1 | P0-1 技能确认 `role` 未绑定 | +1 行 | 极低 |
| 2 | P0-3 `logger` 未定义 + 损坏配置隔离 | +5 行 | 极低 |
| 3 | P1-1 过期 pending 误入生成器 | +2 行 | 低 |
| 4 | P2-1/P2-2/P2-3 删死代码 + 补导入 | -25 行 | 极低 |
| 5 | P1-4 scheduler 异常回调 | +6 行 | 低 |
| 6 | P1-9 登录限流 | +12 行 | 低 |

### 第二批 · 1 周内（结构与参数）

7. P0-2 SQLite 线程本地连接 + 写重试封装
8. P0-4 过载按优先级裁剪 + 暂停降到 60s + Bark 兜底
9. P1-2 熔断只计失败 + 参数重调
10. P1-3 MQTT 丢件计数 + 回调内 try
11. P1-5 后台任务取消与退出条件
12. P1-8 时长估算统一

### 第三批 · 2-4 周（架构收敛）

13. P1-6 统一 TTS 出口，收编 5 条旁路
14. P1-7 同步 DB 调用线程化（先 API 层，再全量）
15. P2-9 异常纪律制度化（`errors.py` + 关键路径禁 `pass` + 基线守门）
16. P2-5 小爱播报降级链补齐

---

## 6. 回归验证清单

**新增单元测试**（放 `tests/`，可直接作为门禁用例）：

```python
# test_dialog_skill_draft.py —— 覆盖 P0-1 / P1-1
async def test_confirm_skill_does_not_raise_UnboundLocalError(dm, rt_with_pending_draft):
    res = await dm.on_wakeup("kevin", "书房", "确认", member="Kevin")
    assert res["ok"] is True          # 修复前：UnboundLocalError

async def test_expired_pending_goes_normal_dialog(dm, rt):
    dm._pending_skill_desc["kevin"] = time.time() - 1      # 已过期
    res = await dm.on_wakeup("kevin", "书房", "今天天气怎么样", member="Kevin")
    assert "skill_create" not in res                        # 修复前：误生成技能草稿


# test_config_routes.py —— 覆盖 P0-3
def test_corrupt_config_returns_default_and_quarantines(tmp_path, monkeypatch):
    write_corrupt_json(cfg_path)
    assert _load_cfg() == {}
    assert any(cfg_path.parent.glob("config.bad.*"))


# test_tts_queue.py —— 覆盖 P0-4 / P1-2
async def test_overload_keeps_alerts(q):
    enqueue(q, "漏水了", priority=1)
    flood(q, 30, priority=3)
    assert q.has(p1_text := "漏水了")        # 修复前：被一并清空

async def test_breaker_ignores_successful_playback(q, speaker_ok):
    for _ in range(10):
        await q.play_one()
    assert not q.breaker.is_open            # 修复前：7 次成功即熔断
```

**门禁强化建议**（写入 `.gates.toml` / CI）：

1. `F821`（未定义名称）、`E722`（裸 except）设为 **error**，零容忍——本次 4 处 F821 全部是真实缺陷信号；
2. `ASYNC230`（async 内阻塞 IO）纳入 critical，禁止新增；
3. 基线 `.gates-baseline.txt` 保持"只减不增"，并把 `dialog.py` 的 3 处 `fake-ok-const` 排入本期清零；
4. `/api/health` 增加 `details`：MQTT 丢件计数、DB 写失败计数、调度任务最近成功时间、TTS 熔断/过载状态、degraded 原因——**让"不响"变成看得见**。

**上线前冒烟**（NAS 实机）：

```bash
curl -s http://192.168.2.200:8095/api/health | jq .details   # degraded=false、dropped=0
# 1) 连续对话 10 轮：第 8 轮仍须出声（验证 P1-2）
# 2) 语音建技能 → 说"确认"：须返回"技能已创建"（验证 P0-1）
# 3) 断开 MQTT 30s 后重连：health 应出现 degraded=true → false（验证 P1-10）
# 4) 凌晨静默窗内触发异常检测：不得出声（验证 P1-6）
```

---

## 附：审计覆盖与未覆盖说明

**已逐行精读**：`app.py`(1027)、`store/db.py`、`bus/mqtt_client.py`、`bus/inbox.py`(324)、`core/dialog.py`(全文)、`tts/queue.py`(569)、`tts/manager.py`(259)、`integrations/ha.py`(核心段)、`store/repo.py`(核心段)

**模式扫描覆盖**：全仓 220 个 Python 文件（async 阻塞、裸 except、动态 SQL、未用导入、线程/任务生命周期）

**未深入**（建议后续专项）：`api/skill_routes.py`(1061)、`api/doubao_webhook.py`(849)、`core/cron_task.py`(727)、`integrations/memory_agent.py`(504)、`integrations/llm.py`(291)、`api/deps.py`(349) 的完整业务逻辑正确性；以及前端 `static/` 与 Node-RED 侧的联动契约。这些文件的**结构性风险**（并发、阻塞、异常吞噬）已在本次模式扫描中覆盖，但业务语义层面的缺陷需专项审计。
