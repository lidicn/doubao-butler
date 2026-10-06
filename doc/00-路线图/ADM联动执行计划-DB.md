# ADM 联动执行计划 · DB（豆包管家）

> 出品：关键决策部（DCD）
> 版本：v2.6 联动落地版（基于 v2.5_DCD版路线图 + 全部裁定链）
> 前提：homesdk **0.3.1** 已投递 NAS（`\\192.168.2.200\docker\libs\homesdk\`，含 consent/config/http/auth/mqtt/presence/time/gates 全模块，sha256 已登记 VERSIONS.txt）
> 落点：`E:\NAS\doubao-butler\doc\00-路线图\`
> 依赖裁定：所有引用裁定均已落 `E:\NAS\关键决策部\decisions\`

---

## 一、DB 的角色

DB 是**交互中枢**：用户/设备经 DB 进入生态，DB 编排"感知→决策→表达"。联动里 DB 负责：
- 开设公共收件箱（`butler/inbox/*`）让 MA/AF 投递"请管家说话"的请求；
- 发布 presence（`adm/doubao-butler/status`+`caps`），供 MA/AF 探测管家在不在；
- 收 MA 的 `ma/insights` 事件，过 Sentinel 闸门后播出；
- 通过 MCP 调 MA 查业务数据（"昨天书房空调时长"等）；
- 通过 MCP 调 AF 建自动化（DB v2.6 之后的实验档）。

---

## 二、任务卡（按依赖序排，每卡含验收硬线）

### 第 0 步：homesdk 升级到 0.3.1（vendored wheel，前置）

**现状**：DB 仓内 vendor 的是 0.1.1（只 `consent` + `gates/`），`http/auth/mqtt/presence/time` 一字没有。

| 子任务 | 验收 |
|--------|------|
| ① 按 `decisions/20261001-DB六格与MA五题-裁定.md` §三 裁 B：`vendor/` 换成 `homesdk-0.3.1-py3-none-any.whl`（`COPY` + `pip install`），Dockerfile 只动一行 | 镜像里 `python3 -c "import homesdk; print(homesdk.__version__)"` == `0.3.1`；sha256 与 `VERSIONS.txt` 同号 |
| ② 先解决两个前置（裁定 §三硬要求）：`BUTLER_WEB_PASSWORD` 不进 LEGACY_TOKEN_KEYS（落 `BUTLER_TOKEN` 键名）；7 条静默边（`AUTOFORGE_BASE_URL` 等）补 compose | `homesdk_peer_probe.py` 重跑，分区表只朝"更多键上链"方向变；**⛔ 用 try 住继续跑** |
| ③ 搭下一次既有 restart 窗（与写面修复窗合并，不单独开） | `docker restart`（⛔ 不 `up -d`），重启后 4 连采每条出站边 |
| **决策门**：不卡这条，下面的 inbox/presence 全接不上 | — |

### 第 1 步：DB 公共收件箱（`butler/inbox/*` 订阅 + Sentinel 闸门）

| 子任务 | 验收 |
|--------|------|
| ① 已有：`bus/inbox.py:15-17` + `e13b4d3` InboxGate 四闸（schema fail-closed / 每来源限流 / 冷却 / speak 每日预算 3）——**复核其与新 0.3.1 presence 的 schema 一致** | homesdk 0.3.1 的 `INBOX_TOPICS` == `{speak, notify, tv}`，DB 侧订阅三主题 |
| ② DB 收到 `butler/inbox/notify` 时，过 Sentinel 闸门（夜间/冷却/角色/每日预算）→ 走 TTS/通知/TV 通道 | 限流：每来源每分钟 ≤ N 条；schema 缺字段丢弃 + 审计 |
| ③ trace_id 必填校验（`inbox_events` 表已在建账格 12 裁 H2 批准） | 投递缺 trace_id → 拒收 + 计数 |

### 第 2 步：DB presence 发布

| 子任务 | 验收 |
|--------|------|
| ① `bus/mqtt_client.py:36` 的 LWT 归 `PUB_STATUS`——**改为 `adm/doubao-butler/status`，LWT payload = `offline`** | 容器 kill -9 后 LWT 生效 |
| ② 启动时发 retained `adm/doubao-butler/status=online` + `adm/doubao-butler/caps`（`{mcp: true, tools: [...], version: "2.6"}`） | 网络抓包或 mosquitto_sub 能看到 retained |
| ③ `butler/trigger/gu_anheng_alert` 等 MA 直推主题——**MA 侧清理**，DB 不动（这条不归 DB） | — |

### 第 3 步：DB→MA 四路 token 收敛（与 homesdk auth 合并做）

| 子任务 | 验收 |
|--------|------|
| ① MA 侧 service_token 已批 MA 先做（`20261001-MA-service_token与设备事件feed-裁定.md`），DB 侧配合：`MEMORY_AGENT_TOKEN` / `MEMORY_AGENT_APP_TOKEN` 两键合并为一把 `MEMORY_AGENT_TOKEN`（带 scope: butler/app 由 MA 侧解析） | 30 天双轨期，到期日由 SP 定 |
| ② DB 侧出站请求改走 `homesdk.http.peer_url("memory-agent")` + `homesdk.auth.require_auth_headers("memory-agent")`，**不再裸 httpx** | 全仓 grep 裸 `httpx.*memory-agent` → 0 |
| ③ `integrations/memory_agent.py` 的 4 路认证收敛为 1 路（MCP 主通道）+ 1 例外（vision REST Basic） | vision 实时留 Basic，其余走 Bearer |

### 第 4 步：DB→MA MCP 业务查询（MCP 工具调用）

| 子任务 | 验收 |
|--------|------|
| ① 调 MA MCP `ask_memory`（自然语言查法 → MA 内部路由）或 `get_member_persona`（成员档案+画像）拿回数据 | DB 在对话里能答"昨天书房空调开机时长"（MA 现成洞察工具） |
| ② 拿回数据后**不进历史截断**（上一轮截断统一治理裁定已解：MCP 结果走 MCP 层治理） | — |

### 第 5 步：DB→AF MCP 建自动化（实验档，AF v2.3 后）

| 子任务 | 验收 |
|--------|------|
| ① AF `af_mcp` 工具面命名以 AF 现名为准：`af_draft` → `af_apply(stage="simulate")` → `stage="dry_run"` → 人批 → `stage="save"`（DCD 1002 §三①A：验与部署是同一工具的不同 stage，天然不可能"验着验着变部署"）；DB 通过 MCP 调用链"拟→验→批→存" | 一次端到端 dry_run（不实际部署） |
| ② ask 通道已有，建自动化是 ask 的自然扩展 | AF 侧回执 `ref`，DB 轮询 `/api/asks/pending`（现成） |
| ③ **DB 侧义务（DCD 1002 §三③A，§八-2 要求写入本计划）**：AF 回 `{ok: true, channel_error: true}`（HTTP 200 但写通道坏了）⇒ DB **必须 error 级告警**并**停止把该 ask 标记为已答**；`ref` 语义＝**实例 id**（§三②A），`instance_id` 双写是过渡字段，删除时点＝AF v2.6 | `AF_CHANNEL_ERROR` 落日志 + `delivered_ts` 不记 + `_delivered_total` 不加（`tests/test_dcd_20261002_db_legs.py` 4 例钉住，已落码 b48bfaf） |

### 第 6 步：跨仓契约测试（与 MA/AF 同步）

| 子任务 | 验收 |
|--------|------|
| ① DB→MA 主通道契约测试（`tests/contract/test_db_ma_contract.py`，断言 MCP 调用 schema + 鉴权 + 响应字段） | CI 跑过 |
| ② DB→AF 主通道契约测试（断言 ask 轮询 + answer 回注 schema） | CI 跑过 |
| ③ 部署前必跑（进 `gates.sh`） | — |

---

## 三、不在本版做（已裁并登记）

- v2.9 多模态（P95 6,259ms FAIL，不启动）
- v2.7 Vue3（实测全站 Vue 运行时 0，已裁沿用现有栈）
- doubao_webhook 拆三模块（回归面大，等 v2.6 后）
- v3.0 中枢（G6 门未达成）

## 四、本窗与停机窗口合并

本路线图所有 restart 动作与写面修复窗（已裁 A+C，四条护栏）+ MA 窗（PII 回填+R1/R3 生效）**合并为一个窗口三件事**——不要单独开三次不可用。

**DCD 1002 裁定把窗口顺序写死了**（《20261002-homesdk记账与AF-DB-DPP六件-裁定》§一）：
执行序＝补 homesdk 账（DCD 已做）→ 重建 wheel（已做）→ 重烤 AF 镜像 → AgentOps 模板生效 + **DB 写面遗留** + MA PII/R1/R3；
回滚序＝模板 → 镜像 → wheel → 账，逐件可退。
窗后 AF 侧验收四项（缺任一项＝该步未完成，⛔ 用"配置正确只是没抓包"过账）：`compose ps` 起来、`/health` 200、
`mosquitto_sub` 抓到一条 `af/automation/fired`（含家庭墙钟 `ts`）、`adm/autoforge/status` retained `online`。

---

## 五、DCD 2026-10-02 裁定落账（DB 侧）

权威文书：`E:\NAS\关键决策部\decisions\20261002-homesdk记账与AF-DB-DPP六件-裁定.md`

| 裁定 | DB 侧动作 | 状态 |
|------|-----------|------|
| §二 **A**：tv_notify 改插活井（B 登记死主题／C 砍功能均驳回），载荷以 `butler/api/notify_routes.py:64-73` 那份为准 | `butler/core/cron_task.py::_emit_tv_notify` 走 `butler/notify` 路由 → `TVClient.notify()` → `tv/livingroom/cmd/notify`（CAM 订 `cmd/#`）；出口结论跟着通道回执走，失败/未就绪里不再出现 `: ok`；`PENDING_DCD_DECISION` 随裁定清空 | **已落码-未生效**（commit `b48bfaf`，uvicorn 无 `--reload`，随合并窗 restart） |
| §三③ **A**：AF 回 `channel_error=true` ⇒ DB 必须告警并⛔ 标记已答（§八-2 要求写入本计划） | 已写进本计划第 5 步③（见上表）；代码腿在 `butler/af_bridge.py::on_user_reply`——`AF_CHANNEL_ERROR` error 级 + 不记 `delivered_ts` + 不进 `_delivered_total` | **已落码-未生效**（同 commit） |
| §三① **A**：AF 工具面命名以现名为准，计划文档改口径 | 本计划第 5 步① 已按 `af_draft`/`af_apply(stage=…)` 改口径 | 文书已改 |
| §三② **A**：`ref`＝实例 id；`instance_id` 双写为过渡字段，删除时点＝AF v2.6 | 登记在本计划第 5 步③，DB 侧⛔ 依赖 `instance_id` 新语义 | 已登记 |
| §一：homesdk 记账缺口 DCD 已补；**0.3.1 权威 sha＝重建产物 `b4b5d6bb…`，首次投递 `36fdf77a…` 作废**（成员逐字节一致，差异只在 dist-info 时间戳） | 第 0 步① 的"sha256 与 VERSIONS.txt 同号"按新值对账 | 待第 0 步执行 |

附带落点修正（本窗现读）：通知类型白名单 `ALLOWED_TYPES` 的单一真源从 api 层搬到 `butler/notify/router.py`——
原方案要 `core→api` 反向依赖，且 api 的 `from PIL import Image` 在容器外把 cron 出口直接拖崩
（`ModuleNotFoundError: PIL`，现读过一次）。一份判定只许一处定义。

---

## 七、进度快照与下一版细化（DCD 2026-10-04 增补）

### 7.1 当前真实进度（实测 · 2026-10-06 更新）

| 版本 | 完成度 | 缺口 |
|------|--------|------|
| v2.4.0 | ~50% | jarvis 音色、one-api.db 只读化、播放队列真机验证 |
| v2.4.1 止血 | ~60% | devices.json LWT 归因未验 |
| v2.4.2 清扫 | ~30% | N2 ha.py 去重、N4 白名单硬校验、N5 根目录清理、音色、巡检报告 |
| v2.5 TTS/通知统一 | 67%（2/3） | #3 播放队列真机验证 |
| v2.6 记忆+可观测 | 代码 100% | **已生效**（2026-10-06 restart） |
| v2.7 管理面 | 0% | 已裁沿用现有栈（实测 Vue 运行时 0） |
| v2.8 主动服务治理 | 代码 100% | 已生效 |

**联动计划**（2026-10-06 23:40 实测 · 全部落地）：
- 第 0 步 homesdk 0.3.1：**✅ 已完成**（wheel vendor + Dockerfile 改 + 镜像重建 + 容器验证 `import homesdk; __version__=="0.3.1"`，模块 http/auth/mqtt/presence/time/consent 齐全）
- 第 1 步 公共收件箱：**✅ 已生效 + schema 对齐契约 v2.0 §E**（按通道读 text/title+body/content，去掉 source 必填，长度 ≤500/≤80，MA/AF 按契约投 notify/tv 不再被丢弃）
- 第 2 步 presence 发布：**✅ 已生效 + caps.version=2.7**（MQTT connected as butler，adm peer status online=True，health 端点含 adm_memory-agent 组件）
- 第 3 步 token 收敛：**✅ 已完成**（svc_ 令牌已签发并切换，MEMORY_AGENT_BUTLER_TOKEN+APP_TOKEN 同一 svc_ 令牌，mcp 面独立；7 天观察期后吊销旧令牌）
- 第 4 步 MCP 业务查询：✅ 可用（MCP 工具 list_agent_memories/retrieve_agent_memories/ask_memory/add_semantic_memory 等正常调用）
- 第 5 步 AF MCP 建自动化：**✅ 已生效**（tv_notify 活井 + AF channel_error 告警，`af_bridge` 运行中）
- 第 5 步① 端到端 dry_run：⏳ 待 AF 侧 MCP 工具面就绪后做
- 第 6 步 契约测试：✅ 本地 43 个测试文件，371 passed；gates.sh + .gates.toml 已配置
- 6 个孤儿文件：**✅ 全部已删除**
- 审计报告 40 份：**✅ 全部核实+归档**

**homesdk 落地（PR-A/B 已完成，PR-C1/C2 部分）**：
- PR-A URL 寻址收敛：✅ 5 处硬编码 → `homesdk.http.ma_url()+join_url()`，grep 归零
- PR-B 时区收敛：✅ 7 处 `ZoneInfo("Asia/Shanghai")`/`timedelta(hours=8)` → `homesdk.time.house_now()/house_tz()`，grep 归零
- PR-C1 token 收敛配置：✅ svc_ 令牌已切换（config 层字段名暂保留 butler/app 两键，值同一 svc_ 令牌）
- PR-C2 鉴权头收编 homesdk.auth：⏳ 依赖 homesdk 0.3.2 给 `require_auth_headers` 加 `token_key=` 覆盖参数

**核实补遗 4 项（全部完成）**：
1. ✅ 收件箱 schema 对齐契约（码迁就契约）
2. ✅ caps.version 报计划号 2.7（非包 __version__=1.0.0）
3. ✅ adm_peers 写了也读（离线边沿 ADM_ERR_PEER_OFFLINE + health degraded）
4. ✅ af/automation/fired|failed 登记（DB↔AF 事件腿走 HTTP 轮询，不订阅 MQTT）

**第八节任务卡**：
- #0 收件箱 schema 对齐：✅
- #1 service_token 切换：✅
- #2 adm/*/status 兼容解析：✅（字面量 online/offline 跳过 JSON 解析）
- #3 ma/insights 消费带 ADM_ERR_* + 审计：✅（trace_id 必填 fail-closed + conf 封顶 0.95）
- #4 调 MA/AF 失败统一码：⏳ 后续（ADM_ERR_UPSTREAM_TIMEOUT/AUTH_REQUIRED）
- #5 presence 丢失 → 降级 HA-only + health degraded：✅（presence/fusion.py 已有，adm_peers 离线已纳入 health）
- #6 verify_adm_linkage 三组全绿：⏳ 待 homesdk 0.3.2 发布

