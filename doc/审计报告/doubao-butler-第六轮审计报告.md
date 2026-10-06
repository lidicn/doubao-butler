# doubao-butler 第六轮审计报告 · 潜藏深处的 bug

> **编号说明**：上一份报告已占用"第五轮"，本份接续为**第六轮**，避免同名覆盖。

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮方向（指定）**：**潜藏深处的 bug** —— 被上层 bug 掩盖的下层 bug、单点清理的隐式依赖、跨模块属性/参数名的静默错配
- **方法**：AST 语义扫描 + `symtable` 符号表分析 + 最小复现脚本 + 属性存在性交叉验证
- **结论**：**1 个 P0（双重叠加，含一个被掩盖的第二层 bug）+ 3 个 P1 + 3 个 P2**
- **本轮重要更正**：上一轮提出的 `creator` UnboundLocalError **经复核不成立，已撤回**（详见第四节）

---

## 一、P0 级缺陷

### P0-10 `af_bridge._announce` 双重衔接断裂：AF 协作通道 100% 失效，且会吃掉用户正常对话

- **位置**：`butler/af_bridge.py:170-180`（`_announce`）

这是本轮最"深"的一处：**两个 bug 叠在同一段代码里，第一个把第二个完全掩盖了。**

#### 第一层：`devices.by_room()` 方法根本不存在

```python
# af_bridge.py:170-178
room_devs = devices.by_room(room) if hasattr(devices, "by_room") else []      # L170
if not room_devs:
    logger.warning("af_bridge: no device in room=%s, fallback to living room", room)
    room_devs = devices.by_room("客厅") if hasattr(devices, "by_room") else []  # L173

for dev in room_devs:                                                          # L175
    await _rt.tts.speak(prompt, device=dev)                                    # L176
```

**交叉验证**：`DeviceRegistry`（`butler/devices.py:181-200`）只定义了 `all()` / `get()` / `resolve()` —— **没有 `by_room`**。全仓 grep `by_room` 除 `af_bridge.py` 这两处外，无任何定义。

→ `hasattr` 检查成立 → 短路为 `[]` → fallback 同样 `[]` → **L175 的 `for` 循环零次执行** → `_announce` 静默返回。

**后果**：**每一条 AF ask 都播不出来**（不是偶发，是 100%）。唯一的痕迹是 L172 一条 `warning`。

#### 第二层：`tts.speak(device=dev)` 参数名错误 —— 被第一层掩盖

```python
# butler/tts/manager.py:183-189
async def speak(self, text: str, *, voice=None, device_id: str | None = None, ...)
```

- 实际参数名是 **`device_id`**，而 `af_bridge.py:176` 传的是 **`device=`** → `TypeError: speak() got an unexpected keyword argument 'device'`
- 且 `device=dev` 传的是 `Device` **对象**，而 `device_id` 期望 `str` —— **参数名和类型双错**
- 对比：`tts/adapter.py:62` 用的是正确的 `device_id=device_id`

**这层的隐蔽性**：因为 L175 循环永远不执行，L176 **从未被执行过**，所以这个 TypeError **从未暴露**。一旦有人修好第一层（补上 `by_room` 或改用 `resolve`），**立刻 TypeError 炸出来**——典型的"修一个 bug 冒出下一个"。

#### 合成后果：用户正常对话被静默吃掉

```python
# af_bridge.py:126-131 (_poll_once)
for ask in asks:
    if aid not in known_ids:
        _pending[aid] = {...}      # ← 先写入 pending
        await _announce(ask)       # ← 再播报（此时已 100% 静默失败）
```

`_pending[aid]` **已被写入**，但用户从没听到这条 ask。此后：

```python
# af_bridge.py:194 (on_user_reply，由 dialog.on_wakeup 调用)
for aid, info in list(_pending.items()):
    if info["room"] and info["room"] != room: continue
    if time.time() - info["prompt_ts"] > _answer_window: ...   # 900s 才超时
    → 用户说的任何话都被当作「对这条从未播出的 ask 的回答」
    → 调 _deliver_answer 投递给 AF
```

