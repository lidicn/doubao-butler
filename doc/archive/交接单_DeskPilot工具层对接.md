# DeskPilot（Windows 工具层）对接交接单 —— 致豆包管家开发者

> **交接方**：DeskPilot 项目（`e:\NAS\DeskPilot`，Windows 主机）
> **接收方**：豆包管家开发者（doubao-butler，192.168.2.200:8095）
> **日期**：2026-09-08
> **关联文档**：
> - DeskPilot：`docs/架构_v2_豆包管家Agent整合.md`、`docs/M1_阶段交付报告.md`、`docs/M3_阶段交付报告.md`
> - 管家：`doc/开发计划_轻量Agent框架_20260908.md`、同构参考 `doc/tvpilot_agent_handoff.md`
> - 同构项目：TVPilot（电视工具层，已对接，本交接单与其保持同一套契约）

---

## 一、一句话定位

**DeskPilot = 豆包管家的「Windows 手」，与 TVPilot 完全同构。**

```
用户自然语言 → 豆包管家 ReAct（脑）→ HTTP 调 DeskPilot 工具（手）→ 观察结果 → 继续/结束
```

DeskPilot **不做**意图理解、规划、记忆、对话；只负责"把 Windows 操作稳定地执行掉，并给出可判定的结构化结果"。
模板/技能统一归管家技能系统，DeskPilot 不存模板引擎。

**当前进度**：M0（清理双轨）+ M1（工具层规范）+ M3（可观测 + 技能挖掘）已交付，**M2（接入管家 ReAct 闭环）等管家侧就绪**——本交接单即为此而写。

---

## 二、DeskPilot 侧已完成什么

| 项 | 状态 |
|---|---|
| 统一响应包络 `{ok, tool, result, cost_ms, error}` | ✅ 全部 `/api/v1/*` 已收敛（与 TVPilot 一致） |
| 标准错误码 + Agent 重试策略矩阵 | ✅ 11 个错误码，机器可读（见 §3.2） |
| 结构化观察接口（状态查询类） | ✅ 系统状态/音量/窗口/会话/音乐/轨迹统计 |
| 危险操作二次确认闸 | ✅ 已开启（见 §5，重要） |
| 操作轨迹存档 `logs/ops.jsonl` + 查询/统计 API | ✅ 见 §6 |
| 技能草稿挖掘（从成功序列生成参数化草稿） | ✅ 见 §6 |
| 回归测试 | ✅ 502 passed / 0 failed |

---

## 三、契约（管家接入必须遵守）

### 3.1 统一响应包络

```jsonc
// 成功
{"ok": true,  "tool": "volume_set", "result": {"level": 40}, "cost_ms": 12, "error": null}
// 失败
{"ok": false, "tool": "ssh_exec",   "result": {},            "cost_ms": 30, "error": "session_required",
 "message": "没有可用的 SSH 会话，请先调用 POST /api/v1/ssh/connect"}
```

- **分支判断只看 `ok` / `error`，不要依赖 HTTP 状态码**（HTTP 状态码仍保留语义，便于网关监控）。
- `result` 失败时为 `{}` 而非 `null`，可直接取值。
- `cost_ms` 是服务端耗时，用于判断是否接近你的步数/超时预算。
- `message` 是人类可读补充，仅用于展示与人工排查。

### 3.2 错误码矩阵（请直接编码进你的重试策略）

| error | HTTP | 含义 | 建议 Agent 策略 |
|---|---|---|---|
| `invalid_parameter` | 400 / 422 | 参数不合法或校验失败 | 不重试，改参数 |
| `unauthorized` | 401 | Token 缺失/无效 | 补 Token 后重试 1 次 |
| `forbidden` | 403 | 被安全闸/白名单拒绝 | 不重试 |
| `not_found` | 404 | 目标不存在（窗口/会话/工单/插件） | 不重试 |
| `session_required` | 409 | 需要先建会话（SSH） | 先 connect 再重试 |
| `confirmation_required` | 409 | 危险操作未确认 | **向用户确认后带 `confirm=true` 重试** |
| `app_not_running` | 409 | 目标应用未运行（如 LX Music） | 先 launch 再重试 |
| `operation_timeout` | 504 | 操作超时 | 退避重试 1 次 |
| `unavailable` | 503 / 502 | 依赖不可达（设备/上游/远程主机） | 退避重试 ×3 |
| `not_implemented` | 501 | 能力未实现 | 不重试，告知用户 |
| `internal_error` | 500 | 服务端异常 | 重试 1 次，仍失败则上报 |

