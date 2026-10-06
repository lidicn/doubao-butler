# doubao-butler 第七轮审计：账本化与未读区攻坚

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮方法：**先完善工作流（跨轮账本 + 覆盖度追踪），再按 S2 地图攻坚 ★ 未读文件**
> 交付：新增 **V22**；工作流新增 `ledger.py` 账本层；套件 32 条；全仓库 **7 failed / 617 passed**

---

## 0. 本轮核心结论

1. **V22（P1）**：`doubao_webhook.handle_webhook` 的去重占位符**没有 finally 保护**——异常路径泄漏，导致用户消息因瞬时故障被静默丢弃 5 分钟。这是**"意图写进了注释，但机制没有落地"**的典型。
2. **工作流升级为可跨轮复算**：新增 `ledger.py`，用指纹去重产出 NEW / KNOWN / RESOLVED 三态，**首次让"这一轮比上一轮多/少了什么"可计算**。
3. **两处诚实的负面结论**：`api/deps.py`、`api/doubao_webhook.py` 的 HTTP 与鉴权比我预期健壮（`raise_for_status`、状态码判断、原子写 + 0600 权限都在）。**没有缺陷也是结论**，但 V22 恰是在这种"整体不错"的文件里找到的。

---

## 1. 工作流完善

### 1.1 新增跨轮账本（`scripts/ledger.py`）

前六轮的痛点：**每轮重新扫，无法回答"这轮新增了什么"**。现在每条 finding 生成指纹（`文件:行号:标题前24字` 的 md5 前 10 位），跨轮比对：

```
NEW      172 条   ← 本轮首次出现
KNOWN      0 条   ← 历史已知仍在
RESOLVED   0 条   ← 历史有、本轮消失（= 可能已修复）
```

**踩过的坑（已修）**：单独跑 `--stages 5` 时 S1/S3 未执行、findings 为空，会把全部历史条目误判成 RESOLVED，一次刷出 172 条假信号。已改为**仅当 S1/S3 真正执行且 findings 非空才写账本**。

### 1.2 覆盖度追踪

账本记录 21 个已读文件，S5 输出审计覆盖度。六轮下来我逐行读过的仅 4,173 / 41,431 行 ≈ **10.1%**。现在这个数字不再靠人工估算，而是随每轮推进自动更新。

### 1.3 INV-5（副作用守恒）重写 —— 结论是"高噪声"

把它改成真正的分支展开（if/else、try/except、return/raise 各自成路径）后：

- 初版命中 **34 条**
- 加守卫子句过滤后 **0 条**（过严，漏掉真信号）
- 放宽副作用关键词后 **5 条**，逐条核实 → **全部是守卫式提前 return**，0 真阳性

**判定：INV-5 作为自动发现器失败，降级为"候选生成器，需人工分诊"，不再作为自动发现依据。**

这个负面结果和第六轮证伪"CC=缺陷"是同类教训：**检测器本身也需要被证伪**。已写进参考库。

### 1.4 其他修复

- `abspath` 防御（相对路径让 radon 静默返回 0 样本）
- `vulture` 的 `rc=3` 是"有发现"不是失败
- INV-5 重写时误删 INV-6/INV-7，已恢复
- 沙箱重置导致 vulture/radon/ruff/pytest 全丢，已重装

---

## 2. V22（P1）：去重占位符无 finally 保护

**位置**：`butler/api/doubao_webhook.py:472`

```python
_recent_msgs[msg_hash] = now   # 临时占位（防并发），处理完刷新时间
...
# L484 注释：R2-08: hash 已临时占位，处理成功则保留；下面任意 return 错误前需删 hash
```

**问题**：这个"下面任意 return 错误前需删 hash"的纪律**只写在注释里，没有任何机制保证**。函数体 `handle_webhook`（CC=62，约 400 行）**没有外层 try/except/finally**。

后果链：