→ 在 **900 秒（`_answer_window`）内，该房间所有正常对话都被当作 AF ask 的答案投递出去，用户得不到管家回复**。

**修复方案**（两层一起修）：

```python
# af_bridge.py —— 改用真实存在的 resolve()，并按房间过滤
async def _announce(ask: dict) -> None:
    room = ask.get("room", "")
    prompt = ask.get("prompt", "")
    if not _rt or not prompt:
        return
    devices = getattr(_rt, "devices", None)
    if devices is None:
        logger.warning("af_bridge: no devices manager"); return

    # ✅ 用真实存在的 resolve()：room="*" 不过滤，先取全部再按房间筛
    all_devs = devices.resolve(list(getattr(devices, "devices", {}) or {}), room=None)
    room_devs = [d for d in all_devs if d.room == room] or \
                [d for d in all_devs if d.room == "客厅"]

    ok = False
    for dev in room_devs:
        try:
            # ✅ 参数名 device_id，且传 str
            await _rt.tts.speak(prompt, device_id=dev.id)
            logger.info("af_bridge: spoke to %s", dev.id); ok = True; break
        except Exception as e:
            logger.warning("af_bridge tts failed on %s: %s", dev.id, e)

    if not ok:
        # ✅ 播报失败必须回写状态，避免 _pending 里的"幽灵 ask"吃掉后续对话
        logger.error("AF_ASK_ANNOUNCE_FAILED room=%s prompt=%.40s", room, prompt)
        raise RuntimeError("announce failed")   # 让 _poll_once 不要写入 _pending
```

```python
# _poll_once 相应改为：先播报成功再登记
for ask in asks:
    aid = ask["ask_id"]
    if aid in known_ids: continue
    try:
        await _announce(ask)
    except Exception as e:
        logger.error("af_bridge announce failed, ask NOT registered: %s", e)
        continue                       # ← 不写入 _pending
    _pending[aid] = {...}
```

> **建议补一条启动自检**：`af_bridge.start()` 时断言 `hasattr(devices, "by_room")` 或直接改为静态调用 `resolve()`。用 `hasattr` 做能力探测，会把"方法名写错"从**启动即崩**降级成**永远静默**——本例就是这个代价。

---

## 二、P1 级缺陷

### P1-21 回声抑制是全局单一时间戳，不分设备，且连续播报会无限延长窗口

- **位置**：`butler/core/dialog.py:533`（设置）、`:148`（`on_user_utterance` 读取）、`:241`（`on_wakeup` 读取）

```python
self._echo_until = time.time() + max(8.0, len(text) * 0.4 + 6.0)   # 约 8s 起，长文本更久
```

- **问题 1（不分设备）**：`_echo_until` 是**一个**全局时间戳。管家在客厅音箱播报后，**书房、卧室音箱的用户输入同样被丢弃**（L148/L241 无 `source_device` 差别判断）。
- **问题 2（窗口叠加延长）**：连续多条播报（如简报逐条播）会不断把 `_echo_until` 推后（若是 `max()` 语义则取最远），形成**长时间静默窗**。
- **后果**：家庭成员在别的房间对音箱说话 → 被判为"回声"直接丢弃 → **管家不回应，且只有 info 级日志**。
- **修复**：改为 per-device 字典 `self._echo_until_by_dev: dict[str, float]`，按 `source_device` 判定：

```python
def _suppressed(self, dev_id: str) -> bool:
    return time.time() < self._echo_until_by_dev.get(dev_id or "_", 0.0)
```

---

### P1-22 技能草稿 pending 过期后，普通闲聊被当作技能描述喂进生成器

- **位置**：`butler/core/dialog.py:328-359`
- **机制**（本轮重新精确确认）：
  - L328 `if _pending_exp:` 命中已过期草稿 → L329-332 提示过期并 `del` → **没有 return**
  - 控制流落到 L359 `if creator:` —— 而 `creator` 在 **L264 已无条件赋值**（`creator = getattr(rt, "skill_creator", None)`），故**不抛 NameError**（见第四节更正）
  - → 进入 `generate_skill_from_description(message)`，把用户刚说的"今天天气怎么样"当成技能描述
