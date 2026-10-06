# doubao-butler 第十七轮：修复三条 P0 + 补最后一刀（真实 lifespan）

> 本轮两件事：① 应用三条 P0 修复 ② 用 `amqtt` 跑通真实 lifespan —— **十六轮悬案终结**
> 交付：**4 条缺陷已修复**（V18/V23/V25/V28）；**新增 V32、V33**（运行时实证）；套件 33 场景 / 62 条用例；全仓库 **7 failed / 649 passed**（无副作用）

---

## 0. 先认错：十六轮的一个说法是错的

我从第二轮起反复写「完整 lifespan 跑不起来」「真实 MQTT 未验证」，归因于**沙箱出站被拦**。

实测结果：

```
→ paho 真连上 broker，rc = Success
→ 收到消息: ['connected rc=Success', "butler/event/test=b'hello'"]
```

**沙箱里能跑真实 MQTT。** `amqtt`（纯 Python broker，pip 可装）起在 127.0.0.1:1883，paho 真连上、真收发。

我混淆了两件事：**外网确实被拦**（pypi/github/golang proxy 全部 HTTPError），但**本地回环完全可用**，且 pip 有镜像能装包。MQTT 根本不需要外网。

**这不是环境限制，是我没找到方法。** 十六轮里我至少四次把它写进"边界"当免责声明，而它一直可解。

---

## 1. 三条 P0 已修复（不再是建议）

| ID | 位置 | 修复 |
|---|---|---|
| **V23** | `mcp/server.py:_run_coro` | 已有 loop 直接 `await`，无 loop 才 `asyncio.run` |
| **V18** | `docker_tools.py:108` | 删除 `time.sleep(2) if False else None` 死代码，保留「不等待」原意 |
| **V25/V28** | `store/db.py:get_conn` | 双重检查锁 + `_init` **完成后**才发布 `_conn` |

V18 修复时我特意保留了原注释里的设计意图（"不 sleep，让调用方观察"），只删那行死代码——**修复不该顺手改变作者意图**。

### 修复暴露了套件的两个缺陷（已修）

1. **文本级正测会被注释污染**：我修复时若在注释里复写原缺陷代码，正测在修复后仍报"实锤"。已把 v18/v28 正测升级为 **AST 级**判定。
2. **反测硬编码行号**：V18 反测用了行号 107 + 精确字符串，代码一改即失效。已重写为不依赖行号的 AST 结构断言。

### 工作流新增 RETIRED 语义

正测转红 = 缺陷已修复。已撤下 V18/V23/V25/V28 四条正测，**由反测接管回归守护**。`selftest()` 现在能区分「有意撤下」与「意外遗漏」——后者正是 V22/V23 曾漏注册六轮的教训。

---

## 2. 最后一刀：lifespan 真跑起来了

```
→ create_app() 成功，路由数 = 264
[startup] command_queue table initialized
[startup] wake_ding.mp3 generated
✓ lifespan 启动完成
   GET /api/health → 200 {'ok': True, 'online': True}
   rt.mqtt = MQTTClient
   ✓ 已注册定时任务 19 个
       morning_routine / evening_briefing / memory_extract /
       anomaly_detector / proactive_scenes / security_monitor / ...
```

端到端也通了：

```
→ 发布 butler/event/test_evt
→ butler/# 收到: ['butler/status/state', 'butler/event/test_evt']
```

**十六轮从未执行过的 lifespan、MQTT 链路、19 个定时任务注册，本轮全部跑通。**

---

## 3. V33（P0，新发现）：`/api/health` 恒 200 —— 核心全挂也报绿

跑起来第一眼就看见了。lifespan 启动时的真实状态：

- scheduler **0 个任务**（注册失败）
- MQTT **已断开**
- af_bridge **403**
- presence **降级**为 HA-only
- audiobook 初始化**失败**

而：

```
GET /api/health → 200 {'ok': True, 'data': {'online': True}}
```

源码（`api/system_routes.py:11-13`）：

```python
async def health(request):
    # 始终 200，不因下游不可用而失败（NAS 规范硬约束）
    return ok({"online": True})
```

**健康检查不检查任何东西——它返回一个字面量 `True`。**

README 把「/api/health 不因下游不可用而失败」列为稳定性设计，但被实现成了「永不失败」，于是它无法履行健康检查的职责。

⚠ **要区分**：`/api/status`（需鉴权）**确实**报告 mqtt/tv/llm/memory 四项连接状态。信息是有的，只是**不在 health 上**——而 health 才是监控探针会打的那个端点。

这是「监控在说谎」的终极版，比第四轮 V13（定时任务异常记成成功）更根本。