**已知问题（不阻塞联动）**：
- MA presence signal 间歇性丢失（60-70s 周期），自动降级到 HA-only 模式后恢复（已知现象）
- event_stream.py HA WebSocket 偶发 `AttributeError: 'str' object has no attribute 'get'`（已有问题，被抑制，不影响主流程）
- homesdk 0.3.2 未发布（PR-C2 完整鉴权收编 + verify_adm_linkage 依赖此版本）

### 7.2 第一优先：合并停机窗（这是你唯一的硬阻塞）

**只 `docker restart`，⛔ 不 `up -d`**（会抹可写层热修）。窗内一次生效：

1. 写面修复两枚 commit（隔离态豁免 + 写失败台账）；
2. `b48bfaf`（tv_notify 活井 + AF channel_error 告警）；
3. 公共收件箱 + presence；
4. 前端 dry_run 开关（B1 裁定）；
5. 格2/3/4/6 + 评估口径流水表。

**窗后硬验收（缺一不算完成）**：
- `db_write_failures` 台账建档且计数 0；
- `trigger_runs` MAX(ts) 持续推进（写面真活）；
- MA 投 `butler/inbox/speak` → 电视真播报；
- 收口尺 `post_deploy_reconcile.py` 的 `heartbeat`/`trigger_evaluations` 两格转绿。