- **后果**：LLM 生成一份语义荒谬的技能草稿；用户得到"我给你写了个技能草案…"的回复，**真实意图被完全吞掉**；返回带 `skill_create: True`，上层无法区分。
- **修复**：L332 的 `del` 之后补 `return`（或 `continue`）。

```python
if 已过期:
    del self._pending_skill_desc[role_id]
    return {..., "skill_create_expired": True}   # ← 补 return
```

---

### P1-23 16 处 `except Exception: continue` 静默吞异常，其中 2 处直接吞掉整个唤醒流程

- **位置**：`xiaomi_ear.py:90,100`（**最关键**）、`cron_task.py:87,493`、`event_stream.py:149,182,192`、`ha.py:533,601`、`anomaly.py:201`、`proactive/engine.py:524`、`schedule.py:32,42`、`failure_learn.py:70`、`self_evolve.py:47`、`wakeup.py:31`

`xiaomi_ear._handle_event` 的结构是 `try: ... except Exception: continue`，而它内部 **L176/201/207 裸调用 `dialog.on_wakeup(...)`，自身无 try**。

→ `on_wakeup` 抛出的任何异常（含 P1-22 之外的路径、TTS 异常、LLM 异常）都在 `xiaomi_ear` 这层被 `continue` 吞掉，**没有任何 warning/error 日志**。

→ 用户侧：对小爱说话，**完全无响应，日志里什么都没有**。

**修复**：至少在 `xiaomi_ear._handle_event` 的 `except` 里加 `logger.exception(...)`；`on_wakeup` 调用点加 try 并回一句兜底语音。

```python
except Exception:
    logger.exception("xiaomi_ear handle_event failed (event=%s)", entity_id)   # ← 至少留痕
    continue
```

---

## 三、P2 级隐患

| # | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| P2-18 | `dialog.py:446-451` | `set_state(SPEAKING)` 后若 `speak_as_role` 抛异常，L450/451 的 `set_state(WAITING)` 与 `_return_idle()` 不执行 → **状态永久卡 SPEAKING**；`on_wakeup`/`on_tv_voice` **无状态守卫**（不校验当前状态），也无看门狗恢复 | 用 `try/finally` 包裹发声段，finally 中恢复状态；或加超时看门狗。**注**：已核查状态仅用于 WebUI 展示、无门禁依赖，故定 P2 而非 P1 |
| P2-19 | `core/state.py:52-59` + `dialog.py:84-85` | `mark_absent` **唯一**调用点是 TV 上报 `offline`（依赖 TV 侧 LWT）。若 TV 正常关机不发 offline，或 Arcface 误识别产生新名字，在场人员**永久累积** | 增加 TTL：超过 N 分钟无人脸刷新即 `mark_absent` |
| P2-20 | 全局 12+ 处 `datetime.now()`（naive） | 正确性依赖 `docker-compose.yml:25` 的 `TZ=Asia/Shanghai`；与 `agent.py:86`、`decision/engine.py:117` 的 `ZoneInfo` aware 写法**并存**。已核查**无直接比较**（不会 TypeError），但**去掉 TZ 环境变量即静默错 8 小时** | 统一改用 `datetime.now(ZoneInfo("Asia/Shanghai"))`，或在 README 明确 TZ 为硬依赖 |

---

## 四、重要更正：撤回上一轮的 `creator` UnboundLocalError

上一轮分析中我曾判定 `dialog.py:359` 的 `if creator:` 会抛 `UnboundLocalError`（理由是 `creator` 只在 `else` 分支赋值）。**经复核，该判断错误，撤回。**

```python
# dialog.py:263-264 —— 在 if _pending_exp 之前，8 空格缩进，无条件执行
# 技能草稿确认流程：如果有待确认草稿，优先处理确认/修改/取消
creator = getattr(rt, "skill_creator", None)     # ← 无条件赋值
```