**修复建议**：health 至少带 scheduler + mqtt 两项；为保留 NAS「不硬失败」的软约束，可返回 200 但 `data.degraded = True`。

---

## 4. V32（P2，新发现）：scheduler 创建失败仍调 `sched.start()`

**我差点误判它，必须说明。**

代码里其实**已经有**一条专门保护（`app.py:737` 注释）：

```python
# P0-5: sched.start() 独立 try，避免作业注册失败连带调度器不启动
```

「add_job 失败不能连带调度器不启动」是**开发者明确考虑过的**，不是疏忽。

缺口在于**只想到一半**：`sched` 的创建和 `add_job` 在**同一个 try** 里。当创建本身失败（apscheduler 缺失、版本不兼容），`sched` 从未赋值，第二个 try 仍去 `start()`：

```
WARNING scheduler job registration failed: No module named 'apscheduler'
ERROR   scheduler start FAILED: local variable 'sched' referenced before assignment
UnboundLocalError ... app.py:741
```

**严重度我定 P2 而非 P1**：触发条件是「apscheduler 装不上」，生产环境罕见；且异常被兜住、应用继续启动。真实危害是「19 个定时任务全不起，对外只有一条 ERROR 日志」。

**修法**：`start()` 前判 `if sched is None: return`，并把失败写进 health（与 V33 配套）。反测已验证：失败路径不抛异常，成功路径仍能 start，**不误伤**。

---

## 5. 顺带实证的第二轮 P1

启动即撞上：

```
RuntimeError: Directory '/app/tts' does not exist
```

这是我第二轮靠读代码推断的 P1（`TTS_DIR` 不存在时应用导入即崩溃）。本轮在真实启动时**撞上了**——从静态推断升级为运行时确证。

可复现：`TTS_DIR=/tmp/definitely-not-exist` → 导入 `butler.app` 即 raise。

---

## 6. 我修正了自己的两处误判

| 项 | 我的初始判断 | 核实后 |
|---|---|---|
| V32 | "开发者没考虑注册失败"（P1） | **已考虑（P0-5 注释）**，缺的是创建失败 → 降 P2 |
| V33 正测 | `no_check = False`（判据失效） | 固定 260 字符窗口**越界到 status()**，改用 AST 函数边界后正确 |

第二处尤其典型：窗口越界让 `get_runtime` 混进判据，差点把这条 P0 判成不存在。

---

## 7. 边界（仍然要说）

1. **19 个任务只验证了"注册成功"，没验证"执行正确"** —— 真跑一轮需数小时。
2. af_bridge / HA / memory-agent 均**不可达**（192.168.2.200 是用户内网），相关降级路径已观察但未验证正常路径。
3. 覆盖度仍 **16.8%**：本轮只新读了 3 个文件。
4. 全仓库 7 failed 全部来自 `test_v25_pytest_shim.py` 与 deskpilot/tvpilot，**与本次修复无关**。

---

## 8. 当前缺陷清单（4 条已修）

**已修复（反测守护）**：V18 ✅ V23 ✅ V25 ✅ V28 ✅

**待修，按优先级**：

1. **V33**（P0，20 分钟）health 带 scheduler + mqtt ← **本轮新增，最该修**
2. **V30**（P1，1 行）`require_presence` 失败改保守跳过
3. **V29**（P1，20 分钟）写操作加锁或每线程独立连接
4. **V26**（P1，10 分钟）anomaly 补 `device_id`、删 `priority`
5. **V24**（P1，1 行）`await asyncio.to_thread(...)`
6. **V13**（P1，30 分钟）15 处 `.result(timeout=30)`
7. **V21 / V22 / V7 / V20 / P0-1 / P0-3**
8. **V32**（P2，3 行）`sched` 判空
9. **V31**（P3，1 分钟）删除 `butler/engine.py`
10. **TTS_DIR**（P1，1 行）启动前 `mkdir(exist_ok=True)`

---

## 9. 关于「是否达到正规审计要求」

**诚实回答：尚未。**

做得扎实的：反测 100% 覆盖、三态判定、4 条撤回都是我自己核实后推翻的、本轮修好了 4 条 P0。

**硬缺口**：

- **覆盖度 16.8%** —— 正规审计不应低于 60%
- **无威胁模型**、**无抽样方法**（我是被信号吸引过去的，不是随机采样）
- **未与维护者确认意图**（V15 的 `via="direct"` 可能是有意设计）
- 19 个定时任务**只验证注册，未验证执行**

但本轮补上了最关键的一块：**运行时验证从 0 到 1**。之前十六轮所有结论都建立在静态推断上，现在至少 lifespan、MQTT、定时任务注册是真跑过的——而且**一跑就炸出两条新缺陷**，证明这块确实不能省。
