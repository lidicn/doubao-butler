# doubao-butler 第十九轮审计报告 · multisource dict-list + webhook & memory_routes

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮主线**：① `multisource` 扩展 dict-list（连续三轮承诺，本轮兑现）② 按第十八轮"优先挖外围"策略，深挖 `api/doubao_webhook.py`(849) + `api/memory_routes.py`(468)
- **结论**：**1 个 P1 + 4 个 P2**

---

## 第一部分：工作流迭代（v2.2 → v2.3）

### 1.1 `multisource` 支持 dict-list（连续三轮承诺，本轮兑现）

第十五轮 P1-43 的 `DEFAULT_ROLES` 是 `[{id:..., name:...}, ...]` 形态，而 `multisource` 只解 `ast.Constant` 元素 → **整类漏报**。第十六轮起连续三轮列为"下一步"，本轮实现。

**方法**：
1. 若 list 元素**全为 dict** → 求所有 dict 共有的、值为纯字符串的键
2. 按优先级 `id > name > key > code > slug > role > member` 选身份字段
3. 用该字段的值构成序列参与比对

**结果**：14 对 → **18 对**（新增 4 对）：

```
[0.833] butler/api/memory_routes.py:329 <inline>    ≈ butler/roles/store.py:70 DEFAULT_ROLES   A仅['gu_anheng']
[0.833] butler/memory/extractor.py:265  <inline>    ≈ butler/roles/store.py:70 DEFAULT_ROLES   A仅['gu_anheng']
[0.833] butler/memory/feeder.py:22      FEEDABLE_ROLES ≈ butler/roles/store.py:70 DEFAULT_ROLES A仅['gu_anheng']
[0.714] butler/defaults.py:10           DEFAULT_TRIGGERS ≈ butler/triggers/defaults.py:10 ...
```

**前 3 对正是第十五轮 P1-43 的人工结论**——同一条结论，人工发现于第十五轮、工具自动复现于第十九轮。第 4 对 `butler/defaults.py` 是已知孤儿（P0-15），一致性交叉验证。

### 1.2 v2.3 实测

| 阶段 | 结果 |
|---|---|
| **multisource** | **18 对（high 10）** ← 14→18 |
| httpcontract | 16 处 |
| lifecycle | 11 处 high |
| atomicity | 35 处（high 33） |
| secrets | 36 处硬编码 / 8 处不可用环境变量覆盖 |
| kwcontract / falsyzero / timeunit | 4 / 15 / 10 |

---

## 第二部分：第十九轮审计发现

### P1-48 `execute_device_command` 完全不检查 HTTP 状态码 —— 设备控制失败被当成成功

- **位置**：`butler/api/doubao_webhook.py:291-441`

```python
await client.post(f"{settings.ha_url}/api/services/{service}", json=payload, headers=headers)
# ↑ 响应被丢弃，共 7 处，无一处赋值/检查
```

#### 实锤

```
execute_device_command 中 client.post 调用数: 7
赋值给变量的:            0
检查 status_code:        False
调用 raise_for_status:   False
```

#### 对照实验（同文件内的两种写法）

| HA 返回 | `query_device_state`（198 行，**正确**） | `execute_device_command`（291 行） |
|---|---|---|
| 200 | 正常解析 | success |
| **401** | `查询失败：401` | **success** ❌ |
| **404** | `查询失败：404` | **success** ❌ |
| **500** | `查询失败：500` | **success** ❌ |

**同一个文件里两种写法**——这证明是遗漏而非设计决策。

#### 后果链（三重）

1. **用户无感知**：在豆包 APP 说"打开客厅主灯"，HA token 过期/实体不存在 → 灯不亮，管家**不报错、不 Bark 告警**（Bark 只在 `except` 分支推，而 HTTP 错误不抛异常）
2. **账本污染**：`else` 分支记录 `{"success": True}` 到自进化学习数据 → **失败样本被当成功样本训练**
3. **返回值谎报**：`handle_webhook` 最终返回 `{"ok": True, "device_commands": N}`，API 层面完全正常

这是豆包 APP 设备控制的**唯一路径**（`execute_device_command` 全仓仅 webhook:688 一处调用）。

#### 与第九轮 P0-14 的关系

P0-14 是"出站 HTTP 从不检查状态码"（deskpilot/tvpilot）。本轮是**同一缺陷的第三处实例**——`httpcontract` 阶段抓到了 16 处，但**这 7 处不在其中**。

