# doubao-butler 第五轮审计报告 · 衔接处与连接处

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮方向（指定）**：**衔接处与连接处** —— 模块之间、层与层之间、前后端之间、代码与设备真源之间的接缝
- **工具**：AST 签名比对脚本（1306 个定义）+ 路由契约比对脚本（后端 267 条 vs 前端 141 处）+ 逐文件通读衔接层
- **结论**：**3 个 P0 + 2 个 P1 + 2 个 P2**。
- **本轮最重的一条**：三个缺陷**首尾相连**，共同构成一个**完美静默的播报失效链**——队列在、入队成功、上层记账成功、预算已扣，但声音永远不出来，且**没有任何一行 ERROR 日志**。

---

## 一、P0 级缺陷

### P0-7 TTS 队列 worker Task 引用被丢弃 → 后台任务可被 GC，播报全哑

- **位置**：`butler/tts/singleton.py:37`（`loop.create_task(_queue.run())`）；`butler/app.py:288`（`init_tts_queue(tts)` 返回值同样丢弃）

```python
try:
    loop = asyncio.get_running_loop()
    loop.create_task(_queue.run())     # ← 返回值未保存，无强引用
    logger.info("TTSQueue worker started")
except RuntimeError:
    logger.warning("TTSQueue run() called outside event loop, worker not started")
```

- **根因**：CPython 文档明确要求持有 `Task` 的强引用；`asyncio` 内部只持有**弱引用**，未保存引用的 Task **可能在完成前被垃圾回收**。
- **影响面**（v2.5 三路由合并后，这些**全部**走该队列）：
  - `core/dialog.py:670` 所有主动/对话发声
  - `bus/inbox.py:273` 公共收件箱 speak 通道
  - `core/briefing.py:214` 早报/晚报
  - `app.py:729` **安全告警（P1 优先级）**
- **故障形态**：`TTSQueue` 对象本身被模块级 `_queue` 全局引用，所以**队列还在、`enqueue()` 仍返回 True**；但 `_q` 无人消费，消息堆到 TTL（300s）后被 `_sweep` 丢弃，计入 `dropped.expired`。
- **可观测性**：**零 ERROR**。日志里只会有启动时的 `TTSQueue worker started`，此后一切"正常"。

**修复**（与第三轮 P1-3 的 `spawn()` 集合统一）：

```python
# singleton.py
_queue_task: asyncio.Task | None = None

def init_queue(manager, config=None) -> TTSQueue:
    global _queue, _manager, _queue_task
    ...
    _queue_task = loop.create_task(_queue.run(), name="tts-queue-worker")
    logger.info("TTSQueue worker started")
    return _queue

def shutdown_queue() -> asyncio.Task | None:
    return _queue_task      # 交由 lifespan finally 统一 cancel
```
并在 `app.py` lifespan `finally` 中 `cancel()` 它（当前也**没有**，见下条 P1-17 的同类问题已在第四轮列出）。

> 建议加一条自检：启动后 60s 检查 `queue.status()["played"] + ["dropped"]["expired"]`，若入队过但 `played==0` 且 `expired>0`，直接 ERROR 告警——把"静默失效"变成"可观测"。

---

### P0-8 房间→设备映射：三份真源 + 覆盖不全 + 2 个臆造键 → 4/7 房间播报 100% 失败

**权威真源**（`butler/devices.py` 种子注册表，7 个房间）：
```
客厅、书房、Kevin房间、Emily房间、主卧室、主卧室浴室、卫生间
```

**第二份真源**（`butler/tts/adapter.py:16-22`，注释自称"从 devices.json 搬过来的"）：

| adapter 的 key | devices.py 是否存在 | 命中情况 |
|---|---|---|
| 书房 | ✅ | 命中 |
| 客厅 | ✅ | 命中 |
| 主卧室 | ✅ | 命中 |
| **卧室** | ❌ **不存在** | **永不命中**（臆造键） |
| **房间** | ❌ **不存在** | **永不命中**（疑似想写 Kevin/Emily 房间） |