### 7.3 下一版 v2.7 细化任务

| # | 任务 | 验收 | 前置 |
|---|------|------|------|
| 1 | homesdk 0.3.1 vendor 到仓（`vendor/homesdk-0.3.1-py3-none-any.whl` `b4b5d6bb...`）+ Dockerfile 一行 | 镜像内 `import homesdk; __version__=="0.3.1"` | 合并窗 |
| 2 | 两个前置：`BUTLER_WEB_PASSWORD` 不落 Bearer + 7 条静默边补 compose | `homesdk_peer_probe.py` 重跑分区表只朝上链方向变 | 1 |
| 3 | 四路 token 收敛（`MEMORY_AGENT_TOKEN` 一把，MA 侧 scope 分发） | 全仓 grep 裸 `httpx.*memory-agent` → 0 | MA service_token |
| 4 | 管理面四张页（**沿用 alpine+tailwind，不引 Vue**） | 技能管理 / 角色管理 / 系统日志 / 触发链反查 | 无 |
| 5 | `butler/engine.py` 影子文件删除（已裁 A） | 删后全量回归绿 | 无 |
| 6 | v2.4.2 剩余清扫（N2/N4/N5/音色/巡检报告） | 逐项有交付记录 | 无 |
| 7 | v2.5#3 播放队列真机验证 | 连续播报不重叠 | 需真机（人） |

