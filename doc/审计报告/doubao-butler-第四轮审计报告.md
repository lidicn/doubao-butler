# doubao-butler 第四轮审计报告 · 出站调用超时链路与异步任务生命周期

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮方向（自主选定）**：**出站 HTTP 超时/重试链路 + 异步任务生命周期（取消与泄漏）**
  - 前三轮已覆盖：启动/lifespan 装配、TTS 栈、SQLite、对话状态机、MQTT、JSON 持久化与并发共享状态
  - 本轮补位：**一次外部调用到底会卡多久**、**上层超时能否真正生效**、**进程退出时后台任务是否收干净**
- **方法**：逐行通读 `integrations/llm.py`、`core/agent.py`、`af_bridge.py`、`core/event_stream.py`、`core/xiaomi_ear.py`、`api/stream_routes.py` + 超时参数链路推算 + 停止路径调用追踪
- **结论**：**1 个 P0 + 3 个 P1 + 2 个 P2**。核心问题是「**超时预算形同虚设**」——代码里到处写着 timeout，但逐层累加后可以卡到 4.5 分钟，而上层预算根本没机会生效。

---

## 一、P0 级缺陷

### P0-6 LLM 重试链最坏耗时 ≈ 270s，而上层 time_budget=120s 完全无法中断它

- **位置**：`butler/integrations/llm.py:71-106`（`_raw`）、`:163-172`（`chat_with_tools` 预算检查）、`butler/core/agent.py:194-208`（外层重试）、`butler/core/dialog.py:434`（调用点）

**逐层耗时推算**（按 `config.py` 默认值）：

| 层 | 参数 | 最坏耗时 |
|---|---|---|
| `_raw` 单次 HTTP | `llm_timeout=30` | 30s |
| `_raw` 重试次数 | `llm_retry_max=3` → 共 **4 次尝试** | 4 × 30 = **120s** |
| `_raw` 退避 | `llm_retry_backoff=2.0`，第 n 次 `2×2^n×U(0.5,1.5)` | 2+4+8 = **~14s** |
| **单次 `_raw` 小计** | | **≈ 134s** |
| `agent.run` 外层重试 | `for attempt in range(2)` | ×2 = **≈ 270s** |

**为什么预算拦不住**：

```python
# llm.py:163-171 —— 预算只在「循环迭代开头」检查
for i in range(max_iter):
    if _timeout():                 # ← 只在迭代边界检查
        return _escape(reason)
    data = await self._raw(...)    # ← 这步最长 134s，期间任何检查都进不来
```

单次 `_raw`（≈134s）**已经超过** `time_budget=120s`。也就是说：第一次迭代结束时预算必然已超，但那时调用早已完成——**预算从未起到"中断"作用，只起到"不再发起第二轮"作用**。而 `agent.run` 的外层 `for attempt in range(2)` 又绕过了这个检查，直接再来一遍。

**调用点无兜底**：`dialog.py:434` 是裸 `await self.agent.run(...)`，**没有 `asyncio.wait_for`**。

**功能影响**：
- LLM/网络抖动一次 → 用户等待最长 **4.5 分钟**才听到"刚才卡了一下"
- 期间对话状态机停在 `THINKING`（`dialog.py:314`），该成员后续语音被状态机占用
- `THINKING` 期间若 TTS 播报触发 `suppress_echo`，回声抑制窗口叠加，进一步吃掉后续输入
- 用户侧表现为「管家突然哑了几分钟」，且无任何超时告警日志

**修复方案**（三处都要改）：

```python
# 1) llm.py chat_with_tools：用剩余预算做硬中断
def _timeout() -> bool:
    return (time.time() - t_start) > time_budget

for i in range(max_iter):
    if _timeout():
        ...
        return _escape(reason)
    remaining = time_budget - (time.time() - t_start)
    try:
        data = await asyncio.wait_for(
            self._raw(full, tools=tool_schemas, temperature=temperature,
                      max_tokens=max_tokens, backend=backend),
            timeout=max(5.0, remaining),        # ← 关键：让预算能真正切断
        )
    except asyncio.TimeoutError:
        reason = f"超时（>{time_budget:.0f}s）"
        if tracer: tracer.finish("timeout", reason)
        return _escape(reason)

# 2) agent.py：重试前先扣减已消耗预算，别让外层把总时长翻倍
t0 = time.time()
for attempt in range(2):
    reply = await self.llm.chat_with_tools(..., time_budget=max(20.0, 120.0 - (time.time()-t0)))

# 3) dialog.py:434：调用侧兜底硬超时
try:
    reply = await asyncio.wait_for(
        self.agent.run(message, member, history, system_override=sys_prompt,
                       source=source, trace_id=trace_id),
        timeout=150,
    )
except asyncio.TimeoutError:
    reply = "嗯，我想得有点久，能再说一遍吗？"
```

