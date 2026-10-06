# doubao-butler 第十一轮审计：旁路形状扩散

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮方法：**继续用 `propagate.py` 深挖第二个形状 `direct-speak`（V15 同类扩散）**
> 交付：新增 **V26（P1）**；套件 **38 条用例**；全仓库 7 failed / 623 passed（新增 2 passed，无回归）
> 工作流迭代：形状库状态追踪 + 报告产出机制

---

## 0. 本轮核心结论

**V26（P1）：`timeseries/anomaly.py:371` 的 critical 告警 TTS 双重失效——永远不播报，且只留一条 warning 日志。**

这是**扩散排查第二次兑现价值**：V15 原本只是一条"via 只是日志标签"的观察，本轮用它的形状搜出 27 处直接发声点，在其中找到了这条**参数名错配 + 缺 device_id** 的硬缺陷。

值得强调的是它的性质：**异常检测是系统里最需要出声的告警**（漏水、设备异常），而它恰好是唯一一个连"合成后能否播放"都没走通的调用点。

---

## 1. V26（P1）：critical 告警永不播报

**调用点** `butler/timeseries/anomaly.py:371`：

```python
await self.rt.tts.speak(
    text=f"异常检测：{first_msg}",
    priority="critical",          # ← ① 不存在的参数
)
# except Exception as e: logger.warning("anomaly critical tts failed: %s", e)
```

### 失效一：参数名错配（V2 同形状）

`TTSManager.speak` 真实签名（实测 `inspect.signature`）：

```
(text, *, voice=None, device_id=None, member='', backend=None,
 speed=None, nowvoice_voice=None, trace_id='', via='direct')
```

**没有 `priority` 参数。** 绑定结果：

```
TypeError: got an unexpected keyword argument 'priority'
```

而调用点外层是 `except Exception: logger.warning(...)`——**TypeError 被吞成一条 warning**，没有任何 ERROR 级日志，也不重试。

### 失效二：缺 `device_id`，即便修好参数也播不出

`manager.py:210` 的播放门：

```python
if device_id and self.ha is not None:
    await self.queue.enqueue(device_id, res)
```

`device_id` 为 None → 跳过 → **只合成、不播放**，函数照样 `return res`（非 None），调用方无从察觉。

**两条独立致死。** 这解释了为什么一个家庭管家最关键的告警通道会静默——用户从来不会看到任何异常提示。

### 对照组：其他 4 处旁路是好的

同为 `via="direct"` 的 5 处业务旁路中，另外 4 处**都传了 `device_id`**，签名绑定通过：

| 调用点 | device_id | 绑定 |
|---|---|---|
| `proactive/engine.py:321` | `tts_device or "xiao_living"` | ✅ |
| `morning/routine.py:207` | `"xiao_touch8"` | ✅ |
| `notifier/router.py:82` | `req.device_id`（有前置判断） | ✅ |
| `af_bridge.py:177` | `device=dev` ← **参数名错**，但被 V1 短路掩盖 | ✗（已被 V1/V2 覆盖） |
| **`anomaly.py:371`** | **无** | **✗ 本轮新增** |

---

## 2. `direct-speak` 形状全貌（27 处）

排查器命中 27 处直接发声。逐类核验后：

- **5 处业务旁路**（V15 已知）：proactive / anomaly / af_bridge / morning / notifier
- **`dialog.speak_as_role`（L625）**：本身就是直接分发路径——它不进 `TTSQueue`，直接 `resolve → _emit_devices`。这是**设计如此**，但意味着所有走 `speak_as_role` 的播报（含 `cron_task.py:380`、`briefing.py:223` 的 fallback）都**不带队列的过载/静默/去重保护**
- **`role_routes` / `action_router` / `runner` / `registry`**：均为 `notify_message`（Bark 文字推送），非 TTS 出声，不适用队列保护

**结论**：真正需要关注的是 `speak_as_role` 这条腿——它绕过了队列的全部保护，而项目自己的注释（`manager.py:192`）承认"via 只进日志"。这不是新缺陷，是 V15 的**范围确认**：绕过点比原先记录的 5 处更多。