`creator` 在 L264 已绑定，L359 引用时**必然有值**，不会抛 `NameError`/`UnboundLocalError`。

**正确的行为**是 P1-22：控制流穿透到 L359，因 `creator` 非空而**进入技能生成流程**，把普通闲聊误当技能描述。也就是说，**第一轮 P1-1 的原始描述（"漏 return 导致闲聊被喂进生成器"）才是正确的**，请以那条为准，不要按"变量未定义"去改。

> 这条更正很重要：若按错误结论去"补一个 `creator = None` 初始化"，问题**不会消失反而更隐蔽**（会静默跳过生成流程，掩盖真实的穿透路径）。

---

## 五、已复核并**排除**的疑似问题（避免误改）

| 疑似项 | 核查结论 |
|---|---|
| `tts/queue.py:162-163` 免打扰跨午夜判断 | **正确**。L163 源码实为 `not (m >= start or m < end)`（有括号），`ast.unparse` 去括号后看着像优先级错误。实测 23:00→07:00 各时段均正确 |
| naive/aware datetime 直接比较 → TypeError | **无**。aware 与 naive 各在独立分支（如 `aggregator.py:41/43/45` 为 mock/正常/fallback 三选一），不存在同表达式比较 |
| 除零 / 空集合 `max()` | 已核查 `performance.py`、`quarantine.py` 等除法点均有守卫 |
| `asyncio.Lock` 重入 | `bark.py:142/184`、`ask.py:45/61` 均为**误报**——两处是各自独立的 `httpx.AsyncClient`，非嵌套加锁 |
| 作者 ⛔ 纪律标注 | **全部遵守**：`repo.py` 读失败直接抛不折 0、`ledger_freshness.py` 空账本返回 `None`、`inbox.py` 台账写失败可数不静默、`memory_agent.py` 缺凭据抛 `CredentialMissing`、`af_bridge.py` 投递后立刻 pop、`db.py` 不打断启动。调用方 `system_routes.py:50`、`health_api.py:177` 均按纪律带 `read_error` 标签 |
| 循环内 lambda 晚绑定 | 0 处 |
| 浮点 `==` 比较 | 0 处 |

---

## 六、本轮方法论备注

P0-10 这类 bug 的特征值得记录，它们**不会被任何静态扫描器发现**：

1. **属性名/方法名错配**（`by_room` 不存在）—— 类型检查器能发现，本项目无 mypy 且 `hasattr` 把错误降级为静默
2. **参数名错配**（`device=` vs `device_id=`）—— 运行时才炸，且**被第一层掩盖，从未执行到**
3. **`hasattr` 能力探测的代价** —— 让"方法名写错"从启动崩溃变成永久静默

**建议的最小改动**：把 `af_bridge.py:170/173` 的 `hasattr` 探测改为直接调用 `devices.resolve(...)`，让错误在启动/首次调用时**立刻显式失败**，而不是无限期静默。

---

## 七、本轮审计局限

1. P0-10 的两处断裂基于**静态交叉验证**（`DeviceRegistry` 无 `by_room` 定义、`TTSManager.speak` 签名为 `device_id`），**未做运行时复现**（沙盒缺 AF 服务与小爱设备）。其中第一层必然触发，第二层因被掩盖而从未执行。
2. P1-21 的回声抑制"连续播报无限延长"基于 `max()` 语义推断，未确认 `_echo_until` 是否在别处被重置。
3. P2-19 的"TV 不发 offline 导致在场累积"取决于 TV 端 LWT 可靠性，**无法从本仓库确认**。
4. 未通读 `mcp/server.py`、`agent_collab.py`、`modes/*` 的深层逻辑。
5. 0 值陷阱扫描命中 21 处，本轮仅甄别了 `tts/queue.py:162-163`、`memory_agent.py:466/489` 等少数几处，其余未逐一确认。
6. 未运行测试与集成验证。
7. 本报告**不重复**第一至五轮已列条目，六轮需合并阅读。
