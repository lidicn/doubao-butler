# doubao-butler 只读审计报告 — 功能性与稳定性

| 项 | 值 |
|---|---|
| 审计对象 | `E:\NAS\doubao-butler`（工作区快照） |
| 审计日期 | 2026-10-02 |
| 审计类型 | **只读**（未修改任何文件，未启动服务，未连接外部依赖） |
| 范围 | 功能性 bug 与稳定性缺陷（**不**覆盖安全/凭据/容器加固，见文末声明） |
| 方法 | 静态通读 + grep 交叉验证 + 分区子代理复核；关键结论均给出可复验的 文件:行号 证据 |
| 总体结论 | **2 个 P0（启动即崩）+ 多个 P1/P2（核心链路失效、数据不一致、事件循环挂起）** |

> 说明：本目录已存在多份历史审计报告（第二轮/第三轮/第四轮），针对不同代码快照。本报告针对**当前工作区树**独立成篇，不重复历史报告，仅聚焦**本树上仍存在的、可复验的功能/稳定性问题**。

---

## 0. 十句话结论

1. **`butler/tts/nowvoice_client.py` 缺失** ⇒ `import butler.app` 在 import 期直接 `ModuleNotFoundError`，服务**无法启动**（`app.py:85 → manager.py:18 → nowvoice_tts.py:13`）。这是本树最严重的硬失败。
2. **`config.py:36` `_env_bool` 恒返回 False**（`return False if v == "" else False`）——任何受其影响的布尔配置项永远取不到 `True`（`TTS_CACHE_ENABLED`、`ILINK_ENABLED` 等）。这是无歧义的逻辑缺陷。
3. **`dialog.py:389` 引用不存在的 `self.rt`** ⇒ 简单命令设备控制路径必抛 `AttributeError`，被 except 吞掉后回复"出了点问题"——非小爱来源的设备控制**永久失败**。
4. **`PushGuard.check_tts` 入队后 `tts_pop()` 全仓 0 调用者** ⇒ TTS 队列只进不出，累计 10 条触发 5 分钟过载闩锁，且闩锁每次调用自续期 300s——**TTS 播报周期性静默失败**。
5. **cron 定时任务 100% 不执行**：`cron_task.py:674-676` 的 `sync_job` 在 APScheduler 线程池调 `asyncio.get_event_loop()`，非主线程无 loop ⇒ 必然 `RuntimeError`，只留一行 apscheduler 日志。
6. **决策层心跳 job 被 `next_run_time=None` 钉死**（`app.py:567`）⇒ 依赖 heartbeat 的 trigger 规则链静默失效；且 `reg.register` 已登记，产生一条**永远修不好的假告警**。
7. **`get_snapshot()` 方法不存在**（实为 `snapshot()`），6 处调用（自动模式/主动关怀/早安/异常检测）全部命中 `except Exception: logger.debug` ⇒ 定位条件恒为 False，**自动功能永不生效**且日志不可见。
8. **SQLite 初始化顺序颠倒 + 全树 0 处 rollback**：9 处 `_conn` 先缓存后 `_init`，`_init` 抛错则坏连接被永久缓存；所有 INSERT+commit 后无 rollback，异常时事务悬着把写锁带给下一个使用者。
9. **TTS 降级链中 `tts_pop` 无消费方** 与 **`bark.push` 未 await（`doubao_webhook.py:425`、`quarantine.py:176`）** 属"实现了但没接线"——与历史报告的元观察一致：**代码写对了 ≠ 接上了线**。
10. **无 TZ、三套时间语义并存**：容器无 `TZ`（UTC），但代码里显式 Asia/Shanghai、朴素 `datetime.now()`、硬编码 `+8` 混用 ⇒ "晨起/夜间窗口"等条件偏移 8 小时。

---

## 1. P0 — 服务无法启动 / 核心功能 100% 失效

### P0-1 `nowvoice_client.py` 缺失 ⇒ `import butler.app` 直接失败

- **证据**：`butler/tts/` 目录仅含 `__init__.py base.py edge_tts_patch.py edge_tts.py kokoro.py manager.py nowvoice_tts.py`；`nowvoice_client.py` 不存在。`nowvoice_tts.py:13` 顶层 `from butler.tts.nowvoice_client import NowVoiceClient`。
- **断裂链**（全为顶层 import，无 try 保护）：
  ```
  butler/app.py:85    from butler.tts.manager import TTSManager
    └ butler/tts/manager.py:18  from butler.tts.nowvoice_tts import NowVoiceTTS
        └ butler/tts/nowvoice_tts.py:13 from butler.tts.nowvoice_client import NowVoiceClient  ← 文件不存在
  ```