**实际未覆盖的房间（4 个）**：`Kevin房间`、`Emily房间`、`主卧室浴室`、`卫生间`

- **后果**：`enqueue_tts(room="Kevin房间", ...)` → `ROOM_TO_PLAYER_ENTITY.get()` 返回 `None` → `adapter.py:58` 日志 `no device resolved for room 'Kevin房间', skipping playback` → **返回 False，消息被丢弃**。
- **调用点**：`dialog.py:670`（room 取自成员档案 `mc.room`）、`inbox.py:273`（room 来自 MQTT 投递方）、`briefing.py:214`（硬编码 "客厅"，唯一安全的）
- **第三份真源**：`notify/router.py:181` `self.room_entities = dict(room_entities or {})` —— `app.py:289` 的 `init_router(...)` **未传该参数**，实际为**空 dict**，房间解析永远落到 `default_entity_id`（见 P1-20）。

**修复**（消除第二、三份真源，统一从权威源解析）：

```python
# adapter.py —— 删掉硬编码表，改为运行时从设备注册表解析
def _resolve_player_entity(self, room: str) -> str | None:
    rt = get_runtime()
    for dev in getattr(rt, "devices", None) and rt.devices.all() or []:
        if getattr(dev, "room", "") == room and getattr(dev, "type", "") == "xiaomi":
            return getattr(dev, "ha_entity", "") or None
    return None
```
`init_router` 同样注入 `room_entities=...`（或从同一函数构建）。

> 兜底建议：房间解析不出时**降级到默认音箱（如客厅）并日志告警**，而不是直接丢弃——安全告警丢在"Kevin房间"这种房间名上是不可接受的。

---

### P0-9 「入队成功」被当作「播出成功」→ 账实不符，故障完全静默

- **位置**：`butler/bus/inbox.py:273-283`、`butler/core/dialog.py:670-673`

```python
# inbox.py
accepted = enqueue_tts(note["text"], priority=..., room=note["room"], ...)
if accepted:
    today = self.budget_consume(note["source"])          # ← 先扣预算
    logger.info("INBOX_DONE ... budget=%d/%d", ...)      # ← 再记成功
    return OUTCOME_ACCEPTED, "budget=%d/%d"
```

```python
# dialog.py
queued = enqueue_tts(text, priority=priority, room=room, ...)
if queued:
    return {"spoken": True, "engine": "queue", "devices": [], "queued": True}
```

- **根因**：`enqueue_tts()` 返回 `True` 仅表示 **TTSQueue 接受了这条消息**（入队），**不代表播出**。真正的播出发在 worker 里（`adapter.py:58` 房间解析失败 / `manager.speak` 抛错），此时：
  - `TTSQueue.play_one` 记 `self._failed += 1`，仅 `logger.warning`
  - **没有任何回调把失败回传给入队方**
- **三重账实不符**：
  1. `inbox` **已扣每日预算** → 预算记了，声音没有
  2. `inbox` 日志 `INBOX_DONE`（成功）→ 排障时看到的是成功
  3. `dialog` 返回 `spoken: True` → 上层 API/状态机认为发声成功

**与 P0-7/P0-8 的合成故障**（这是本轮最值得警惕的）：

> worker 被 GC（P0-7）→ 消息永不出队 → 或房间解析失败（P0-8）→ 消息被丢弃
> ↓
> `enqueue()` 返回 True → `inbox` 扣预算、记成功、`OUTCOME_ACCEPTED`
> ↓
> **用户听不到声音，系统所有账本与日志都显示成功，无一行 ERROR。**

**修复**（把异步结果回传给入队方）：

```python
# queue.py —— 给 TTSItem 增加完成回调
@dataclass
class TTSItem:
    ...
    on_done: Callable[[bool, str], Any] | None = None   # (ok, reason)

# play_one 结束处
if item.on_done:
    try: item.on_done(ok, "" if ok else "playback_failed")
    except Exception: logger.debug("on_done callback failed")
```