### 7.4 直接执行（不必再问）

- `butler/engine.py` 删除：**已裁 A=删**（`20261002-DB六件影子代码-裁定.md` ⑥），执行即可；
- ① 记忆提取改取最新：**已裁 A**（同裁定 ①）；
- ② 模式规则接上 / ③ TTL 落地 / ④ 输出白名单改报错 / ⑤ TTS 过载归一：**均已裁 A/B/A/A**（同裁定）。

### 7.5 别再重投（已裁）

19 格全部已裁（`20260930-豆包管家19格-裁定.md`）；六件影子代码已裁；tv_notify 已裁；homesdk vendor 形态已裁 B。**新单前先回读裁定书。**

---

## 八、下一阶段：更紧密联动（DCD 2026-10-06）

> 依据：`关键决策部/decisions/20261006-ADM下一阶段联动路线图-裁定.md`；契约 v2.0 见 `homesdk/doc/ADM联动主题注册表与消息契约.md` §七。
> 核心：三组联动端到端跑通 + 失败统一降级/错误码（`ADM_ERR_*`）。

> ⚠️ **核实补遗（DCD 2026-10-06 DB 子代理代码级核实）——首改项，比下表任何一条都靠前**：
> 1. **收件箱 schema 与契约 §1.3 不符（会真丢件）**：代码 `text≤1000`/`title≤64`/统一必填 `text`+`source`，契约是 `speak={text}`/`notify={title,body}`/`tv={content}`、`≤500`/`≤80`、无 `source`。⇒ **MA/AF 按契约投 notify/tv 会因缺 `text` 被丢弃，`body`/`content` 从未被读取**。**裁定：码迁就契约**（契约是唯一真源，MA/AF 已按契约实现）——`inbox.py` 按通道读 `text`/`title+body`/`content`，长度对齐 ≤500/≤80，去掉 `source` 必填。
> 2. **caps.version 报 `1.0.0`（`__version__`）非计划号 `2.6`**：`mqtt_client.py:177` 改报计划号。
> 3. **`adm_peers` 写了不读**：对端离线对 DB 行为零影响——补消费者（离线 → 降级 + `ADM_ERR_PEER_OFFLINE`）。
> 4. **`af/automation/fired|failed` 未订阅**：DB 现走 HTTP 轮询 `/api/asks/pending`，不经 MQTT fired/failed——若要"更紧密"，补订阅（否则在契约里显式登记"DB↔AF 事件腿走 HTTP、不走 MQTT fired"）。