- **取证**：`class NowVoiceClient` 全树唯一命中 `_wt2\nv_client.py:113`（根级临时目录，不可 import）；`__pycache__` 有 `nowvoice_tts.pyc` 但无 `nowvoice_client.pyc` ⇒ 该模块从未成功编译。
- **触发**：任何 `uvicorn butler.app:app`（Dockerfile:29）。`restart: unless-stopped` 下变成 **crash-loop**。
- **影响**：服务完全无法启动；import 期错误无业务提示。
- **修复**：把 `_wt2/nv_client.py` 的类迁入 `butler/tts/nowvoice_client.py` 并修正内部 import；或彻底移除 NowVoice 引擎。

### P0-2 `config.py:36` `_env_bool` 恒返回 False

- **证据**（`butler/config.py:31-37`）：
  ```python
  def _env_bool(key, default):
      v = os.environ.get(key, "").strip().lower()
      if v in ("1","true","yes","on"): return True
      if v in ("0","false","no","off",""): return False if v == "" else False   # ← 无论真假恒 False
      return default
  ```
  `return False if v == "" else False` 两个分支都返回 `False`。即当环境变量**存在**（值非空）时本应读值，却被强制判为 False。
- **影响**：所有经 `_env_bool` 读取的配置项恒为 False。grep 命中项含 `TTS_CACHE_ENABLED`（禁用 TTS 缓存）、`ILINK_ENABLED`（iLink 微信 bot 永不启用，`app.py:432-433` 判断恒 False）、`wake_phrase` 相关等。**默认值 0 之外的布尔开关全部失效**。
- **修复**：`return False`（当值匹配 false 词表时）。

---

## 2. P1 — 单链路永久失效 / 数据不一致 / 事件循环挂起

### P1-1 `dialog.py:389` 引用不存在的 `self.rt` ⇒ 简单命令设备控制永久失败

- **证据**：`DialogManager.__init__`（`dialog.py:41-69`）只赋值 `self.s/state/mqtt/llm/tts/tv/ha/bark/memory/persona/dedup/wakeup/agent`，**从不设置 `self.rt`**。但 `dialog.py:389-390` 在简单命令 `call_service` 分支（非 xiaoai 来源）写 `if self.rt.ha:` / `await self.rt.ha.call_service(...)`。
- **触发**：用户对小爱之外的来源（TV 麦克风/手动）说"开灯/开空调"等，命中 `_DEVICE_ON_PATTERNS` 且 `source != "xiaoai"`。
- **影响**：`AttributeError: 'DialogManager' object has no attribute 'rt'` 被 `except Exception`（:394）吞掉 → 回复"嗯，刚才出了点问题" → **设备控制永不执行**，且无错误日志（仅 warning）。
- **修复**：`if self.rt.ha` → `if self.ha`；`self.rt.ha.call_service(...)` → `self.ha.call_service(...)`。

### P1-2 `PushGuard` TTS 队列只进不出 ⇒ 周期性 TTS 静默失败

- **证据**（`guard/push_guard.py`）：
  - `check_tts`（:242-332）把每次调用 append/insert 进 `_tts_queue`，`while len > TTS_QUEUE_MAX(=10)` 裁剪到 10。
  - `if len(self._tts_queue) >= TTS_QUEUE_MAX:`（:325-327）→ `self._tts_overload_until = now + TTS_OVERLOAD_PAUSE(=300)`。
  - `tts_pop()`（:334-340）**全仓 0 个调用者**（grep 唯一命中即定义处）；`reset()`（:381）全仓 0 个调用者。
  - `api/tts_routes.py:263-289` 只 `check_tts()` 后判 `drop`；:277-278 与 :288-289 调用 `pg.tts_unlock()`，但**只在成功路径**（无 finally），且 `tts_unlock()` 只设 `_tts_lock=False`，而 `_tts_lock` 只有 `tts_pop()` 会设 True ⇒ 恒为 no-op。
- **触发**：对 `POST /api/tts/speak` 第 10 次调用后。
- **影响**：队列停在 10 → 每次新调用 `:326` 把 `_tts_overload_until` 续到 `now+300` → **闩锁每 5 分钟自我续期永不到期** → 所有非 critical 播报 `{"spoken": false, "reason":"push_guard_drop"}` 而 HTTP 200（调用方以为成功）。早报/主动关怀/技能输出语音全部静默失败。
- **修复**：播报完成后调用 `tts_pop()`；`:325` 改按**时间窗内入队速率**判断；`reset()` 接运维端点。

### P1-3 cron 定时任务 100% 不执行（`sync_job` 线程池必然抛错）

- **证据**（`core/cron_task.py:670-689`）：
  ```python
  async def job_func():
      await self.execute_task(skill)
  def sync_job():                       # 普通函数
      loop = asyncio.get_event_loop()   # ← 灾难点
      asyncio.run_coroutine_threadsafe(job_func(), loop)
  self.scheduler.add_job(sync_job, "cron", ...)
  ```
  项目用 `AsyncIOScheduler`（app.py:517）⇒ executor 为 `AsyncIOExecutor`。`sync_job` 非协程 ⇒ 走 `run_in_executor`（默认线程池），线程池线程**没有事件循环** ⇒ `asyncio.get_event_loop()` 抛 `RuntimeError`，异常被 apscheduler 捕获仅一行日志。