1. L472 占位写入 → 2. 后续任一环节抛异常（`repo.add_turn` 失败、`rt.roles.get` 抛错、LLM 超时）→ 3. 占位符**永不删除** → 4. doubao2api 的重推被 L468 判为 `duplicate` 静默丢弃 → 5. **该消息 300 秒内彻底无法处理**，用户完全无感知。

已知的正确做法就在同一个函数里：`if not role_id:` 分支（L481）和失败分支（L482）都手动 `del`。**但那是显式 return 路径**——异常路径覆盖不到。

讽刺之处：开发者在 L457 明确写了"`_recent_msgs` 那道内存 hash 门重启即失效、只挡 5 分钟，挡不住重推"，说明**知道这道门的不可靠性**，却没给占位符加释放保护。

**修复**（3 行）：把 L472 之后到函数末尾包进 `try: ... finally: 若未成功则 _recent_msgs.pop(msg_hash, None)`。

---

## 3. 本轮审计的未读文件（含负面结论）

| 文件 | 结果 |
|---|---|
| `api/deps.py`（枢纽 42） | **无新缺陷**。会话文件 tmp+os.replace 原子写 + `os.chmod 0o600`；bearer 限流（`bearer_locked` / `note_bearer_failure`）逻辑正常 |
| `api/doubao_webhook.py`（CC 62） | **HTTP 处理比预期健壮**：L650 `raise_for_status()` 后再 `.json()`，L208 有状态码判断，设备控制失败有 Bark 推送 + self_evolution 记录。**但发现 V22** |
| `core/cron_task.py` | L676 `run_coroutine_threadsafe` 未取 result（**V13 形状在新文件的延伸**，非新缺陷类型）；L480 `now_local = now() + tz_offset` 未使用，且 **UTC+8 硬编码** |

**`cron_task.py:480` 值得单列**：`dt.datetime.now() + timedelta(hours=8)` 是硬编码时区偏移，且结果是死变量。虽然当前无害，但**同一文件里若别处用了它就会在冬令时/跨时区部署出错**。列 P3。

---

## 4. 本轮未解决

- S2 地图仍有 **8 个 ★ 未读文件**（`core/agent.py`、`skills/runner.py`、`decision/aggregator.py`、`audiobook/manager.py`、`bus/mqtt_client.py`、`tools/registry.py` 等）。本轮读了 3 个。
- **完整 lifespan、真实 MQTT、SQLite 并发——七轮均未覆盖。**
- INV-5 需要重新设计才能产生真信号（当前 0 真阳性）。

---

## 5. 修复优先级（累计更新）

1. **V18（P0，5 分钟）**——`docker_tools` async 内 `time.sleep` + 缺 import
2. **V13（P1，30 分钟）**——15 处 `.result(timeout=30)`，含 `cron_task.py:676`
3. **V21（P1，3 分钟）**——`fast_routes` 损坏回退内置
4. **V22（P1，10 分钟）**——`handle_webhook` 占位符加 finally（**本轮新增**）
5. **V7 / T-1（P0，20 分钟）**——`dialog.speak` 提前 return
6. **V20（P1，20 分钟）**——4 处原子写
7. **P0-1 + P0-3（25 分钟）**——技能确认 `role`、config `logger`

---

## 6. 七轮的元结论

| 轮次 | 方法 | 新缺陷 | 证伪 / 撤回 |
|---|---|---|---|
| 1–3 | 读代码 + 推断 | 14 | 2 条撤回 |
| 4 | 套件做发现 | 4 | 1 条降级 |
| 5 | 工具链扫描 | 3 | 1 条误判 |
| 6 | 工作流驱动 | 2 | **1 条假设证伪**（CC≠缺陷） |
| **7** | **账本 + 未读区攻坚** | **1** | **1 个检测器被证伪**（INV-5 高噪声） |

本轮新增可能只有 1 条，但**工作流从此具备跨轮复算能力**——这是七轮里第一次能回答"这轮到底进步了什么"。

同时必须说清：七轮累计覆盖率仍只有 ~10%，**"未发现"不等于"不存在"**。V22 恰恰是在一个"整体写得不错"的文件里找到的，说明剩余盲区的风险并未随轮次线性下降。