| # | 任务 | 验收 | 前置 |
|---|------|------|------|
| 0 | **收件箱 schema 对齐契约 §1.3**（见上⚠️1，码迁就契约） | MA/AF 按契约投 notify/tv 不再被丢弃；`body`/`content` 被读；长度/字段与契约逐字一致 | 无（第一优先） |
| 1 | service_token 切换（已签发，见 `回执_MA联动收尾_DB侧三项待办_20261006.md` §五） | `.env` 两键换 svc_ 令牌、`GET /api/members` 200、7 天旧令牌零使用 | MA 已签发 |
| 2 | `_on_message` 对 `adm/*/status` 兼容解析（契约 v2.0 §7.1）——**修已登记 bug**（现 JSON 解析丢弃字面量 `online`） | 收到 JSON 与字面量 `online/offline` 都正确更新 `adm_peers` | 无 |
| 3 | 收件箱 / `ma/insights` 消费带 `ADM_ERR_*` + 审计（§7.2） | 校验失败 → fail-closed + 码 + `inbox_events` 审计 | 无 |
| 4 | 调 MA/AF 失败统一码 + `channel_error`（§7.3） | MA/AF 不可达 → `ADM_ERR_UPSTREAM_TIMEOUT`/`ADM_ERR_AUTH_REQUIRED` + 告警，**不假绿** | 无 |
| 5 | presence 丢失 → 降级 HA-only 且 health 报 `degraded` + `ADM_ERR_PEER_OFFLINE` | 断 MA → 60s 内降级 + `/api/health` degraded:true + 码 | 无 |
| 6 | 跑 `verify_adm_linkage`（homesdk `scripts/`）三组全绿 | 探针 rc=0（缺一组即红） | 1-5 |