- **影响**：**用户创建的 cron 技能一次都不跑**（产品主功能之一）。无 registry 记录、`/api/triggers/health` 不报错。
- **修复**：`sync_job` 改 `async def`；或复用 `app.py:518` 的 `reg.wrap_scheduler_job` + 保存 `_main_loop` 的 `run_coroutine_threadsafe` 模式。

### P1-4 决策层心跳 job 被 `next_run_time=None` 钉死

- **证据**（`app.py:559-568`）：`sched.add_job(_heartbeat_job, "interval", minutes=..., id="decision_heartbeat", next_run_time=None)`。
  apscheduler 的 `MemoryJobStore.get_due_jobs` 对 `next_run_time is None` 直接 `continue` ⇒ **永不被调度**。
- **影响**：`decision_cfg.enabled` 默认 True ⇒ heartbeat 事件**完全不发**，依赖它的 trigger 规则链静默失效；且 `reg.register("sched:decision_heartbeat", ..., expected_interval_sec=...)` 已登记 ⇒ `/api/triggers/health` 产生**永远修不好的假告警**（stale 恒 True）。
- **修复**：删掉 `next_run_time=None`；或用 `pause_job()` 实现"先不跑"。

### P1-5 `get_snapshot()` 不存在 ⇒ 6 处定位功能恒失效

- **证据**：`presence/__init__.py:139` 只有 `def snapshot(self)`，`get_snapshot` 全树**不存在**。但 6 处调用 `self.rt.presence_engine.get_snapshot()`：
  - `modes/auto_switch.py:139`（离家/观影/睡眠自动模式判定）
  - `morning/routine.py:93`（早安流程）
  - `proactive/engine.py:391, 407`（主动关怀场景条件）
  - `timeseries/anomaly.py:154, 282`（异常检测）
- **影响**：这些自动功能的定位条件恒为 False/空，且全部被 `except Exception: logger.debug(...)` 吞掉 ⇒ **正常日志级别完全不可见**。离家自动模式、主动关怀、早安、异常检测全部不生效。
- **修复**：`get_snapshot` → `snapshot`；把相关 `logger.debug` 提到 `warning`。

### P1-6 每日 03:45 自进化分析从未成功过（`analyze_candidates` 不存在）

- **证据**：`app.py:660-661` `from butler.core.self_evolve import analyze_candidates; result = analyze_candidates()`。而 `self_evolve.py` 只有 `def analyze_llm_traces`（:16），`analyze_candidates` 全树不存在。
- **影响**：每日 03:45 无条件触发的定时任务，`ImportError` 被 `app.py:663-664` 吞成一行 warning ⇒ **自进化每日分析自上线起一次都没跑过**。
- **修复**：改为调用 `analyze_llm_traces()`。

### P1-7 SQLite 初始化顺序颠倒 + 全树 0 rollback ⇒ 坏连接永久缓存 / 事务悬挂

- **证据**（9 处同模式）：`store/db.py:29-31`、`core/decision_store.py:28-30`、`core/notification_store.py:28-29`、`core/pwa_chat_store.py:25-26`、`core/user_location.py:35-36`、`ha_tools/whitelist.py:37-38`、`timeseries/store.py:30-31`、`proactive/engine.py:52-53`、`store/task_store.py:36-37`：
  ```python
  _conn = c       # 先缓存
  _init(c)        # 后初始化 —— 抛异常时坏连接已给所有人
  ```
- **影响**：`_init` 抛错（磁盘忙/损坏/并发 ALTER 撞 `duplicate column name`）时，模块级 `_conn` 已指向坏连接，下次 `get_conn()` 直接返回 → **该模块所有读写恒报 `no such table`，只能重启**。
- **叠加**：全树 `except Exception: ...` 后**无一处 `rollback()`**。Python `sqlite3` 默认 `isolation_level=""`，`execute(INSERT)` 隐式开事务，commit 前异常让事务悬着，共享 Connection 把写锁带给下一个使用者 ⇒ 后续同库写入 5s 后报 `database is locked`（如 `store/db.py`、`decision_store.py`、`event_stream.py`、`task_store.py` 等）。

### P1-8 触发引擎跨时基计算冷却 + 退避公式缺陷

- **证据**（`triggers/engine.py`）：
  - `:128` `_last_fired[cd_key] = fire_start`（`time.time()` 墙钟）；`:313` 同。
  - `:384` `now = time.monotonic()`（单调时钟）⇒ `status()` 用 `now - self._last_fired.get(...)` **跨时基相减**，`max(0.0,...)` 钳成 0 ⇒ API 显示"冷却 0s"但实际在冷却中。
  - `:318` `self._last_fired[cd_key] = fire_start - trig.get("cooldown_sec", 300) + 60`。当 `cooldown_sec>60`（默认 300）时 `fire_start - 240`，`now - last` 立刻 ≥240 ⇒ 退避没有"短于完整冷却"，实际仍按 ~240s 阻塞，与注释"短退避 60s"矛盾。
