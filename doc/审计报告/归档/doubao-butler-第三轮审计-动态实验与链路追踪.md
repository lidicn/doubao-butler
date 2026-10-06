# doubao-butler 第三轮审计：动态实验与链路追踪

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮方法：**安装 `review-audit` skill（证据驱动，未经证据不得判定 PASS）+ 在沙箱里对运行时行为做受控动态实验**
> 与前两轮的关系：第一轮静态阅读、第二轮跑测试套件；**本轮直接构造输入驱动真实代码路径，观察实际行为**
> 本轮原则（沿用 review-audit）：**每一条判定都必须附上可复现的实验输出，无法验证的一律列入「未验证」而非猜测**

---

## 0. 本轮核心结论

**六个受控实验全部跑通，产出 4 条新缺陷 + 2 条修正，其中 1 条 P0、5 条 P1。**

最重要的一条是本轮新发现的 **P0：`dialog.speak` 走队列后提前 return，绕过了防重复、对话落库与 SSE 推送**——这意味着**项目的核心防重复机制（README 明确列为"关键设计"）在主动播报路径上根本不工作**。这不是代码写错了某一行，而是**两条发声路径分叉后，其中一条把整套副作用全丢了**。

同时必须修正第一轮的两处判断：

- **P1-8（时长估算导致截断）——予以降级**。实测证明队列的 `estimate_ms` 只用于遥测字段，不控制停止时机，播放停止实际用 dialog 侧更长的估算值，截断风险不成立。
- **我自己上一轮扫描的「15 处 open 未用 with」——是误报，予以撤回**。AST 扫描未判断 `open()` 是否位于 `with` 内，核验后全部为假阳性。

---

## 1. Skill 安装与使用

本轮新增安装 **`review-audit`**（`dualform-labs/review-audit`，Apache-2.0，单提示文件零依赖）→ `/data/workspace/skills/review-audit/SKILL.md`（70 行）。

选用理由：它的核心纪律正是本轮需要的——**"未经证据不得判定，'我没检查'是一等输出，未检查的维度不得计入结论"**，且明确要求"回归维度必须有真实运行的 exit code，接线维度必须有具体 grep 或 file:line"。这与前三轮"读代码猜"的局限直接互补。

实际执行方式：按该 skill 的 four-axes 精神，本轮对 **稳定性** 与 **功能性** 两个轴做深度验证，每个轴都给出"实验输入 → 实际输出 → 判定"，未验证的轴（wiring 全量接线、spec 合规性）如实列入第 6 节。

---

## 2. 受控动态实验（全部已复现）

### 实验 1：TTS 熔断对**成功播放**计数 → 确认 P1

```
连续 7 次成功播放后：breaker_active=True，冷却 25s
冷却期间：普通消息 dropped_cooldown（丢弃）；P1 告警 accepted=queued
```

**判定：确认 P1-2 成立，且比第一轮描述更严重。** 熔断的本意是"下游故障扩散时止损"，但 `tts/queue.py:376` 的 `_note_trigger(now, "playback")` 在 `dequeue()` 里**无条件调用，不看播放成败**。于是**正常对话 7 条 → 管家静默 25 秒**。防故障的机制自己在制造故障。

**修复**：`play_one()` 内改为"成功清零、失败才计数"，并把默认阈值调整为「5 次失败 / 30s 窗口 / 冷却 20s」，冷却期放行 P1 与 P2（只丢 P3+ 闲聊）。

---

### 实验 2：过载保护**拒绝并丢弃 P1 告警** → 确认 P0（并升级）

```
19 条 P3 后入队 1 条 P1：accepted=False, reason=overload_reset
队列被整体清空，暂停 600s
暂停期间 5 次播放：全部 0 条播出
```

**判定：P0-4 成立，且比第一轮描述更严重——不只是"已入队的 P1 被清空"，而是 P1 **根本入不了队**（`tts/queue.py:328-341` 的过载分支在插入之前执行，直接返回 `REASON_OVERLOAD_RESET`，连"保留但不播"都没有）。**

