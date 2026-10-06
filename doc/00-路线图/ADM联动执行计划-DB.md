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

### 7.1 当前真实进度（实测）

| 版本 | 完成度 | 缺口 |
|------|--------|------|
| v2.4.0 | ~50% | jarvis 音色、one-api.db 只读化、播放队列真机验证 |
| v2.4.1 止血 | ~60% | devices.json LWT 归因未验 |
| v2.4.2 清扫 | ~30% | N2 ha.py 去重、N4 白名单硬校验、N5 根目录清理、音色、巡检报告 |
| v2.5 TTS/通知统一 | 67%（2/3） | #3 播放队列真机验证 |
| v2.6 记忆+可观测 | 代码 100% | **未生效**（uvicorn 无 reload） |
| v2.7 管理面 | 0% | 已裁沿用现有栈（实测 Vue 运行时 0） |
| v2.8 主动服务治理 | 代码 100% | 未生效 |

**联动计划**：第 0 步 homesdk 0.3.1 **未 vendor**（卡第 1/2/3 步）；第 1/2 步落码（`e13b4d3`）；第 5 步落码 `b48bfaf` **未生效**；第 6 步契约测试已交付但未确认进 gates.sh。

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

—— 关键决策部 · DCD