- **修复**：`status()` 的 `now` 改回 `time.time()`；退避公式改为 `fire_start - 60` 或单独用 `_backoff_until` 字段。

### P1-9 决策执行失败被记 `ok`

- **证据**（`decision/engine.py:158-167`）：`result = await self.router.execute(action_json)`，`router.execute`（`action_router.py:28-49`）把**所有** action 异常吞成字符串返回（"执行失败：…"、"拒绝：…"），但 `engine.py` 恒 `self._log(action, ..., status="ok", ...)` 且返回 `"ok": True`。
- **影响**：`decision_runs` 表把"实际失败/被拒"记成成功，复盘统计与冷却语义被污染；调用方（PM/豆包）以为已执行。
- **修复**：`router.execute` 返回结构化 `(ok, result)`；`engine` 按 `result` 前缀（"拒绝"/"执行失败"）记 `status="failed"`。

### P1-10 `bark.push` 未 await（协程从未执行）

- **证据**：`bark.push` 是 `async def`（`integrations/bark.py:52`）。
  - `api/doubao_webhook.py:425`：async 上下文里直接 `rt.bark.push(...)` **不 await**。
  - `skills/quarantine.py:176`：同步函数里调 `rt.bark.push(...)` ⇒ 协程从未创建执行，`RuntimeWarning: never awaited`；即便 await 也会因位置参数与签名 `push(body, *, title=...)` 不符而 TypeError。
- **影响**：设备失败通知、技能隔离告警**永远发不出去**（且 try/except 抓不到任何东西）。

### P1-11 `llm_decide/ask.py` 引用不存在符号

- **证据**（`skills/engines/llm_decide/ask.py`）：
  - `:86 from butler.ha import ha_api` —— `butler/ha.py` **不存在**（实为 `butler/integrations/ha.py`）⇒ `ImportError` 被 `:105-106` 吞成 warning ⇒ **"小爱主动问询"流程从未执行过**。
  - `:116-122 asyncio.get_event_loop().create_task(_tv_ask_stop())` —— `_tv_ask_stop` 全仓未定义 ⇒ `NameError` 被 `except Exception: pass` 吞掉 ⇒ **超时后电视聆听模式永不关闭**。

### P1-12 MQTT→队列：`QueueFull` 处理是死代码 + 单消费者队头阻塞

- **证据**（`bus/mqtt_client.py:87-89`）：
  - `except asyncio.QueueFull` 包在 paho 回调线程里，但 `put_nowait` 由 `call_soon_threadsafe` 派到 loop 线程执行，`QueueFull` 在那里抛，**不经过 paho 线程的 except** ⇒ 设计好的丢弃告警永不出现，改由 asyncio 默认处理器刷 traceback。
  - `_consume`（`app.py:94-123`）串行 `await rt.dialog.on_event(...)`，内含 LLM(30s)+TTS+HA ⇒ 期间事件全部堆积，超 2000 静默丢弃，**无丢弃计数**。
- **修复**：在 loop 线程消费侧捕获 `QueueFull` 并计数；或改用背压队列。

### P1-13 事件流 / 小爱耳朵：干净断连无退避 + 握手无超时

- **证据**（`core/event_stream.py:108-116,124-149,191-192`、`core/xiaomi_ear.py:41-49,62-65,85-101`）：
  - `while self._running: try: await self._connect()` 只有异常分支 `sleep(5)`；对端**干净关闭**时 `_connect` 正常返回 ⇒ 立即重连，**零退避**（紧密重连循环）。
  - 握手 `receive_json()` 无 `wait_for` ⇒ HA 不回帧但回 ping ⇒ 永久挂起且不抛异常，进程看似健康、事件流无声停摆。
  - `:191-192` 与 `xiaomi_ear.py:100-101` 的 `except Exception: continue` **连日志都不写**。

### P1-14 异步上下文内同步 DB/文件 I/O 阻塞事件循环

- **证据**（代表性命中）：
  - `core/event_stream.py:194,240-249`：`async def _handle_event` 内同步 `conn.execute()`+`commit()`，走 `busy_timeout=5000` ⇒ 每一条 HA `state_changed` 最坏阻塞事件循环 5 秒，且 except 只 warning 不 rollback。
  - `api/config_routes.py:19,28`：`read_text`/`write_text` 直接落盘。
  - `api/doubao_webhook.py:748-767`：async handler 内同步 SQLite。
  - `api/audiobook_routes.py`：`scan_library`/`load_progress`/`glob()+read_text` 全同步。
  - `skills/runner.py:110-117`、`decision/engine.py:214-225`、`core/dialog.py:610-617`：同步 SQLite 未 to_thread（部分调用方已用 `asyncio.to_thread`，但并非一致）。
- **影响**：事件风暴时全进程间歇性冻结；MQTT/SSE/webhook/TTS 全部冻结。

### P1-15 `CronTaskExecutor` 构造时 `scheduler=None`