**工具盲区**：`httpcontract` 的判据是"解析响应前未检查状态码"，需要存在 `r.json()`。而此处**连响应体都不读**，所以完全逃逸。

**修复**：改为调 `rt.ha.call_service()`（ha.py:233 已正确检查并返回错误串），或至少 `if r.status_code >= 400: raise`。

---

### P2-21 独立 SQLite 连接确认为 **11 个**（更新第十二/十三轮统计）

`lifecycle` 阶段列出 11 个 high，逐文件核对 `synchronous` 配置：

| 文件 | connect | synchronous=NORMAL |
|---|---|---|
| `store/db.py`（参考实现） | 1 | ✅ |
| **`store/task_store.py`** | 1 | ✅ ← 本轮新确认 |
| `core/decision_store.py` | 1 | ❌ |
| `core/notification_store.py` | 1 | ❌ |
| `core/pwa_chat_store.py` | 1 | ❌ |
| `core/user_location.py` | 1 | ❌ |
| `ha_tools/whitelist.py` | 1 | ❌ |
| `proactive/engine.py` | 1 | ❌ |
| `store/write_failures.py` | 1 | ❌ |
| `timeseries/store.py` | 1 | ❌ |
| `presence/store.py`（实例属性） | — | ❌ |

**第十一、十二轮的"9 个" → 第十三轮"至少 10 个" → 本轮确认 11 个**，其中仅 2 个配置了 `synchronous=NORMAL`。

### P2-22 `/app/data` 硬编码 22 处 → DATA_DIR 修改会造成数据分裂

`secrets` 阶段报 36 处硬编码地址，其中 `"/app/data"` **22 处**（`config.py` 的 3 处是 `_env` 默认值，其余 19 处为纯硬编码）。

```
走 data_dir（可配）:  config.json / butler.db / roles/roles.json
硬编码（不可配）:     sessions.json / role_state.json / device_aliases.json
                     fast_routes.json / skills/user/* / butler.db(self_evolve)
```

**当前无实际影响**：`docker-compose.yml:66` 挂载 `.../data:/app/data`，且未设 `DATA_DIR`，两者指向同一处。

**风险**：用户若按 README 改 `DATA_DIR` 做数据迁移，会以为全部迁移完成，实际上述文件仍写 `/app/data`（容器内路径，若未挂卷则容器重建即丢失）。属部署陷阱。

### P2-23 `DEVICE_MAP` 是硬编码设备表（第三份真源）

`doubao_webhook.py:74-81` 硬编码 7 个设备名 → entity_id 映射（显示器挂灯/书房空调/客厅主灯/客厅电视/客厅风扇/主卧室空调/房间床头灯）。

与 `devices.py` 的种子表**不冲突**（后者是音箱），但属**互补的两份真源且无法同步**：WebUI 改设备不会更新 `DEVICE_MAP`，反之亦然。

### P2-24 `save_user_page` 非原子写

`atomicity` 命中 `doubao_webhook.py:285`：`A1-裸写 + A4-无 fsync`。豆包生成的 HTML 页面写入时若中断/并发 → 文件损坏。

---

## 第三部分：`memory_routes.py` 深挖结论（468 行）

**结论：无功能性缺陷。**

| 检查项 | 结果 |
|---|---|
| handler 数 / 注册 | **18 / 18 全部注册**，无遗漏 |
| 重复定义 | **无**（与 `skill_routes.py` 的 `skill_mock_test` 重复形成对照） |
| 鉴权 | 每个 handler 首行 `g = guard(request)` |
| 批量审核阈值 | `approve: confidence >= 0.7` / `reject: confidence <= 0.6`，中间 0.6–0.7 留白给人工 —— **设计合理** |
| `confidence` NULL 风险 | 建表 `REAL DEFAULT 0.5`，两条写入路径（`memory_routes:130`、`extractor:265`）均保证非 NULL —— **无三值逻辑陷阱** |
| `target_role` 白名单 | 与 `feeder.py` / `extractor.py` 一致（6 角色），与 `roles/store.py`（5 角色）漂移 —— **即第十五轮 P1-43，本轮由 multisource 自动复现** |

**`DEVICE_MANUAL`（378 行）** 599 字符的投喂 prompt，无缺陷。

---

## 第四部分：本轮**排除**的误报