一个家庭管家，在设备异常刷屏时**连"漏水了"都发不出去**，且此后 600 秒全面静默。这已经超出"过载保护"的合理边界。

**修复**：过载时按优先级裁剪（只丢 P3+，保 P1/P2），暂停缩短到 60s，过载事件本身走 Bark 文字推送兜底（当前只有 TTS 播报，等于"过载这件事自己也说不出口"）。

---

### 实验 3：`override_quiet=True` 完全绕过夜间静默 → 确认 P1（新）

```
凌晨 2 点（quiet_active=True）时入队：accepted=queued
```

`dialog.py:670` 对**所有主动播报**硬编码 `override_quiet=True`。实测确认：静默窗口形同虚设。

四个硬编码调用点：

| 位置 | 场景 | 是否应豁免静默 |
|---|---|---|
| `app.py:730` | P1 安全提醒 | ✅ 应当（告警本就豁免） |
| `briefing.py:214` | 早/晚报（priority=2） | ❌ 不当——晚报在静默窗内也会出声 |
| `tts_routes.py:412` | API 手动入队 | ⚠️ 视调用方而定 |
| `dialog.py:670` | **全部主动播报** | ❌ **不当——这是覆盖面最大的一处** |

**修复**：`dialog.speak` 的 `override_quiet` 改为透传调用方意图，默认 `False`；仅安全告警传 `True`。

---

### 实验 4：trigger 冷却状态文件**非原子写** → 重复播报风暴（新，P1）

这是本轮唯一一个"用一次进程被杀就必然发生"的缺陷，已完整复现：

```
落盘后文件大小: 81 bytes
（模拟写一半被 kill）
load cooldowns failed: Expecting ',' delimiter: line 1 column 21 (char 20)
损坏文件加载后 _last_fired = {}
→ 冷却记录数: 0（原为 2）
★ 结论：冷却全丢且只留一条 logger.warning，所有 trigger 将在重启后再次触发
```

**根因链**（`butler/triggers/engine.py`）：

```python
# 第 81 行：直接往目标路径写，没有 tmp + os.replace
json.dump(self._last_fired, open(self._cooldown_file, 'w', encoding='utf-8'), ...)

# 第 72-73 行：加载失败只 warning，然后静默带着"零冷却"继续跑
except Exception as e:
    logger.warning("load cooldowns failed: %s", e)
```

三步合成一个完整故障：① 非原子写 → ② 进程被 kill（`docker restart` / OOM / 断电）时文件截断 → ③ 重启后加载失败被吞成一条 warning，冷却表**静默归零** → 所有 trigger 立刻再触发一次。

**这正是本项目的系统性病灶**：异常被降级成"记一条日志然后带着空状态继续跑"，看起来一切正常，实际防护已经失效。而且它与第一轮的 P0-3（config 损坏）是**同一个病**：都是"状态文件损坏 → 降级路径静默 → 防护消失"。

对比：项目里 `config_routes.py:31-35`、`skills/store.py:158`、`triggers/store.py:90` **都实现了 tmp + os.replace 原子写**，`deps.py:56` 会话文件也是。**唯独 trigger 冷却文件这个最需要耐久的漏了**——不一致。

**修复**：

```python
def _save_cooldowns(self):
    if not self._cooldown_file: return
    tmp = self._cooldown_file + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._last_fired, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, self._cooldown_file)
    except Exception as e:
        logger.error("save cooldowns failed: %s", e)   # ★ 升级为 error

def _load_cooldowns(self):
    ...
    except Exception as e:
        # ★ 损坏文件必须留证 + 显式标记防护失效，不能静默归零
        logger.error("cooldowns corrupt, ALL triggers will re-fire: %s", e)
        os.replace(self._cooldown_file, self._cooldown_file + ".corrupt")
        self._degraded = True
```

---