- **证据**（`app.py:332` vs `app.py:712`）：`rt.cron_task_executor = CronTaskExecutor(..., scheduler=rt.scheduler)` 在 `:332`，而 `rt.scheduler` **首次赋值在 `:712`** ⇒ 构造时恒为 `None`；`:334 init_from_store` 全部走 `if not self.scheduler: return False` 打 warning；`:717` 才第二次 init。
- **影响**：只要 `:514-717` 的 try 抛异常 ⇒ `rt.scheduler` 永远 None ⇒ cron 技能静默全部不调度，且启动日志刷一遍 "scheduler not initialized" 掩盖真实故障。

### P1-16 `decision_store._init` 把所有 `OperationalError` 当"列已存在"

- **证据**（`core/decision_store.py:57-64`）：`except sqlite3.OperationalError: pass` 不区分错误码——意图是容忍 `duplicate column name`，但 `database is locked`、`database disk image is malformed` 也全部被跳过 ⇒ 旧库缺 `channel` 列而程序以为建好 ⇒ `create_decision` 恒 `no such column`，**决策记录功能从此失效**（叠加 P1-7 的 `_conn` 永久缓存）。
- **修复**：只对含 `duplicate column name` 的异常跳过，其余重新抛出。

### P1-17 事件循环内同步阻塞：TTS / LLM ReAct 无超时

- **证据**：
  - `tts/edge_tts.py:42-47`：`await proc.communicate()` **无 timeout**，整条 TTS 路径无 `wait_for` ⇒ edge-tts CLI 僵死时永久挂起，kokoro/Bark 兜底不生效。
  - `integrations/llm.py:217`：ReAct 循环对**工具执行本身无 `wait_for`**，`time_budget` 只在循环头检查 ⇒ 任一工具卡死则 `agent.run` 永不返回，`dialog.on_wakeup` 永挂。
  - `skills/runner.py:271-280`：`async with self._out_lock` 无 `wait_for` ⇒ 输出互斥锁持有者卡住则 `run()` 永不返回。

### P1-18 关停不完整（`app.py:747-763`）

- **证据**：`consumer.cancel()` **未 `await`**（:750）⇒ finally 继续到 `close()`，消费者可能正在 `repo.add_turn` ⇒ `Cannot operate on a closed database`；只停 3 项，漏停 `_presence_poll_task`、`af_bridge._loop_task`、`event_stream`、`ilink_bridge`、`ha._xiaomi_token_refresh_task`、audiobook 播放任务。af_bridge 会在 `mqtt.stop()` 之后继续 publish（静默丢失）。
- **对偶**：`yield` 之前 `:232-746` 是整段无保护装配，任一步抛异常则 `finally` 不执行 ⇒ 启动期无清理、关停期清理不全，**两头漏**。

### P1-19 `scheduler` 状态"假绿"：`mark_success` 包的是派发而非执行

- **证据**（`triggers/registry.py:230-241` + `app.py:518-560`）：`wrap_scheduler_job` 的 `mark_success` 包的是 `func`（全是 `lambda: asyncio.run_coroutine_threadsafe(...)`），返回的 Future **从不 `.result()`/`add_done_callback`** ⇒ 真实协程成败永不回写，`mark_success` 恒成功 ⇒ `/api/triggers/health` **假绿**。与 P1-3 叠加最坏：任务根本没跑，健康报告却全绿。

### P1-20 `mcp/server.py` 跨事件循环运行 SkillRunner ⇒ `Semaphore/Lock` 撞 loop

- **证据**（`mcp/server.py:375,389-400` + `skills/runner.py:42-44`）：MCP 把全局同一个 `SkillRunner` 单例放到**新线程的新事件循环**跑，而 `runner._sem = asyncio.Semaphore(1)`、`_out_lock = asyncio.Lock()` 是**跨循环原语** ⇒ Python 3.10+ 首次 acquire 绑定哪个 loop，换 loop 直接 `RuntimeError: bound to a different event loop`。主循环先跑则 MCP 侧永远失败；MCP 先跑则主循环所有技能报错，`_record_failure` 连续 3 次自动隔离技能。

### P1-21 `memory_agent.py` 无 `raise_for_status` ⇒ HTTP 错误被记成功

- **证据**（`integrations/memory_agent.py:53-66,42-51,111-125`）：无 `raise_for_status()` ⇒ HTTP 401/500 的错误体被 `_parse_sse` 的 `json.loads` 成功解析记成功 ⇒ **熔断（5次/30s）形同虚设**；SSE 多帧只取最后一帧，若末帧是 `notifications/*` 而非 `result`，返回值语义被静默替换。

### P1-22 时间语义混乱（无 TZ + 三套时间）