> 该表在代码里也有机器可读版本：`backend/core/tool_response.py` 的 `ERROR_RETRY_POLICY` / `ERROR_HTTP_STATUS`。
> 若你希望以 API 方式获取（如 `GET /api/v1/tools/schemas` 一并返回），告诉我，我加一个端点导出（见 Q2）。

### 3.3 鉴权与地址

| 项 | 值 |
|---|---|
| 局域网地址 | `http://192.168.2.201:8765` |
| Tailscale | `100.76.68.118:8765` |
| 鉴权 | `Authorization: Bearer <token>` 或 `X-API-Token: <token>` |
| 当前 Token | `deskpilot-dev-token-123456`（**开发默认值，生产需更换，见 Q7**） |
| Swagger | `http://192.168.2.201:8765/docs`（匿名可访问，含全部端点与参数） |
| 健康检查 | `GET /health`（免鉴权） |
| 认证失败 | 401 + `error: unauthorized`（不会执行任何操作） |

### 3.4 观察方式：结构化优先

每个动作都有对应的**状态查询工具**，Agent 执行后应调查询工具验证，而不是靠猜：

| 想确认 | 工具 |
|---|---|
| 系统状态（CPU/内存/磁盘/前台窗口） | `system_status` |
| 音量是否真的变了 | `volume_get` |
| 窗口是否真的激活/最大化 | `windows_list` |
| 音乐是否在播 | `lxmusic_status` |
| SSH 会话是否还在 | `ssh_sessions` / `ssh_session_info` |
| 最近到底执行了什么 | `traces_query` / `traces_stats` |

**快照（截图）当前未提供**（DeskPilot 无截图依赖）。若你的 ReAct 需要"关键节点截图兜底（width 480）"，告诉我，我加依赖实现（见 Q3）。

---

## 四、工具清单（建议首批注册）

> 完整清单见 Swagger `/docs`；下列为覆盖 4 个验收场景的最小集。
> 命名规则：`<模块>_<动作>`，长期稳定，可直接作为你的 tool name。

| tool 名 | 方法 + 路径 | 关键参数 | 典型错误码 |
|---|---|---|---|
| `system_status` | GET `/api/v1/system/status` | — | — |
| `system_notify` | POST `/api/v1/system/notify` | `title`, `message` | `unavailable` |
| `system_run` | POST `/api/v1/system/run` | `path`, `args?` | `internal_error` |
| `system_lock` ⚠️ | POST `/api/v1/system/lock` | `confirm` | `confirmation_required` |
| `system_shutdown` ⚠️ | POST `/api/v1/system/shutdown` | `delay?`, `confirm` | 同上 |
| `system_restart` ⚠️ | POST `/api/v1/system/restart` | `delay?`, `confirm` | 同上 |
| `system_sleep` ⚠️ | POST `/api/v1/system/sleep` | `confirm` | 同上 |
| `system_hibernate` ⚠️ | POST `/api/v1/system/hibernate` | `delay?`, `confirm` | 同上 |
| `volume_get` | GET `/api/v1/volume` | — | `unavailable` |
| `volume_set` | POST `/api/v1/volume` | `level`(0-100) | `unavailable` |
| `volume_step` | POST `/api/v1/volume/step` | `step`(±) | `unavailable` |
| `volume_toggle_mute` | POST `/api/v1/volume/mute` | — | `unavailable` |
| `windows_list` | GET `/api/v1/windows/list` | — | `unavailable` |
| `windows_activate` | POST `/api/v1/windows/activate` | `title` | `not_found` |
| `windows_close` | POST `/api/v1/windows/close` | `title` | `not_found` |
| `windows_minimize` | POST `/api/v1/windows/minimize` | `title` | `not_found` |
| `windows_maximize` | POST `/api/v1/windows/maximize` | `title` | `not_found` |
| `ssh_connect` | POST `/api/v1/ssh/connect` | `host`, `username`, `password?`, `port?` | `unavailable` / `operation_timeout` |
| `ssh_exec` | POST `/api/v1/ssh/exec` | `command`, `session_id?` | `session_required` |
| `ssh_sessions` | GET `/api/v1/ssh/sessions` | — | — |
| `lxmusic_status` | GET `/api/v1/lxmusic/status` | — | — |
| `lxmusic_play` / `lxmusic_pause` / `lxmusic_toggle` | POST `/api/v1/lxmusic/{play,pause,toggle}` | — | `app_not_running` |
| `lxmusic_play_by_keyword` | POST `/api/v1/lxmusic/play-by-keyword` | `keyword` | `app_not_running` |
| `agent_loop_tickets` | GET `/api/v1/agent-loop/tickets` | `agent?`, `status?` | — |
| `traces_query` | GET `/api/v1/traces` | `limit?`, `tool?`, `ok?`, `since?` | — |
| `traces_stats` | GET `/api/v1/traces/stats` | `limit?` | — |
| `skills_drafts` | GET `/api/v1/skills/drafts` | — | — |
| `skills_mine` | POST `/api/v1/skills/mine` | `min_support?`, `max_len?` | — |