### 实验 5：两条发声路径分叉，队列保护近乎空转（新，P1）

追踪全部发声入口后的接线事实：

| 入口 | 路径 | 过 TTSQueue？ |
|---|---|---|
| `on_wakeup` → `speak_as_role` → `_emit_devices` | **主对话（唤醒回复）** | ❌ 不过 |
| `dialog.speak`（主动播报） | `enqueue_tts` | ✅ 过 |
| `inbox` speak 通道 / `briefing` / 安全监控 / notify router | `enqueue_tts` | ✅ 过 |

**判定**：`TTSQueue` 只服务主动播报这一类，**用户唤醒管家说话这条最高频路径完全不经过队列**。后果是——过载保护、熔断、夜间静默、去重、TTL、优先级**对主对话路径全部无效**。队列里那套精心设计（569 行）的保护，在最重要的场景上空转。

这也解释了实验 3 的危害为何被放大：静默保护本来就只覆盖一半路径，而覆盖到的那一半又被 `override_quiet=True` 关掉了。

---

### 实验 6：`dialog.speak` 提前 return，丢失防重复 / 落库 / SSE（新，**P0**）

看 `butler/core/dialog.py:661-730` 的实际控制流：

```python
async def speak(self, text, member, *, source="proactive", ...):
    if use_queue:
        ...
        queued = enqueue_tts(...)
        if queued:
            return {"spoken": True, "engine": "queue", ...}   # ★ 第 672 行提前 return

    # ↓↓↓ 以下全部被跳过 ↓↓↓
    if source == "proactive":
        dup, sim = await self.dedup.is_duplicate(member, text)   # 第 677 行
    ...
    await self.dedup.record(member, text)                        # 第 710 行
    await asyncio.to_thread(repo.add_turn, ...)                  # 第 716 行
    self.state.note_speak(member)                                # 第 720 行
    self.state.add_turn(...)                                     # 第 721 行
    self._publish_dialog({...})                                  # 第 723 行 ← SSE 推送
```

而 `use_queue` 默认为 `True`，且 `enqueue_tts` 返回 `EnqueueResult.accepted`（bool），只要入队成功就为真 → **这条提前 return 是常态路径，不是异常分支**。

**四个真实后果**：

1. **防重复机制失效**：`dedup.is_duplicate`（判重）与 `dedup.record`（写指纹）都在 return 之后。README 把 "bigram Jaccard 防重复" 列为关键设计，但在主动播报路径上**既不判重也不写指纹**——同一句关怀可以在一天内反复播，而指纹表因为没有 `record` 而永远学不会"这句话说过了"。
2. **对话流水缺失**：`repo.add_turn` 不执行 → 主动播报不进 `dialog_turns` 表 → 记忆抽取（每天 03:15 分析最近 7 天对话）**看到的对话史是残缺的**，进而影响人格与记忆质量。
3. **WebUI 不刷新**：`_publish_dialog` 不执行 → SSE 订阅者收不到事件。
4. **状态机不推进**：`state.note_speak` / `add_turn` / `set_state` 全跳过。

**修复**（把副作用从"队列成功"里解耦——入队成功只代表"排上了"，不代表"播完了"）：

```python
if use_queue:
    queued = enqueue_tts(...)
    if queued:
        # ★ 入队成功也要写指纹、落库、推 SSE（播出结果由队列侧回调补记）
        await self.dedup.record(member, text)
        await asyncio.to_thread(repo.add_turn, member, "butler", text,
                                engine="queue", source=source, target=room or "queued")
        self._publish_dialog({"type": "speak", "member": member, "text": text,
                              "engine": "queue", "source": source, "ts": time.time()})
        return {"spoken": True, "engine": "queue", "queued": True}
```

判重（`is_duplicate`）应在**入队之前**做——否则判重永远来不及拦住重复播报：

```python
if source == "proactive":
    dup, sim = await self.dedup.is_duplicate(member, text)
    if dup:
        return {"spoken": False, "reason": "dedup", "similarity": sim}
```