- **证据**：Dockerfile/compose/.env 均无 `TZ=` ⇒ 容器进程时区 = UTC。代码混用：显式 Asia/Shanghai（`decision/engine.py:117`、`core/agent.py:84`、`app.py:517 AsyncIOScheduler(timezone="Asia/Shanghai")`）、朴素 `datetime.now()`/`time.localtime()`（20+ 处）、硬编码 `+8`（`cron_task.py:480,492`）。
- **影响**："6:00-10:00 晨起窗口"实际在北京 14:00-18:00；trigger `time_range`、`cooldown_type=daily`、briefing"今天"、cron 条件全偏移 8 小时；与 APScheduler 按 Asia/Shanghai 准点触发的作业错配。

### P1-23 `_return_idle` 无代际/序号校验（`dialog.py:684-686`）

- **证据**：`asyncio.create_task(self._return_idle())` 在 `dialog.py` 共 9 处裸调用、不存引用、不校验当前状态；`await sleep(waiting_seconds)` 后无条件 `set_state(IDLE)`。t=0 播报的 timer 在 t=5s 触发，会把 t=1s 开始的新一轮会话强制打回 IDLE；且裸 `create_task` 无引用可能被 GC 中途回收 ⇒ 状态卡在 WAITING。

### P1-24 `dialog.on_wakeup` 函数级无 try/finally

- **证据**（`dialog.py`）：只有 `:411-415 agent.run` 单点保护，`:355` 生成技能、`:357` 写草稿、`:409 to_thread(repo.add_turn)`、`:420 speak_as_role` 任一抛错跳出函数，**状态永久停在 THINKING/SPEAKING**。

---

## 3. P2 — 边界情况 / 可观测性 / 轻微降级