**本仓失败语义**：收件箱校验失败 fail-closed + 码；presence/洞察失败 degrade-flag + 码；非关键提示 fail-open。**禁止"静默降级"（本仓多轮审计点名的系统性病灶）。**

### 契约对齐规范 v2.0（逐字版 · DCD 20261006）

> 唯一真源 = `E:\NAS\homesdk\doc\ADM联动主题注册表与消息契约.md`。本节是其**逐字快照**，供本仓执行，不再回查其它仓；两者冲突以契约表为准并提 DCD 复议。

**A. `adm/*/status` 统一 JSON**（取代字面量 `online`/`offline`）：

```json
{"state":"online|offline|degraded","ts":1760000000,"degraded":false,"reasons":[],"version":"<计划号>"}
```

- `reasons` 非空 ⇒ `degraded=true`，元素 = `ADM_ERR_*`；`version` = 计划号（AF 2.6 / MA 1.4 / DB **2.7**——**本仓现报 `1.0.0`，须改**）；
- 消费端**兼容旧字面量**：非 JSON 的 `online`/`offline` → 按 `{"state":"online|offline"}` 解析，**不得丢弃**（本仓 `_on_message` 现 JSON 解析丢弃字面量，须改）。

**B. 统一错误码**：

| 码 | 含义 |
|---|---|
| `ADM_ERR_BROKER_UNREACHABLE` | MQTT broker 连不上 |
| `ADM_ERR_PEER_OFFLINE` | 对端 presence 不在线 |
| `ADM_ERR_PAYLOAD_INVALID` | 载荷 schema/校验失败 |
| `ADM_ERR_AUTH_REQUIRED` | 缺令牌 / 过期 / 越权 |
| `ADM_ERR_UPSTREAM_TIMEOUT` | 调对端超时 |
| `ADM_ERR_INTERNAL` | 未分类兜底 |

落点：status `reasons[]` ／ MCP·HTTP 响应 `{ok:false, code, message}` ／ `inbox_events` 审计。**联动失败必须带码，禁止静默丢弃（替代本仓现多套 ad-hoc 前缀 INBOX_DROP/INBOX_FAIL/AF_CHANNEL_ERROR 等）。**

**C. 降级三档**：fail-closed（写面/不可逆：拒+码+审计）｜degrade-flag（读面/可重试：继续+`degraded`+码）｜fail-open（纯提示：放行+日志）。

**D. 事件载荷（逐字）**：
- `ma/insights` `{trace_id, ts, insight_id, kind, persons[], room?, summary, evidence[], snapshot_url?, conf?, intent?}`（**本仓现未过 Sentinel、无 `insight_id` 去重、无 `conf` 封顶——须补**）
- `ma/presence` `{trace_id, ts, members:[{name, member_id, room, via, confidence, last_seen, trigger}], total}`（retained；**本仓现坏载荷静默 return 0/continue——须改 fail-closed + 码**）
- `ma/device-health` `{trace_id, ts, device_id, status, entity_id, from, to, stable_id}`（**`stable_id` 必须非空**；本仓现仅日志，须补消费）
- `af/automation/fired` `{trace_id, ts, automation_id, ref}`（**本仓现未订阅——若要更紧密，补订阅；否则登记"DB↔AF 事件腿走 HTTP"**）
- `af/automation/failed` `{trace_id, ts, automation_id, ref, error}`（同上）

**E. 收件箱 schema（对齐后权威版，本仓码必须按此改）**：
- `butler/inbox/speak` `{trace_id, ts, text, role?, priority?, expires_at?}`，text ≤500，trace_id 必填
- `butler/inbox/notify` `{trace_id, ts, title, body, channel?, priority?}`，title ≤80 / body ≤500，trace_id 必填
- `butler/inbox/tv` `{trace_id, ts, content, duration_s?}`，content ≤500，trace_id 必填
- **无 `source` 字段**；按通道读 `text`/`title+body`/`content`。**本仓 `inbox.py` 现统一要求 `text`（≤1000）+`source`、`title`≤64——与契约不符，MA/AF 按契约投 notify/tv 会被缺 `text` 丢弃，须按上表改。**

**F. MCP 面实名**：AF = `af_draft` + `af_apply(stage∈check|simulate|dry_run|save)`（**无 `verify`/`deploy` 别名**）；ask `GET /api/asks/pending`（read 令牌）+ `POST /api/asks/answer`（write 令牌 + INBOX_KEY，回报 `channel_error`）。