---

## 3. 对前两轮的修正（诚实记录）

### 3.1 撤回：第一轮 P1-8「时长估算不一致导致尾部截断」

实测数据：

| 字数 | queue 估算 | dialog 估算 | 差值 |
|---|---|---|---|
| 10 | 4.0s | 3.2s | queue 更长 |
| 30 | 8.0s | 9.6s | dialog 更长 |
| 60 | 14.0s | 19.2s | dialog 更长 |

更关键的是**用途核查**：`tts/queue.py:279-280` 的 `estimate_ms` 只写进 `item.duration_ms` 遥测字段，**不参与任何播放/停止决策**；控制 `media_stop` 时机的是 `dialog._est_speak_secs`（0.32s/字），它比 queue 的 0.2s/字**更大、更安全**。

**判定：截断风险不成立，从 P1 降为 P3（仅保留"两处口径应统一"的可维护性建议）。** 第一轮我推断了用途而未核查，这是错的。

### 3.2 撤回：上一轮我自己的「15 处 open 未用 with」

我的 AST 扫描把 `open()` 调用一律标记为"未用 with"，但未判断它是否位于 `with` 语句内。核验结果：

```
feeder.py 第 53 行的 open 在 with 内 → 我的扫描是误报
feeder.py 第 46 行的 open 在 with 内 → 我的扫描是误报
```

`cron_task.py:98/110/522` 同样已核实为 `with open`。**该项整体撤回，不做为缺陷计入。**

（但请注意：第 2 节实验 4 的 `triggers/engine.py:81` 是**真实**的无 `with` 写入——那一条是逐行读代码确认的，不是扫描得出的。）

### 3.3 保留并强化：第二轮 N-2（DeskPilot 不校验状态码）

本轮扫描补充了全仓分布：**`return r.json()` 共 12 处**，而已有 `status_code`/`raise_for_status` 检查 38 处。说明团队知道该做，只是漏了这 12 处。维持 P1。

---

## 4. 附带核查（无重大问题，供参考）

| 项 | 结论 |
|---|---|
| **端点鉴权** | 260 个注册端点中 8 个无显式 `guard`，均为有意豁免（health/version/webhook 自带 token、queue_enqueue 内部调用）。**无问题** |
| **死循环风险** | `while True` 无 sleep 的忙循环：**0 处**。**无问题** |
| **裸 except** | `except:` 裸捕获：**0 处**。**无问题** |
| **LLM 客户端** | `integrations/llm.py` 已实现指数退避重试（429/500/502/503/504）、超时、`raise_for_status`。**质量良好**；小瑕疵：`chat()` 返回的耗时恒为 0，遥测失真 |
| **命令注入** | 全仓唯一 `shell=True` 出现在 `quarantine.py:43` 的**隔离黑名单字符串**中（非真实执行）；`docker_tools` 全程列表参数 + 白名单。**无风险** |
| **原子写覆盖** | 已实现：`config_routes`、`deps` 会话、`skills/store`、`triggers/store`；**未实现**：`triggers/engine` 冷却文件、`push_routes.py:152`、`fast_routes.py:44`、`feeder.py:54`（后三者影响面小，建议一并统一） |

---

## 5. 三轮累计：稳定性与功能性缺陷总表