```python
# inbox.py —— 入队只"预占"，播出成功才"确认"
accepted = enqueue_tts(..., on_done=lambda ok, r: self._settle(note, ok, r))
if accepted:
    return OUTCOME_QUEUED, "已入队，待播出确认"    # ← 不再立即扣预算
```
或短期折中：把 `enqueue_tts` 的返回值语义改名为 `accepted`，`dialog.py` 返回 `{"queued": True, "spoken": None}`，**不再谎报 spoken**。

---

## 二、P1 级缺陷

### P1-19 前端调用后端不存在的路由 `/api/skill/runs` → 技能运行日志面板永久空白

- **位置**：前端 `butler/static/app.js:70` 和 `:82`；后端 `butler/api/skill_routes.py:1057`
- **现象**：前端请求 `/api/skill/runs?limit=50`，后端**只有** `/api/skill/{skill_id}/runs`（需要具体 `skill_id`），无匹配路由 → **404**
- **静默原因**：前端在 `Promise.all` 中用了 `.catch(() => ({items: []}))`，404 被吞成空数组 → 面板永远显示"暂无记录"，无任何报错
- **验证**：全仓 grep 确认后端无 `/api/skill/runs` 静态路由定义

**修复**（后端补一条聚合路由，前端更合理）：

```python
Route("/api/skill/runs", list_all_skill_runs, methods=["GET"]),
```
```python
async def list_all_skill_runs(request):
    limit = int(request.query_params.get("limit", 50))
    return ok({"items": repo.recent_skill_runs(limit=limit)})   # 已有类似查询可复用
```
同时建议前端 catch 分支改为 `console.warn` + 面板显示"加载失败"，避免同类问题再次静默。

---

### P1-20 NotifyRouter 的 `room_entities` 为空，兜底 `tts.doubao_tts` 未在部署清单中声明

- **位置**：`butler/notify/router.py:171,181,280`；`butler/app.py:289`

```python
# router.py:280
entity_id = note.entity_id or self.room_entities.get(note.room, "") or self.default_entity_id
#                              ↑ app.py:289 未传 → 空 dict → 永远走兜底
```

- **现象**：`init_router(bark=..., ha=..., tv=..., tts_queue=...)` **未传 `room_entities`**，所有 TTS 通道的房间解析直接落到 `default_entity_id = "tts.doubao_tts"`。
- **风险**：`tts.doubao_tts` 在 `devices.py` 种子注册表中**不存在**，仅在 3 处被硬编码为默认参数。它是 HA 的 `tts` 平台实体，**依赖 HA 侧已配置该平台**；若 HA 未配置（或配成 `tts.xiaomi` 等其他名），所有走 NotifyRouter 的 TTS 播报失败。
- **加剧因素**：`app.py:289` 的 `init_router` **也没传 `app`**，直到 `app.py:519` 才补挂 `_notify_router.app = rt.notifier` —— 启动窗口内 app 通道不可用（当前窗口极短，属脆弱耦合）。

**修复**：
1. `init_router(..., room_entities=_build_room_entities(devices), app=rt.notifier)` 一次传全（需调整初始化顺序，把 `devices.load()` 提前）
2. 在 `.env.example` / README 明确声明 HA 侧需提供 `tts.doubao_tts` 实体，或改为从配置读取

---

## 三、P2 级隐患

| # | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| P2-16 | `core/dialog.py:656` | `XiaomiEar._echo_cooldown[d.id] = time.time()` —— 直接写**另一个模块的类级属性**，跨模块隐式耦合；且该类属性全仓无清理（只写不删） | 改为 `rt.xiaomi_ear.note_speak(dev.id)` 显式方法调用；内部加 TTL 清扫 |
| P2-17 | `dialog.py` `speak_as_role` 返回值形状不一致 | 无设备分支返回 `{"spoken": False, "fallback": "bark"}`，正常分支返回 `{"spoken": True, "devices": out}`；`_quick_reply` / `on_wakeup` 将整个 dict 作为 `"spoken"` 字段透传给 API 层——**同一字段有时是 bool、有时是 dict** | 统一为 `{"spoken": bool, "devices": list}`；或把回执拆成独立字段 `receipts` |