| 疑似 | 核查结论 |
|---|---|
| `handle_webhook` 的 `_recent_msgs` 泄漏 | ❌ **正确**：R2-08 设计——占位在 471 行，仅 `unknown_conversation`（482 行）时 `del` 允许重试；其余 `ok=True` 路径保留 hash 符合预期 |
| webhook 鉴权 fail-open | ❌ **正确**：WO-ME-203 已改为 fail-closed，三通道（Authorization / X-Webhook-Token / ?token=）均用 `hmac.compare_digest`；未配置时返回 503 而非放行 |
| `DEVICE_MAP` vs `devices.py` 冲突 | ⚪ 互补而非冲突（灯/空调 vs 音箱），定 P2-23 而非 P1 |
| `/app/data` 硬编码导致当前故障 | ⚪ 默认 compose 下两者同路径，**无实际故障**，定 P2（部署陷阱） |
| `facts_bulk_approve/reject` 阈值重叠 | ❌ 0.6–0.7 留白是**刻意的人工审核灰区**，非缺陷 |

---

## 第五部分：工作流现状与局限

### v2.3 已解决

| v2.2 短板 | v2.3 状态 |
|---|---|
| **multisource 只覆盖字符串序列**（连续三轮未兑现） | ✅ dict-list，14→18，自动复现第十五轮 P1-43 |

### 本轮暴露的新盲区

**`httpcontract` 抓不到"完全不读响应体"的情况**——判据依赖 `r.json()` 存在。本轮 P1-48 的 7 处全部逃逸。

> 这是工具迭代五轮以来第一次出现"新缺陷落在已有阶段职责范围内却未被捕获"。说明各阶段的**判据边界**需要定期用已知缺陷做回归。

### 仍存在的短板

1. **`httpcontract` 需要增加"响应完全丢弃"判据**（`await client.post(...)` 结果未赋值）
2. `multisource` 仍只覆盖**字面量**序列，设备表这类 dict 值映射（P2-23）仍靠人工
3. C 类（逻辑错误）仍空白
4. 代码深挖 ~35%（本轮新增约 1320 行）

---

## 第六部分：累计与下一轮计划

### 十九轮累计新增（本轮）

| 编号 | 结论 |
|---|---|
| **V45** | **`execute_device_command` 7 处 POST 全丢响应，401/404/500 一律视为成功；且污染自进化学习数据** |
| V46 | 独立 SQLite 连接确认为 **11 个**（仅 2 个配 synchronous=NORMAL） |
| V47 | `/app/data` 硬编码 22 处，改 DATA_DIR 会造成数据分裂（当前无实际故障） |
| V48 | `DEVICE_MAP` 硬编码设备表，与 WebUI 设备管理无法同步 |
| V49 | `save_user_page` 非原子写 |

### 修复优先级

| 优先级 | 项 | 成本 |
|---|---|---|
| **立刻** | **P1-48 `execute_device_command` 检查状态码**（改调 `rt.ha.call_service`） | 低 |
| 立刻 | P1-44/46/47 三个 kwarg 误用 | 各 1 行 |
| 立刻 | P0-15 删 6 个孤儿文件 | 极低 |
| 本周 | P2-14/15/17 排序键 / temperature / status() 时钟 | 各 1 行 |
| 本周 | P2-21 收敛 SQLite 连接工厂 | 中 |
| 排期 | P2-22/23/24 硬编码与原子写 | 中 |

### 第二十轮计划

- **目标模块**：`mcp/server.py`(640) + `proactive/engine.py`(564)
- **工作流**：
  1. **`httpcontract` 增加"响应完全丢弃"判据**（本轮暴露的盲区，最高优先级）
  2. 建立**已知缺陷回归集**：把 V1–V49 中可静态判定的条目做成 fixture，每次改阶段判据后跑回归，防止判据收紧误伤
  3. `multisource` 扩展到 dict 值映射（覆盖 DEVICE_MAP 类）

---

## 附：本轮的两个方法论收获

**其一，兑现承诺比新增阶段更有价值。** `multisource` dict-list 拖了三轮才做，做完后一次性复现了第十五轮的人工结论。说明"工具能力缺口"会持续产生"人工重复劳动"，而补齐的收益是**复利**的。

**其二，判据边界需要回归测试。** 本轮 P1-48 落在 `httpcontract` 的职责范围内却未被捕获——因为该阶段的判据建立在"会读响应体"的隐含假设上。工具迭代五轮以来首次出现这类逃逸，提示我：**阶段数量增长后，每个阶段的有效性会随代码形态变化而衰减**，需要定期用历史缺陷做回归验证。