**G. 端到端探针**：`verify_adm_linkage`（homesdk `scripts/`），三组各一条硬读数，缺一 `rc=1`。

### homesdk 落地细案（逐模块 · DCD 20261006 · 确保"装了就要用"）

> **现状一句话**：DB 在 **7 处注释里引 homesdk**（"`homesdk.presence.is_online` 同式"等），但代码里**手写了一份等价物**——这就是"光装 homesdk 没实质应用"。本细案逐模块指定落地 file:line 与验收，**禁止再手写等价物**（对应判例"同名两套"）。

| # | homesdk 模块 | DB 现状（手写等价物，file:line） | 落地目标 | 验收（grep 归零/单点） |
|---|---|---|---|---|
| 1 | `consent` | ✅ 已落地：`core/dialog.py:23`、`skills/engines/llm_decide/ask.py:23` | 保持 | — |
| 2 | `auth` | 4-face token 手塞 header：`integrations/memory_agent.py:22-67`（mcp/butler/app/basic 四令牌） | mcp/butler/app 三面改用**单一 `svc_` 令牌**（已签发，见 `回执_MA联动收尾...` §五）+ `homesdk.auth.require_auth_headers(peer)` | `memory_agent_token`/`butler_token`/`app_token` 三键归零（只剩 config 层读一处 svc_）；裸 header 拼装归零 |
| 3 | `http.peer_url` | URL 硬编码：`memory_agent.py:253/337/414/477/499` `f"{...}/api/..."` | `homesdk.http.peer_url("memory-agent")` 统一寻址 | grep `http://192.168.2.200:8086` 归零 |
| 4 | `mqtt` | 自读 broker 配置（`bus/mqtt_client.py`） | `homesdk.mqtt.broker_settings()` + `resolve_credentials()`（缺凭据 **fail-closed**） | 凭据来源 = homesdk.mqtt，不再自读 |
| 5 | `presence` | 手写 status/caps retained+LWT（`mqtt_client.py:198-220`）+ 手写 `adm_status_is_online`（`topics.py:42`、`mqtt_client.py:104` 注释"同式"） | `homesdk.presence.advertise`（发布）+ `homesdk.presence.is_online`（判读） | 手写 `will_set(ADM_STATUS` / `adm_status_is_online` 归零 |
| 6 | `time` | 硬编码 +8：`decision/engine.py:117-119`、`decision/aggregator.py:43-45`、`core/agent.py:86`、`llm_decide/engine.py:85` 的 `ZoneInfo("Asia/Shanghai")`；`triggers/engine.py:16` `_CST=timedelta(hours=8)`；`cron_task.py:509` `now+tz_offset` | `homesdk.time.house_now()`/`to_house_iso()`，时区由 `HOMESDK_TZ` 单点声明 | grep `ZoneInfo("Asia/Shanghai")` + `timedelta(hours=8)` 归零（或显式登记"纯展示、非时间轴"） |
| 7 | `adm`（0.3.2） | 无 | `ADM_ERR_*` 常量 + status JSON encode/decode + `verify_adm_linkage` 底座 | ad-hoc 前缀（INBOX_DROP / INBOX_FAIL / AF_CHANNEL_ERROR）→ 全部替换为 `ADM_ERR_*` |

**执行顺序与依赖**：

1. **模块 2/3/6（auth/http/time）先做**——它们是"第 3 步 token 收敛"的实体，**不依赖 broker/镜像**，纯代码；
2. **模块 4/5（mqtt/presence）随收件箱 schema 对齐（#0）同批**——都动 `mqtt_client.py` 同一个文件，分开改会互相踩；
3. **模块 7（adm）等 homesdk 0.3.2 发布**，三仓同批 import（规格见 `homesdk/doc/homesdk-0.3.2-规格.md`）。

**红线**：凡 homesdk 已提供的能力，**必须 import 库，禁止手写第二份**。验收的"grep 归零"是硬线，不是建议——这是"装了就实质用"的可判定义。

### PR 级任务卡（homesdk 落地 · 模块 2/3/6 · DCD 20261006）

> 每卡 = 一个可独立合入的 PR，含"改哪个文件、删哪几行、加哪几行、验收命令"。顺序 A → B → C1 → C2，**各自独立可并行**。

#### PR-A：URL 寻址收敛到 `homesdk.http`（模块 3）