---

## 四、已复核并**排除**的疑似问题（避免误改）

1. **跨模块函数签名不匹配** —— AST 扫描全仓 1306 个定义，逐一比对必填参数与未知关键字，**零真实不匹配**。唯一疑似项 `agent_collab.py:366 register_agent()` 经核实为 `self.register_agent(**agent)` 字典解包调用，**误报**。
2. **`ADM_CAPS="adm/doubao-caps"` vs `ADM_STATUS="adm/doubao-butler/status"` 命名不一致** —— 经查 `doc/00-路线图/豆包管家版本开发路线图_v2.5_DCD版.md:189` 明确写死的跨仓约定，**非 bug，勿改**（改了会断 ADM 生态对端）。
3. **MQTT 自订阅**：`SUB_TOPICS` 含 `butler/dialog/event`（即自身 `PUB_DIALOG`），看似死循环 —— 核实 `_consume` 首分支即拦截处理，**设计如此**。
4. **MQTT `adm/*/status` 落入 `dialog.on_event` 的 `endswith("/status")` 分支** —— 核实 `_consume` 中 `adm/*` 在第 5 分支被优先拦截，**不会误判为 TV 离线**。
5. **其余 8 处前后端路由疑似断裂** —— 逐一 grep 核实（`ha/devices`、`memory/facts`、`evolution/suggestions`、`task/*` 等）**后端均存在**，为比对脚本误报。

---

## 五、三轮衔接缺陷的共性根因

本轮 3 个 P0 集中在**同一条链路**上，且共享一个根因：**TTS 队列 v2.5 改造留下了三处未接完的线头**：

```
入队方 (dialog / inbox / briefing / 安全告警)
    │
    ├─ 线头 1（P0-9）：enqueue 返回值被当作"播出成功"
    │                   → 入队即记账，失败无回传
    ▼
TTSQueue（对象被全局引用，存活）
    │
    ├─ 线头 2（P0-7）：worker Task 无强引用 → 可被 GC
    │                   → 队列存活但无人消费
    ▼
TTSManagerSpeaker
    │
    ├─ 线头 3（P0-8）：房间映射硬编码副本，覆盖 3/7
    │                   → 4 个房间 100% 丢弃
    ▼
（无声）
```

**建议的收敛动作**：`enqueue_tts()` 增加一个可选的 `on_done` 回调，并把 `played` / `failed` / `dropped` 三个计数器暴露到 `/api/health`。这三项做完，"播报静默失效"这一类问题就**从不可观测变为可观测**——即使根因未修完，运维也能第一时间发现。

---

## 六、本轮审计局限

1. P0-7 的 GC 风险为**文档规定的行为推断**，实际是否触发取决于 CPython 版本与 GC 时机，**未做实机复现**。但无论是否触发，"Task 无强引用"都是必须修的规范性问题。
2. P0-8 的 4 个未覆盖房间基于 `devices.py` **种子值**推断；若实际部署的 `devices.json` 被改过（WebUI 可编辑），受影响房间集合会不同——但"两份真源会漂移"这个**结构性结论不变**。
3. `tts.doubao_tts` 是否在 HA 侧存在**无法从本仓库确认**（需看 HA 配置），故 P1-20 按"未在部署清单声明"定级，而非"确认失效"。
4. 未通读 `mcp/server.py`、`agent_collab.py`、`modes/*` 的模块间接缝，这些位置可能存在同类问题。
5. 前后端契约比对仅覆盖 `butler/static/app.js` 与 `js/app.js`，`dashboard_pwa/js/app.js` 与 `js/pages/*.js` 未做完整比对。
6. 未运行测试与集成验证（沙盒缺 MQTT / HA / LLM / 小爱设备依赖）。
7. 本报告**不重复**第一至四轮已列条目，五轮需合并阅读。