| ID | 等级 | 缺陷 | 轮次 | 状态 |
|---|---|---|---|---|
| **T-1** | **P0** | `dialog.speak` 提前 return → 防重复/落库/SSE 全丢 | 三轮 | 🆕 实测确认 |
| P0-4 | **P0** | 过载丢弃 P1 + 600s 静默 | 一轮→三轮 | ✅ 升级（P1 根本入不了队） |
| P0-1 | **P0** | 技能确认 `role` 未绑定 → `UnboundLocalError` | 一轮 | 待修 |
| P0-3 | **P0** | config 损坏 → `NameError` → 全站 500 | 一轮→二轮 | ✅ 动态复现 |
| P0-2 | **P0** | SQLite 单连接跨线程 | 一轮 | 待修 |
| **T-2** | **P1** | trigger 冷却非原子写 + 静默归零 → 触发风暴 | 三轮 | 🆕 实测确认 |
| **T-3** | **P1** | 主对话路径绕过 TTSQueue，保护空转 | 三轮 | 🆕 实测确认 |
| **T-4** | **P1** | `override_quiet=True` 使夜间静默失效 | 三轮 | 🆕 实测确认 |
| P1-2 | **P1** | 熔断对成功播放计数 → 正常对话静默 25s | 一轮→三轮 | ✅ 实测确认 |
| N-1 | **P1** | `TTS_DIR` 不存在 → 导入期崩溃 | 二轮 | 待修 |
| N-2 | **P1** | DeskPilot/TVPilot 不校验状态码 | 二轮 | 待修 |
| N-3 | **P1** | 15 个硬编码内网 URL | 二轮 | 待修 |
| P1-1 | **P1** | 过期 pending 误入技能生成器 | 一轮 | 待修 |
| P1-4 | **P1** | 16 个定时任务异常静默（Future 未取回） | 一轮 | 待修 |
| P1-7 | **P1** | 同步 SQLite 阻塞事件循环（93 处调用点） | 一轮 | 待修 |
| ~~P1-8~~ | ~~P1~~ | 时长估算截断 | 一轮 | ❌ **撤回（降 P3）** |
| ~~P1-9~~ | ~~P1~~ | 登录无限流 | 一轮 | ❌ **撤回（已实现）** |
| ~~open×15~~ | — | open 未用 with | 二轮 | ❌ **撤回（扫描误报）** |

**累计：4 个 P0（1 新）+ 10 个 P1（3 新）**，另有 2 条撤回、1 条降级。

---

## 6. 未验证声明（按 review-audit 纪律）

以下轴本轮**未做验证**，不得计入任何"通过"结论：

- **接线（wiring）全量轴**：只追踪了发声链路，`skills/`、`api/` 其余模块的"定义了但从未被调用"未做全量 grep 计数；
- **回归轴**：未执行完整 `pytest`（第二轮已执行：17 failed / 576 passed，本轮未重跑）；
- **完整 lifespan 运行**：未启动真实 MQTT 连接、16 个定时任务、presence 轮询——三轮均未覆盖，故"应用可用"仅指装配阶段；
- **外部依赖真实链路**：沙箱出站 HTTP 被代理策略全面拦截（`policy_default_denied`），HA / 小爱 / Bark / memory-agent 的真实交互未验证；
- **`speak_as_role` 的并发行为**：未做多线程/协程并发压测，实验 5 的判定基于静态接线追踪，非动态压测。

---

## 7. 建议的修复顺序（按投入产出比）

1. **T-1（P0，约 20 分钟）**——`dialog.speak` 把 `dedup.record` / `add_turn` / `_publish_dialog` 提到 return 之前，判重提到入队之前。**这是唯一一条"核心设计宣称存在但实际不工作"的缺陷**，且用户每天都在遭遇（重复播报）却永不报障。
2. **P0-1 + P0-3（约 25 分钟）**——技能确认 1 行、config logger 5 行，都是"静默失效"类。
3. **T-2（P1，约 15 分钟）**——冷却文件改原子写 + 损坏时显式 error 而非静默归零。**改动小，但消除的是"重启即重复播报风暴"这类最伤用户体验的故障。**
4. **T-4 + P1-2 + P0-4（约 1 小时）**——TTS 三件套：`override_quiet` 透传、熔断只计失败、过载按优先级裁剪。这三条共同决定"管家会不会在不该说话时说话、在该说话时不说话"。
5. **T-3（P1，架构）**——主对话路径收编进 TTSQueue。改动面较大，建议排在前面四项之后单独一个迭代。