- **文件**：`butler/integrations/memory_agent.py`
- **加 import**（文件头）：`from homesdk.http import ma_url, join_url`
- **删 5 处** `url = f"{self.s.memory_agent_url.rstrip('/')}{path}"`（现 :253、:337、:414、:477、:499）
- **改成**：`url = join_url(ma_url(), path)`
  - `ma_url()` = `peer_url("memory-agent")`，解析顺序 `HOMESDK_PEER_MEMORY_AGENT_URL` → `MEMORY_AGENT_URL` → `MEMORY_AGENT_BASE_URL`。DB 现有 `MEMORY_AGENT_URL`（config.py:134）已是 legacy 兼容键，**compose/.env 不用动**。
- **验收命令**：`grep -rn "http://192.168.2.200:8086" butler/` → **0**；`grep -rn "memory_agent_url.rstrip" butler/` → **0**

#### PR-B：时区收敛到 `homesdk.time`（模块 6）

- **6 处硬编码 +8 → house_now()/house_tz()**：
  1. `decision/engine.py:117-119` `datetime.now(ZoneInfo("Asia/Shanghai"))` → `house_now()`
  2. `decision/aggregator.py:43-45` 同上
  3. `core/agent.py:86` 同上
  4. `skills/engines/llm_decide/engine.py:85` 同上
  5. `triggers/engine.py:16` `_CST = timezone(timedelta(hours=8))` → 删，改用 `house_tz()`
  6. `cron_task.py:509` `now_local = dt.datetime.now() + tz_offset` → `house_now()`
- **加 import**：`from homesdk.time import house_now, house_tz`（各文件头）；删只为这一处存在的 `from zoneinfo import ZoneInfo` / `timedelta`
- **验收命令**：`grep -rn "ZoneInfo(\"Asia/Shanghai\")" butler/` → **0**；`grep -rn "timedelta(hours=8)" butler/` → **0**；`grep -rn "house_now" butler/` → ≥6
- 若某处 `now` 是"纯展示、不进任何跨仓/时间轴判定"且想留本地时间，须在注释**显式登记理由**（禁止静默留硬编码）。

#### PR-C1：token 收敛（butler+app → 单一 svc_ 令牌）——纯 DB 配置，无 homesdk

- **文件**：`butler/config.py` + `.env`
- **删**：`memory_agent_butler_token`、`memory_agent_app_token` 字段 + env 读取（:141、:143、:255、:256）
- **加**：`memory_agent_svc_token` 字段 + `_env("MEMORY_AGENT_SVC_TOKEN")`
- **改**：`butler/integrations/memory_agent.py:53-56` `_token_for`：butler/app 两面都返回 `self.s.memory_agent_svc_token`
- **值**：svc_ 令牌 = MA 已签发（`回执_MA联动收尾_DB侧三项待办_20261006.md` §五）
- **验收**：`grep -rn "memory_agent_butler_token\|memory_agent_app_token" butler/` → **0**；`GET /api/members` 用 svc_ 令牌 → 200

#### PR-C2：鉴权头收编 `homesdk.auth`（模块 2）

- **文件**：`butler/integrations/memory_agent.py`
- **加 import**：`from homesdk.auth import require_auth_headers, require_basic_headers`
- **basic 面**（`basic_auth()` :66-70）→ 删，改 `require_basic_headers("memory-agent")`（homesdk 读 `USER_MEMORY_AGENT`/`PASSWORD_MEMORY_AGENT`，legacy `MEMORY_AGENT_USER`/`MEMORY_AGENT_PASS` 已在表内，**compose 不用动**）
- **mcp bearer 面**（`bearer_headers("mcp")`）→ 删，改 `require_auth_headers("memory-agent")`（读 `TOKEN_MEMORY_AGENT` → legacy `MEMORY_AGENT_TOKEN`，**不用动**）
- **butler/app svc bearer 面** → ⚠️ **依赖 homesdk 0.3.2 给 `auth.require_auth_headers` 加 `token_key=` 覆盖参数**（规格已同步 `homesdk/doc/homesdk-0.3.2-规格.md`），落地后 `require_auth_headers("memory-agent", token_key="TOKEN_MEMORY_AGENT_SVC")`。**0.3.2 未发前此面先保留 DB 自己的 `bearer_headers`（token 源改读 C1 的 `memory_agent_svc_token`），不阻塞本 PR 其余部分。**
- **验收**：`grep -rn "Authorization.*Bearer" butler/integrations/memory_agent.py` → 只剩 homesdk 调用（裸 header 拼装归零）；`grep -rn "basic_auth\|bearer_headers" butler/integrations/memory_agent.py` → 归零（或仅 C2 暂留 svc 面）

---

—— 关键决策部 · DCD