⚠️ = 危险操作，必须带 `confirm: true`（见 §5）。

---

## 五、危险操作与二次确认（请务必实现，避免误锁屏/关机）

DeskPilot 主机上 `DESKPILOT_ENABLE_SYSTEM_OPS=1`（安全闸开启），**这些操作会真实执行**（已发生过一次联调误触发真实锁屏）。

因此我加了第二道闸：`system.dangerous_ops.require_confirm = true`。

```http
POST /api/v1/system/lock  {}
→ 409 {"ok": false, "tool": "system_lock", "error": "confirmation_required",
       "message": "该操作为危险操作，需显式携带 confirm=true"}

POST /api/v1/system/lock  {"confirm": true}
→ 200 {"ok": true, "tool": "system_lock", "result": {"method": "async"}}
```

**要求管家侧**：收到 `confirmation_required` 时，**先向用户确认**（语音/面板/微信均可），用户同意后再带 `confirm=true` 重试；用户拒绝则直接结束，不要再试。

---

## 六、轨迹存档 `logs/ops.jsonl` 与技能挖掘（M3）

### 6.1 ops.jsonl 是干什么的

它是 DeskPilot 的**操作审计流水**（与 TVPilot `data/ops.jsonl` 同构）：**每一次工具调用**都会在响应返回前自动追加一行 JSON，字段：

```json
{"ts":"2026-09-08T03:15:07","ts_epoch":1788808507.566,"tool":"volume_set",
 "method":"POST","path":"/api/v1/volume","params":{"level":40},
 "ok":true,"error":null,"cost_ms":3,"http_status":200}
```

三个用途：
1. **回放**：出问题时按时间窗口还原"当时到底调了什么、参数是什么、耗时多少"；
2. **观测**：统计每个工具的调用次数、成功率、平均耗时（`traces_stats`）；
3. **喂技能挖掘**：从**成功**序列里挖出可固化的技能草稿（§6.3）。

细节：敏感参数（`password`/`token`/`secret`/`api_key`/`cookie`）自动脱敏为 `***`；流式端点（SSE/WS）跳过；认证失败也记录（便于发现"哪个端点在报 401"）。

### 6.2 轮转（已按建议实现）

- 单文件超过 `system.audit.max_bytes`（默认 10MB）自动轮转为 `ops-<YYYYmmdd-HHMMSS>.jsonl`；
- 保留 `backup_count`（默认 5）个历史文件，超出删除最旧；
- 查询 API 默认连历史文件一起读（`include_rotated=true`），不会因轮转丢数据；
- 置 `max_bytes: 0` 可关闭轮转。

### 6.3 技能草稿（供你的技能沙箱导入）

`POST /api/v1/skills/mine` 从近期成功轨迹挖掘**重复出现的工具序列**，把变化的值参数化：

```json
{"name": "skill_windows_list_windows_activate", "support": 3,
 "steps": [{"tool":"windows_list","params":{}},
           {"tool":"windows_activate","params":{"title":"{title}"}}],
 "params": ["title"]}
```

按架构 §P3：**DeskPilot 只产草稿，是否入库由你的技能沙箱决定**（我不会自动生成可执行技能）。

---

## 七、联调验收场景（M2）