| # | 位置 | 问题 |
|---|---|---|
| P2-1 | `config_routes.py:108-109,112` | `int(body["cooldown_seconds"])` 未包 try ⇒ 非数字 500；`list(body["dnd_windows"])` 对 dict/int TypeError。 |
| P2-2 | `api/doubao_webhook.py:146` | `int(value)` 对 `26℃` 等 ValueError 500；`:641` 链式下标 `data["choices"][0]["message"]["content"]` 被 `:643` except 静默归零 ⇒ 设备/场景/查询/页面四条链全丢。 |
| P2-3 | `api/doubao_webhook.py:441-464` | 去重 hash 在处理**之前**落表 + 路由层无 try ⇒ 处理抛 500 后，上游重试命中 duplicate 返回 `ok:true` ⇒ 指令永久丢失且上游以为成功。 |
| P2-4 | `api/config_routes.py` 三个 POST | 均未 `isinstance(body, dict)` 校验（对比 device/role/wakeup 都有）⇒ body 为 list/str 时 500。 |
| P2-5 | `api/doubao_webhook.py:498,511,523,534,501,515,526,538,691` | `rt.doubao.chat(...)`/`rt.notifier.push(...)` 无 None 检查 ⇒ notifier/doubao 未就绪时 500（对比 `role_routes.py:138-139` 有检查）。 |
| P2-6 | `api/cron_task_routes.py:175,184` → `core/cron_task.py:171-184` | async 路由里直接调同步 `executor.test_api()`，内部 `pool.submit(...).result()` 无 timeout ⇒ 阻塞事件循环直到目标 API 超时（用户可配更大），期间全服务冻结。 |
| P2-7 | `api/deps.py:53-61,64-76` | `_sessions_save/_load` 的同步磁盘 I/O 落在请求热路径（每次带 cookie 请求都走 `session_alive`）；`:136` 模块导入期读盘硬编码 `/app/data/sessions.json`，本地/非容器失效。 |
| P2-8 | `modes/auto_switch.py:23,131` | `_debounce` 是模块级 dict；4 处 `return result`（:86/99/114/128）绕过 `_reset_if_needed` ⇒ 其他 key 的 stale 计数不被重置，累积到阈值误触发切换。 |
| P2-9 | `skills/runner.py:43` | 全局 `asyncio.Semaphore(1)` 注释"VLM 串行"但**所有**引擎都包着 ⇒ 单条慢技能（LLM 30s）把所有 trigger/skill 堵 30s；连续超时触发 `record_timeout`→`_record_failure`→3 次自动隔离 ⇒ 一条慢 LLM 链路批量隔离依赖 LLM 的技能。 |
| P2-10 | `skills/runner.py:179-183` | 引擎 `registry.get()==None` 直接 `quarantine`（不可逆落盘）而非跳过；配合 `plugins.py:49` 加载失败只 warning 静默跳过 ⇒ 一次内置引擎加载失败把用该引擎的所有技能批量永久隔离。 |
| P2-11 | `skills/plugins.py:36-50` | 内置与用户插件同名引擎时 user 静默覆盖 builtin 无告警。 |
| P2-12 | `skills/runner.py:73-87,279-349` | `breaker_status`/`get_all_skill_stats` 每次全表扫 `skill_runs`，无 retention ⇒ 表越大越慢。 |
| P2-13 | `triggers/engine.py:100` | `_event_seen` 每次 `handle_event` 全量重建 dict ⇒ 密集事件 O(n) CPU 热点。 |
| P2-14 | `guard/push_guard.py:151-180` | 熔断触发时 `state["counts"]=[]` 清空 ⇒ `get_status` 看到 count 恒 ≤5，风控审计无法回答"熔断前堆了几条"。 |
| P2-15 | `guard/push_guard.py:34` | `PRIORITY_TTL` 常量定义但 `check_bark/check_tts` 全程未用（L4 TTL 过期未落地）。 |
| P2-16 | `tts/manager.py:86/manager.py:47-62` | `kokoro`/`nowvoice` 直接 `path.write_bytes` 无原子替换，进程中途被杀留半截 mp3，`_cache_hit` 只要 >5000 字节即当缓存命中 ⇒ 播放半截音频；两并发同 text 写同名文件缓存读竞态。 |
| P2-17 | `presence/fusion.py:48-49` + `presence_config.json` | 用户在 `user_device_map` 漏配 device_tracker 时 `_get_home_status` 恒 unknown、`room` 恒 None、confidence 永不更新 ⇒ 该成员永久隐身，依赖定位的 decision/proactive/mode_auto_switch 对其失效。 |
| P2-18 | `decision/engine.py:115-124` | `zoneinfo` 缺 `tzdata` 时夜间判定回落本地时区，容器 UTC 下 `night_start/night_end` 错 8 小时 ⇒ 夜间 TTS 拦截失效/误拦。 |
| P2-19 | `app.py:901-934` 进化线程 | `while True`+`time.sleep`，只 `except Exception` ⇒ `KeyboardInterrupt`/`SystemExit` 无顶层 `except BaseException`，该线程静默死亡后进化任务永久消失。 |
| P2-20 | `integrations/ha.py:171 vs 442` | `schedule_media_stop` **定义了两次**（第二次覆盖第一次，第一次是死代码，行为从 `media_stop` 变 `media_pause`）；且 `_media_stop_tasks` 是**类变量**（:440）非实例变量，跨实例共享。 |
| P2-21 | `api/stream_routes.py:16-17` | `guard(request)` 调用两次（重复鉴权 + 重复 `sessions.json` I/O）。 |
| P2-22 | `api/skill_routes.py:953 vs 979` | `skill_mock_test` 定义两次，后者静默覆盖前者。 |
| P2-23 | `api/dialog_routes.py:30-33,36-38` | `recent`/`state` 缺 `guard`；`recent` 的 `int(limit)` 未包 try。 |
| P2-24 | `api/bark_routes.py:114-116` | `except Exception: return ok({"pushed": False})` 以 200 返回失败，调用方只看 HTTP 状态会误判成功。 |
| P2-25 | `api/doubao_webhook.py:278-282` | CSP meta 仅在小写 `<head>` 时插入 ⇒ `<HEAD>`/仅 `<html>` 页面无 CSP。 |
| P2-26 | `api/notify_routes.py:131` | `Image.LANCZOS` 在 Pillow 10 起废弃（仍可用），弃用告警噪声。 |
| P2-27 | `api/doubao_webhook.py:211` | `attr = data.get("attributes", {})`，HA 返回 `"attributes": null` ⇒ `attr.get` AttributeError。 |
| P2-28 | `api/openai_routes.py:56-71` | `messages` 元素被假定为 dict ⇒ 任一非 dict 元素即 500。 |
| P2-29 | `api/scene_routes.py:148` | `all(r["ok"] for r in results)` 依赖 `_execute_step` 返回契约，某分支漏 `"ok"` 即 KeyError。 |
| P2-30 | `api/profile_routes.py:71-74` | `schedule` 失败被 `except Exception` 静默吞成 `{}`，错误不可观测。 |
| P2-31 | `api/task_routes.py:48-55` | `int(limit)` 的 ValueError 被 `except Exception` 吞成 500，输入错误误报为服务端错误。 |
| P2-32 | `core/event_stream.py:17` | `import aiohttp` 不在 requirements.txt（仅经 edge-tts 传递依赖）⇒ 一旦 edge-tts 换实现即崩，依赖脆弱。 |
| P2-33 | `store/repo.py:155` | 注解 `sqlite3.Row` 但 `repo.py` 未 `import sqlite3`，仅因 `from __future__ import annotations` 才不炸 ⇒ 脆弱。 |
| P2-34 | `app.py:18-52` | `decision_routes`（:36/:44）、`audiobook_routes`（:20/:51）被**重复 import**；`:204` 与 `:215` 两次 `*decision_routes.routes()` ⇒ 同一批 Route 重复注册（首条生效，冗余）。 |
| P2-35 | `core/self_evolve.py:31-37` | 硬编码 `/app/data/butler.db`，忽略可配 `DATA_DIR`，且无 try/finally ⇒ 异常时 `db.close()` 不执行。同族硬编码 `/app/data` 共 8 处。 |

---

## 4. 跨文件结构性主题