> 顺带建议：把 `llm_retry_max × llm_timeout` 的乘积纳入启动自检——「单次调用的**总**耗时上限」应显式小于所有上层预算，否则预算永远是纸面的。

---

## 二、P1 级缺陷

### P1-16 8 处 `aiohttp.ClientSession()` 未设 timeout（默认挂 300s）

- **位置**：
  - `af_bridge.py:216`（`_announce` 的 POST）
  - `core/briefing.py:97, 114`（天气 / 新闻拉取，**定时任务 7:10 / 22:00**）
  - `core/tts_baidu.py:32, 57`（百度 TTS token 获取，在合成链路上）
  - `core/xiaomi_ear.py:70`（HA WS 连接）
  - `skills/engines/autoflow_propose/engine.py:204, 268`
- **现象**：`aiohttp.ClientSession()` 不传 `timeout` 时，默认 `ClientTimeout(total=300)`。对比同文件 `af_bridge.py:111` 就**正确写了** `timeout=aiohttp.ClientTimeout(total=5)`——说明是遗漏而非刻意。
- **功能影响**：
  - `briefing.py` 挂在定时任务里：一次 HTTP 卡满 300s，会让当日简报**推迟 5 分钟**才发出（或与该时段其他任务堆叠）
  - `tts_baidu.py` 在合成链路上：token 卡住 → 该次播报整体延误，叠加 P0-4（NowVoice 160s）会形成**播报雪崩**
  - 这些调用不阻塞事件循环（是 await），但**任务堆积**会让用户感知到"管家反应变慢"

**修复**：

```python
_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10, sock_connect=5, sock_read=10)
async with aiohttp.ClientSession(timeout=_DEFAULT_TIMEOUT) as session:
    ...
```
> 注：`xiaomi_ear.py` / `event_stream.py` 的 WS 长连接**不应**套 total 超时（会误杀长连接），应单独设 `ws_connect(timeout=10, heartbeat=30)`，只对握手阶段限时。

---

### P1-17 三个后台任务的 stop 路径缺失 → 退出/重载时残留

- **位置**：`butler/app.py:780-794`（lifespan finally）

lifespan 退出只做了 5 件事：`consumer.cancel()`、`mqtt.stop()`、`xiaomi_ear.stop()`、`_sched.shutdown()`、`db.close()`。**以下三个从未被收尾**：

| 模块 | 停止方法 | 调用情况 | 后果 |
|---|---|---|---|
| `af_bridge` | `stop()`（`af_bridge.py:91`，会 cancel `_loop_task`） | **全仓零调用点** | 轮询 task 残留，继续 POST 外部 AF 服务 |
| `event_stream` | `stop()`（`event_stream.py:104`，仅置 `_running=False`） | **未被 lifespan 调用**；且只置标志**不 cancel task**，若此刻正 `await` 在 WS 上，循环根本不会退出 | WS 订阅残留 |
| `tts/playback_queue` | `shutdown()`（`playback_queue.py:128`） | **全仓零调用点** | 每设备 worker task 残留 |

**功能影响**：`uvicorn --reload`、优雅重启、容器热重载时，旧 task 未取消 → **重复 MQTT 订阅 + 重复 HA WS 连接 + 重复播报**，并输出 `Task was destroyed but it is pending`。单进程 `--workers 1` 下容器完全重启可清空，但**开发重载与滚动更新窗口内必然出现**。

**修复**（与 P1-3 的 `spawn()` 集合配合，统一在 finally 收尾）：

```python
finally:
    consumer.cancel()
    mqtt.stop()
    try: await af_bridge.stop()          # ← 补
    except Exception: pass
    if getattr(rt, "event_stream", None):
        rt.event_stream.stop()
        es_task = getattr(rt.event_stream, "_task", None)
        if es_task: es_task.cancel()     # ← 光置标志不够，必须 cancel
    if getattr(rt, "tts", None):
        await rt.tts.queue.shutdown()    # ← 补（PlaybackQueue）
    for t in list(getattr(rt, "_bg_tasks", set())): t.cancel()
    await asyncio.gather(*[...], return_exceptions=True)
```

---

### P1-18 SSE 订阅者泄漏，且泄漏者永久占位

- **位置**：`butler/api/stream_routes.py:18-36`

```python
q = asyncio.Queue(maxsize=200)
rt.sse_subscribers.add(q)        # ← L20：在生成器「外」注册

async def gen():
    try:
        yield ": connected\n\n"
        while True: ...
    finally:
        rt.sse_subscribers.discard(q)   # ← L36：在生成器「内」注销
```