| # | 场景 | 期望工具序列 |
|---|---|---|
| 1 | "打开微信并最大化" | `system_run`(path) → `windows_list` → `windows_activate` → `windows_maximize` → `windows_list` 验证 |
| 2 | "把音量调到 40，然后静音" | `volume_set`{40} → `volume_get` 验证 → `volume_toggle_mute` → `volume_get` 验证 |
| 3 | "帮我 SSH 看一下 NAS 磁盘" | `ssh_connect` → `ssh_exec`{df -h} → 结果回灌（若未建会话应先收 `session_required` 并自动 connect 重试） |
| 4 | 失败恢复 | 注入超时/未建会话 → 按 §3.2 矩阵重试或换路径，不得死循环 |

判定：4 个场景端到端跑通 + `traces_query` 能看到完整工具序列（可回放）。

---

## 八、需要你回复的问题（编号回答即可）

| # | 问题 | 为什么问 |
|---|---|---|
| **Q1** | 管家侧 ReAct 闭环（含观察器、错误码重试、轨迹落库）**预计何时就绪**？能否给一个可联调的时间窗口？ | M2 完全依赖此项，我据此排期 |
| **Q2** | 工具注册需要什么格式的 schema？OpenAI function calling JSON？还是你们自有格式？**要不要我加一个 `GET /api/v1/tools/schemas` 端点自动导出全部工具的 schema**（含错误码与重试策略）？ | 避免我手写的清单与你家解析器对不上 |
| **Q3** | 观察是否**需要截图兜底**（架构 §3.3 建议 width 480）？如果需要，我引入 `mss`（最轻）或 `pillow` 实现 `screen_snapshot`；不需要我就跳过 | 决定是否新增依赖 |
| **Q4** | `confirmation_required` 你们打算如何向用户确认？（小爱 TTS / 面板 / 微信 / 其他）需要我在错误里额外返回什么字段（如确认话术模板）吗？ | 影响 UX 闭环，也影响我要不要在响应里带话术 |
| **Q5** | 技能沙箱希望草稿是什么结构？我现在的字段是 `{name, support, steps:[{tool,params}], params}` —— 需要补 `id`/`version`/`trigger`/`description` 吗？参数化占位符用 `{title}` 可以吗？ | 保证草稿能被直接导入 |
| **Q6** | 轨迹（agent_traces）由**你定时拉** `GET /api/v1/traces`，还是希望我**主动推**（Webhook / MQTT）？若推送，目标地址与格式？ | 决定要不要做推送通道 |
| **Q7** | 生产 Token：现在的 `deskpilot-dev-token-123456` 是开发默认值。**生产请你们给一个 token 由我配到 `config/deskpilot.yaml`**，还是我生成后给你们？ | 安全项，避免开发 token 进生产 |
| **Q8** | 管家部署在 NAS（192.168.2.200），DeskPilot 在 Windows（192.168.2.201），**网络可达性你们是否有约束**（防火墙/固定 IP/DHCP 保留）？需要我改用 Tailscale（100.76.68.118）吗？ | 联调连通性 |

---

## 九、已知风险与待办（DeskPilot 侧）

| 项 | 说明 |
|---|---|
| 前端仍兼容旧包络 | `frontend/src/api/http.js` 保留 `{code,message,data}` 兜底分支，**约定 M2 联调跑通后清理** |
| 快照观察 | 未实现，待 Q3 确认 |
| 挖掘算法 | 启发式 n-gram（非 PrefixSpan），轨迹规模变大后可升级 |
| 主机休眠/关机 | 若管家在夜间调用，注意 Windows 睡眠策略已设为 `standby-timeout=0`、屏保关闭 |
| 单实例 | DeskPilot 目前单机单实例，不做并发编排 |

---

## 十、回复方式

在本文件**同目录下新建** `回执_DeskPilot工具层对接.md`（沿用 `回执_MA对接_成员档案与在场查询.md` 的格式），按 Q1–Q8 逐条回复即可；有额外要求也一并写明。

我这边收到回执后：
1. 按 Q2/Q3/Q5 补齐 schema 导出 / 截图 / 草稿字段；
2. 按 Q1 的时间窗口进入 §七 的 4 个场景联调；
3. 联调通过后清理前端旧包络兜底分支，并出 M2 交付报告。