1. **"实现了但没接线"**（本轮最核心元观察，与历史报告一致）：
   - `PushGuard.tts_pop()` 0 调用者、`reset()` 0 调用者（P1-2）
   - `config_reloader.ConfigReloader` 定义完整但全仓无启动点（`app.py` 未装配）
   - `store/db.py` 的 `close()` 定义但除 `app.py:763` 外无人调用
   - `self_evolve.analyze_llm_traces` 写好了，调用方却 import 不存在的 `analyze_candidates`（P1-6）
   - `modes/engine.py` 的 `can_tts/can_bark/can_run_skill/is_care_skill_disabled` **全树 0 个生产调用者**（仅 `workorders` 里一个被注释的 shadow）⇒ **整套模式门禁（观影静音/睡眠禁 skill/away 全停）从未生效**

2. **坏连接永久缓存**（P1-7）：9 处 `_conn` 先缓存后 `_init`，无重试。

3. **全局异常兜底把 500 归一化**：`app.py:778 exception_handlers={Exception: _global_exc}`，`_global_exc`（:175-177）对所有未捕获异常返回 `{"ok": False, "error": "internal_error"}` 500。**副作用**：上文大量"500"最终都表现为同一种 `internal_error`，故障定位时无法区分是"坏输入"、"下游不可达"还是"真实 bug"。这与 P1-24（dialog 状态卡死）、P2-3（webhook 丢失）叠加后，现场表现为"管家无响应"且无有效日志。

4. **retention 缺失**：`decision_runs/skill_runs/trigger_runs/dialog_turns/chat_logs/memory_facts/notify_history` 全为只追加表，无 retention；`app.py:554-557` `_table_retention` 只清 `wakeup_log/trigger_audit/presence_history` 3 张表，且 `db_retention_days` 默认 0=关闭（`config.py:180`）。表越大，`count_skill_runs_today`（repo.py:218）、`breaker_status` 等每日全表扫越慢。

---

## 5. 建议优先修复顺序

| 优先级 | 项 | 一句话 |
|---|---|---|
| P0-立即 | P0-1 | 补 `nowvoice_client.py` 或移除 NowVoice，否则服务启动即崩 |
| P0-立即 | P0-2 | 修 `_env_bool` 恒 False，否则布尔开关全失效 |
| P1-立即 | P1-1 | `dialog.py` 的 `self.rt` → `self.ha`，恢复设备控制 |
| P1-立即 | P1-3 | cron `sync_job` 改 async 或复用 wrap 模式 |
| P1-立即 | P1-4 | 删 `next_run_time=None` 解除心跳 job |
| P1-立即 | P1-2 | 接通 `tts_pop`，否则 TTS 周期性静默 |
| P1-高 | P1-5 | `get_snapshot` → `snapshot`，恢复自动功能 |
| P1-高 | P1-7 | 修 `_init` 顺序 + 全树补 rollback + `busy_timeout` |
| P1-高 | P1-8 | 修触发引擎时基/退避 |
| P1-高 | P1-16 | `decision_store._init` 只容忍 `duplicate column name` |
| P1-中 | P1-10/11 | 修 `bark.push` await、`ask.py` 悬空引用 |
| P1-中 | P1-12/13/14/17 | 事件循环阻塞与背压 |

---

## 6. 已复核为"非问题"（避免后续重复排查）

- `app.py` 各 `while True` 轮询（`_consume:96`、`_presence_poll_loop:952`）外层均有 `except Exception`，单轮异常不会杀死循环；`CancelledError` 继承 `BaseException` 不在此捕获，属预期终止路径。真正会静默死的只有 `app.py:901` 进化线程（需 `BaseException` 才致死）。
- `store/db.py:24` 的 `check_same_thread=False` + `_lock` 是正确写法；坑在多模块各自另开连接未设 `busy_timeout`。
- `dialog_routes.py:38` 的 `state()` 返回 `ok(...)`（包住三元表达式），不是裸 dict —— **不是**早前子代理误报的 P0。
- `triggers/engine.py` `continue_on_error` 默认 True、部分 action 失败整条判 `ok=False` 是设计，与 `all(...)` 叠加对多 action 串联偏严（边界，非纯 bug）。
- `event_stream.py` 的 `aiohttp` 依赖当前经 edge-tts 传递可用，`_handle_event` 已用 `asyncio.to_thread` 包 SQLite（`event_stream.py` 内确认），阻塞面已收敛。

---

## 7. 范围声明

- 本报告**只覆盖功能性 bug 与稳定性缺陷**。
- **不覆盖**：安全/鉴权/凭据/注入/CORS/容器加固（docker.sock、one-api.db、非 root、read_only 等）——这些已有 `doc/04-安全加固/P0-10容器安全加固计划.md` 及第一轮安全审计报告独立覆盖。
- 全部结论基于静态阅读 + grep 交叉验证；未在真实 NAS 上运行复现。
- 由于本工作区 `E:\NAS\doubao-butler` 按 `.gates.toml:6` 属"过期副本"，实际 NAS 部署可能已含修复；请以 NAS 那份为准复核 P0 项。