- **现象**：`discard` 依赖 `gen()` 协程**被启动并执行到 finally**。若客户端连接后立即断开、或 `StreamingResponse` 因异常未被消费（协程从未运行 / 在首个 `yield` 前被取消的路径外），`finally` 不执行 → `q` **永久留在 `sse_subscribers`**。
- **放大效应**：`app.py:105-109` 对每个事件遍历全集合 `put_nowait`，`QueueFull` 静默 `pass`。泄漏的队列无人消费 → 很快填满 200 → 其后所有事件对**该僵尸订阅者**静默丢弃；集合膨胀后每次事件的遍历成本线性上升。
- **无上限、无 TTL**：`Runtime.sse_subscribers` 是裸 `set`（`runtime.py:52`）。
- **触发场景**：WebUI 反复刷新、笔记本合盖断网重连、反向代理超时切断——都是家庭 NAS 场景的日常操作。

**修复**：把注册移进生成器，并加 disconnect 回调与上限：

```python
async def gen():
    rt.sse_subscribers.add(q)                # ← 移入：只要 gen 启动就一定配对
    try:
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(q.get(), timeout=15)
                ...
    finally:
        rt.sse_subscribers.discard(q)
```
另外在 `app.py` 的 `_consume` 中做惰性清理：

```python
dead = [q for q in rt.sse_subscribers if q.full()]
for q in dead: rt.sse_subscribers.discard(q)   # 长期无人消费 = 僵尸
```

---

## 三、P2 级隐患

| # | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| P2-14 | `core/xiaomi_ear.py:104-105` | `_echo_cooldown` / `_role_mode` 定义在**类体**中 → 所有实例共享同一 dict；且 `_echo_cooldown` **只写不清理**（L130 仅读，全文件无 `del`） | 移到 `__init__` 中初始化；`_echo_cooldown` 增加 TTL 清扫（按 `dev.id` 过期剔除） |
| P2-15 | `xiaomi_ear.py:75-79`、`event_stream.py:127-132` | WS 握手后 `await ws.receive_json()` **无超时**：若 HA 建连成功却不返回 `auth_required`，该协程永久挂起，`while self._running` 循环再也不会重试 | `await asyncio.wait_for(ws.receive_json(), timeout=10)` |

---

## 四、已复核并**排除**的疑似问题

1. **`llm._raw` 没有重试上限导致死循环** —— 已确认有 `attempt < max_retries` 判据，会正常退出。**不是 bug**（真正的问题是重试总时长，见 P0-6）。
2. **`httpx.AsyncClient` 全部无超时** —— 实际核查：全仓 20+ 处 httpx 调用**均显式传了 timeout**（5s / 10s / 15s / 25s / 30s / `llm_timeout`），是本项目的良好实践。**不是 bug**。
3. **`requests` 库同步调用** —— 全仓零使用，出站已全部异步化。**不是 bug**。
4. **`af_bridge._poll_once` 的 HTTP 无超时** —— 已确认 L111 显式写了 `timeout=aiohttp.ClientTimeout(total=5)`。**不是 bug**（但同文件 L216 遗漏，已计入 P1-16）。
5. **`xiaomi_ear` / `event_stream` 断线重连** —— `while self._running` + `except` 后 `sleep(5)` 重试，逻辑正确。**不是 bug**（缺的是进程退出时的 cancel，见 P1-17）。

---

## 五、本轮审计局限

1. P0-6 的 270s 为**按默认配置的理论上限推算**（`llm_timeout=30 / retry_max=3 / backoff=2.0`），未实测。若部署环境通过环境变量调低了这些值，实际最坏耗时会相应下降——但**「单次 `_raw` 最坏耗时 > `time_budget`」这个结构性矛盾依然存在**（只要 `llm_timeout × (retry_max+1) > time_budget` 就成立）。
2. P1-18 的 SSE 泄漏**未做实机复现**，结论基于「注册与注销不对称」的代码结构推断；`gen()` 未被启动的具体触发条件取决于 Starlette/Uvicorn 版本行为。
3. P1-17 的残留影响在 `--workers 1` + 完整容器重启场景下会被清空，**主要影响开发重载与滚动更新窗口**，严重程度依赖实际部署方式。
4. 未通读 `triggers/engine.py`、`proactive/engine.py`、`modes/*`、`mcp/server.py` 的超时与生命周期处理，这些模块可能存在同类问题。
5. 未运行测试与集成验证（沙盒缺 MQTT / Home Assistant / LLM 端点依赖）。
6. 本报告**不重复**第一、二、三轮已列条目，四轮需合并阅读。