---

## 3. 工作流迭代

### 3.1 形状库状态追踪

`propagate.py` 现在标注每个形状的排查状态，避免重复劳动：

| 形状 | 源 | 状态 |
|---|---|---|
| `new-loop` | V23 | ✅ 已深挖（第十轮，坐实 V24） |
| **`direct-speak`** | **V15** | **✅ 已深挖（本轮，坐实 V26）** |
| `success-err` | V13 | ⬜ 未排查 |
| `blocking` | V18 | ⬜ 未排查 |
| `nonatomic` | T-2/V20 | 🔶 部分（V20 已覆盖 4 处） |

**剩余 3 个形状未系统性排查**——这是 R12+ 最直接的产出来源。

### 3.2 一个写作纪律

本轮注册 V26 时，正测/反测的**展示字符串里嵌了引号**导致 `SyntaxError`，整个套件收集失败。这类"展示文本"看似不影响断言，但会让**全部 38 条用例无法运行**。

已修正，并记入纪律：**展示字符串与断言逻辑同等重要——它挂掉等于整个套件挂掉。**

---

## 4. 本轮的诚实边界

- **V26 是静态 + 签名绑定验证，不是真跑 TTS**。没有合成音频、没有实际调用 HA。结论建立在"签名不接受 `priority`"和"播放门要求 `device_id` 非空"两个可验证事实之上，这两条是硬的。
- **未核实 `af_bridge.py:177` 的 `device=dev` 是否已修**——它被 V1（`by_room` 不存在）短路掩盖，修好 V1 当天会炸。仍属未修。
- **完整 lifespan、真实 MQTT、SQLite 并发运行时复现 —— 十一轮零进展。**

---

## 5. 修复优先级（累计更新）

1. **V23（P0，5 分钟）**——`_run_coro` 改同 loop await（唯一能挂死进程）
2. **V18（P0，5 分钟）**——`docker_tools` async 内 `time.sleep` + 缺 import
3. **V25（P0，15 分钟）**——SQLite 发布顺序 + 写操作加锁
4. **V26（P1，10 分钟）**——anomaly 补 `device_id`、删 `priority`；建议改走 `enqueue_tts(priority=1)` ← **本轮新增**
5. **V24（P1，1 行）**——`await asyncio.to_thread(...)`
6. **V13（P1，30 分钟）**——15 处 `.result(timeout=30)`
7. **V21（P1，3 分钟）**——`fast_routes` 损坏回退内置
8. **V22（P1，10 分钟）**——`handle_webhook` 占位符加 finally
9. **V7 / T-1（P0，20 分钟）**——`dialog.speak` 提前 return
10. **V20（P1，20 分钟）**——4 处原子写
11. **P0-1 + P0-3（25 分钟）**——技能确认 `role`、config `logger`

---

## 6. 十一轮元结论

| 轮次 | 方法 | 新缺陷 | 证伪 |
|---|---|---|---|
| 8 | 快速核心扫描 | 1（P0） | 性能数字更正 |
| 9 | 混合聚焦 | 0 | 性能数字更正 |
| **10** | **扩散 new-loop** | **2** | 1 候选证伪 + 1 修法被反测推翻 |
| **11** | **扩散 direct-speak** | **1** | — |

**连续两轮证明：产出递减不是"缺陷挖完"，而是方法触顶。** 每换一个扩散形状就重新产出。

这也说明前九轮的方法有个系统性盲点——我们一直在**发现新形状**，却很少**把已知形状搜到底**。现在 5 个形状里还有 3 个没挖（`success-err` / `blocking` / `nonatomic`）。

### 修复清单现状（必须说清）

累计 **4 个 P0 + 11 个 P1**，**零修复**。审计产出若不落地，再跑多少轮都是纸上功夫。

按当前优先级，前 3 条（V23 / V18 / V25）合计约 **25 分钟**就能做完，且都是"静默失效、用户天天遇到但永不报障"的类型。
