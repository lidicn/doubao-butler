# 建议单：触发层与 MCP 选型分析 —— 自研 vs 复用 autoflow

> 发起方：AutoFlow（项目负责人）｜接收方：豆包管家 PM
> 日期：2026-09-10
> 关联文档：`开发计划_v1.8_触发层加固与MCP基础.md`、`回复单_感知层归属与MA管家协同_20260910.md`（方案 C）、`路线图_豆包管家_v1.2.md`
> 性质：**选型分析 + 架构体检**，含证据引用（文件:行号），供 PM 裁决
> 依据：五项目代码量普查（2026-09-10）+ autoflow 竞技场 R1~R6.5 七轮验证链迭代教训 + 对 butler 仓库的实测探查

---

## 一、TL;DR（结论先行）

1. **触发层：不是二选一，是切一刀。** 实时决策触发留在自研引擎（这是管家的核心价值，绕道 NR 会毁掉对话延迟）；**持久化自动化规则一律委托 autoflow**（`autoflow_propose_dsl` 提案 → 编译闸 → vhass 孪生自证 → 人审 → NR 执行）。v1.8 的"触发层加固"保留但**收窄范围**到实时/会话内触发。
2. **MCP：不自建 Server。** v1.8 的"MCP Server 基础"降级为**只读 REST API + Token**（先满足外部 agent 接入），MCP 协议层等 autoflow v2.0.12 的 OAuth 发现端点落地后再做生态统一。理由：自建 MCP 是第二套鉴权/审计/Schema 真相源，维护成本 > 收益。
3. **架构体检挖到 2 个必须处理的安全项**：compose 文件提交了 MQTT 明文密码（`docker-compose.yml:18`）+ WebUI 默认密码 `admin`（`docker-compose.yml:13`）；另有自进化引擎 active 模式"低风险变更自动部署"——**无验证链的自动部署**，与 autoflow 七轮血泪教训正面冲突，建议强制 passive/semi。
4. 一句话：**管家 = 决策对话大脑（自研），自动化工厂 = autoflow（复用），感知记忆 = memory-agent（已定方案 C），设备手脚 = 双 Pilot。** v1.8 里唯一越界的是 MCP 部分，收回来就好。

---

## 二、背景：两个项目各自有什么（实测盘点）

| 资产 | autoflow | 豆包管家 |
|---|---|---|
| 定位 | HA 自动化的 **DSL 网关 + 验证工厂** | 家庭 AI **决策大脑**（感知→记忆→决策→开口） |
| 触发相关 | DSL 编译器 → Node-RED flow；`run_staging_gate`（vhass 孪生重放 + 断言 + 诚实降级 B20/B22）；实体白名单/幽灵实体拦截 | 自研 `butler/triggers/`（engine 302 行 + schema 143 行 + store/defaults）；v1.8 计划注册中心+执行审计+健康监控 |
| 验证能力 | **七轮迭代的验证链**：编译闸→静态 Linter→分支感知重放→断言（真转变 changed_by_replay）→诚实降级→WebUI 人审→部署授权码→快照回滚 | PushGuard 五层推送风控（402 行，工业级，**这是管家自己的强项**） |
| MCP | 三面板（/mcp、/mcp-white、/mcp-admin）+ 身份过滤 + 调用守卫 + ACP 端点；v2.0.12 计划补 OAuth 发现 | v1.8 计划自建（未开工） |
| 体量 | 82k 行 / 188 测试文件 | 32k 行 / 12 测试文件 |

---

## 三、选型一：触发层 —— 自研规则 vs autoflow DSL→NR

### 3.1 先切一刀：管家的"触发"是两种东西

| | 实时决策触发 | 持久化自动化规则 |
|---|---|---|
| 例子 | "有人进客厅→管家搭话"、"运动异常→立即告警" | "每晚十点半熄灯"、"湿度>80 开除湿插座" |
| 延迟要求 | **毫秒~秒级**（对话体验） | 分钟级无感 |
| 生命周期 | 会话内/当次决策 | 长期驻留、可积累 |
| 需要人审吗 | 不需要（决策即执行） | **需要**（改的是家的长期行为） |
| 正确归属 | 管家自研引擎（+PushGuard 风控） | **autoflow 提案闸 → NR 执行** |

### 3.2 自研触发层跑持久化规则的利弊（结论：弊大于利）

**利**：零跨项目依赖、数据模型贴合管家、单进程零延迟、v1.8 加固后可观测性好。

**弊（按严重度）**：
1. **无验证链**——规则写错没有编译闸/孪生重放兜底。v1.8 自己承认要修"定时任务静默失败"，而这正是 autoflow 验证链的日常输出（R5 一轮就抓出 6+5 道静默失败题）。自研等于重新踩一遍 R1~R6.5 的坑，且没有 twin 可以重放自证。
2. **双引擎运维面**——同一家庭的自动化逻辑散落 NR（autoflow 管）+ triggers（管家管），排障时两个地方翻，出事后互相甩锅没有台账。
3. **绕过人审**——autoflow 的批准只在 WebUI（agent 不可能自批准）是安全不变量；管家自持久化规则等于给 LLM 开了一条绕过人审写"家庭长期行为"的通道。
4. **与 self_evolution 叠加风险翻倍**（见 §6.3）——自进化 active 模式自动部署 + 自研触发无验证 = LLM 自己改自己的自动化还没人知道。

### 3.3 复用 autoflow 的利弊（结论：利大于弊，成本≈1 天）

**利**：验证链白嫖（编译/静态/重放/断言/人审/快照回滚全套）；竞技场经验库直接反哺（butler 的 agent 也有战绩画像）；NR 单一写入纪律得以成立（§6.5）；butler 只需一个 `autoflow_propose` 技能 + WebUI 点批准。

**弊（都要承认）**：
1. **跨服务依赖**——autoflow 宕机时管家不能建新自动化。缓解：管家对 autoflow 健康检查，宕机时降级为"记下意图，恢复后补提案"（参考管家自己给 MA 信号设计的 60s 降级策略，同一手法）。
2. **提案要人审**——管家用户可能嫌烦。缓解：autoflow 提案闸本来就是批量人审设计（WebUI 场景提案面板一次看一批），且低风险题（single switch on/off）验证链已能 fully_verified，人审只是点一下。
3. **表达力边界**——管家某些触发（跨设备上下文、MA 行为信号）超出 HA 事件域，autoflow DSL 可能表达不了。缓解：这正是 `deploy_raw` 逃生舱存在的意义，且这类"上下文触发"本就该留在管家引擎（它不是"持久自动化"而是"实时决策"）。

### 3.4 边界判定准则（建议写进管家 skills 路由）

```
用户说了一句"以后每当X就Y"：
├─ X/Y 都是 HA 设备域事件/状态 ──────────→ autoflow_propose（DSL 可表达）
├─ X 涉及 MA 行为/位置/视觉信号 ─────────→ 两种：
│    ├─ 管家能订阅 ma/# 信号实现 ────────→ autoflow_propose（触发源挂 MQTT 事件）
│    └─ 需要实时融合判断才成立 ──────────→ 管家引擎（这是决策不是自动化）
├─ Y 是 DeskPilot/TVPilot 设备动作 ──────→ 管家技能（ReAct 工具，本来就归管家）
├─ 会话内临时（<1h，如"等会儿提醒我"）──→ 管家引擎 + PushGuard
└─ 拿不准 ──────────────────────────────→ 问用户要不要"长期生效"，要 → autoflow
```

### 3.5 迁移方案（不推翻 v1.8，只收窄）

- v1.8 的触发层加固（注册中心/执行审计/健康监控）**照做**，但 scope 注明"限实时/会话内触发"；
- 新增 `autoflow_propose` 技能：调 autoflow `POST /api/arena/...` 或 MCP `autoflow_propose_dsl`，把"长期规则"请求转为提案，回执里的 `knowledge_feedback` 直接透给用户（前车之鉴）；
- 存量排查：triggers/store 里已持久化的规则，逐条判定"该不该住在管家"，该搬的走一次 autoflow 提案流程搬家。

---

## 四、选型二：MCP —— 自建 Server vs 复用 autoflow

### 4.1 v1.8 原计划 vs 我的建议

| | v1.8 原计划 | 建议方案 |
|---|---|---|
| 形态 | 自建 stdio+HTTP 双传输 MCP Server | **短期：只读 REST API + Token**（现状 API 已 90% 够用）；**中期：等 autoflow OAuth 发现端点**（v2.0.12-1，验收门：客户端零手工配置）后按需 MCP 化 |
| 鉴权 | 自造 Token 体系 | 复用现有会话/Token；外部 agent 走 autoflow 三面板身份（normal/expert），管家数据作为**只读 scope** |
| Schema | 新写一套 | 不新写；工具语义已在 `core/tools.py`（OpenAI function-calling 格式），外部 agent 直接消费 REST |
| 审计 | 计划中 | autoflow 侧 `_telemetry`/`_slog` 现成 |

### 4.2 为什么不建议自建 MCP（利弊对照）

**自建之利**：独立生命周期；能暴露管家私有数据模型（指令中心/技能市场/时序异常）。
**自建之弊**：
1. **第二套鉴权/审计/Schema 真相源**——autoflow 刚在路线图 v2.0.12-2 承诺消灭"手写 schema 与签名两套账"，管家再建一套等于生态里出现第三套；
2. **agent 接入成本翻倍**——外部 agent（opencode/豆包工作）要同时理解两个 MCP 面的工具语义与鉴权；
3. **安全面扩大**——多一个常驻监听面 = 多一份攻击面，而管家测试密度只有 5%（12/248），安全代码没测试兜底；
4. **维护税**——MCP 协议本身在演进（Streamable HTTP、OAuth resource metadata），autoflow 有专人跟，管家跟不动。

**复用之弊（同样承认）**：autoflow MCP 目前没有管家数据（指令/技能/时序）的 Resource——但这恰恰说明**不需要 MCP**：外部 agent 拿管家数据用 REST 就够，MCP 的价值在"模型选择能力"，而管家的核心能力（决策对话）本来就不是给外部模型选的工具。

### 4.3 建议的落地路径

1. **v1.8 内**：把"MCP Server 基础"改为"只读 REST + Token + 审计"（工作量减半，删掉 stdio/HTTP 双传输与 MCP 协议适配）；
2. **autoflow v2.0.12-1 落地后**：管家作为第一个"生态第二 Server"验证 OAuth 发现互操作；
3. **长期**：若外部 agent 真需要"选管家能力"，优先把该能力做成 autoflow 的 Resource/Tool（挂在 autoflow 面下，共享鉴权与审计），而不是管家自开端口。

---

## 五、为什么信 autoflow 的验证链（给 PM 的决策依据）

R1~R6.5 七轮竞技场在验证链上抓出的真实缺陷（全部有台账）：
- B20：断言在目标已处于期望态时**空转通过**（假绿）
- B22：未激活分支的断言被静默跳过 → 反向动作被判放行
- F-R5-01：未充分验证的 flow 被锁死成永久死锁
- F-R6.5：数值条件在触发注入/触发判定/JSONata 三层被当字符串 → 数值自动化全军覆没

这些缺陷每一个都是"规则看起来执行了，其实没验证"的静默失败。管家自研持久化触发，就是把这些坑重新踩一遍——而且管家没有竞技场这个回合制验证场来发现它们。**复用不是偷懒，是别再交一遍学费。**

---

## 六、除路线外：架构体检发现（按优先级，均带证据）

### 6.1 🔴 安全：compose 文件提交了真实凭据（立即处理）
- `docker-compose.yml:13`：`BUTLER_WEB_PASSWORD=admin`（默认弱口令入库）
- `docker-compose.yml:18`：`MQTT_PASSWORD=HP…`（**MQTT 明文密码提交进 git**——MQTT 可控全屋设备，这是全家最高危的一把钥匙）
- 改法：密码移 `.env`（compose `env_file:` 引用），`.env` 进 `.gitignore`；参照 autoflow 的 `tests/test_no_secrets.py`，加一个发布门禁脚本扫 compose/源码里的硬编码密钥。**顺手把已入库的密码全部轮换**（进了 git 历史就算删了也等于泄露）。
- `butler/config.py:303` 已有敏感键清单（说明有意识），缺的是"仓库文件扫描"这一层。

### 6.2 🔴 自进化引擎 active 模式（架构级风险）
`butler/self_evolution.py`：v2.0 自进化闭环，active 模式"低风险变更自动部署"。
- 问题：**什么算低风险由 LLM 判断，部署前无验证链**（无沙箱重放、无断言、无孪生）。这相当于把 autoflow 花七轮才堵上的"未验证就生效"重新打开，而且是自动的。
- 建议：active 模式的"低风险"白名单收窄到**纯只读/纯展示类**变更；任何涉及设备动作/触发规则的生成物，一律走 autoflow 提案闸或强制 semi（人工确认）。验证标准对齐 PushGuard 的做法——你自己已经证明了"风控要持久化+审计"，自进化更需要。

### 6.3 🟡 God module：`butler/core/tools.py` 1765 行
OpenAI function-calling 的统一 dispatch + 全部工具实现挤在一个文件。ReAct 循环是管家最常改的地方，1765 行意味着每次改动 review 面太大、冲突率高。建议按域拆包（ha/tv/desktop/memory/skill 各一个 handler 模块，tools.py 只留注册与分发），与 DeskPilot 13 工具、TVPilot 工具的对接单结构对齐。

### 6.4 🟡 测试分布失衡（12 文件对 248 源码）
已有的 12 个测试集中在 ReAct/决策/对话/双 Pilot 工具——方向对，但覆盖缺口恰好在**静默失败高危区**：`guard/push_guard.py`（396 行，五层防护无回归）、`triggers/engine.py`（302 行）、`integrations/ha.py`、`integrations/memory_agent.py`、`timeseries/anomaly.py`。建议优先给 push_guard 补 R7 式回归（连发熔断/穿透/合并/审计断言——v1.8 文档里的验证结果就是现成测试用例，照抄成 pytest）。

### 6.5 🟡 NR 多方直写，缺写入纪律
autoflow（af_ 前缀 + WebUI 审批）、memory-agent（自带 NR 联动）、butler（根目录 `nr_basic*.json`、v0.8"NR 瘦身"）三方都直接操作 NR。建议立一条**生态纪律**：任何项目向 NR 写持久化 flow，一律走 autoflow 提案闸；项目前缀约定（`af_*`/`butler_*`/`ma_*`），非本前缀 flow 一律只读——autoflow 已实现该守卫，其他两个项目接进来即可。

### 6.6 🟢 工程卫生（低成本收尾）
- 根目录 9 个补丁/试验脚本（`fix_*.py`×5、`hotfix.sh`、`tmp_e2e.sh`、`verify_commands.py`、`add_commands_tab.py`）→ 归档 `scripts/legacy/`，根目录只留入口；
- `nr_*.json` ×7 试验残留 → 移 `doc/archive/` 或删；
- 但ler 的 doc/ 纪律是全生态最好的，这份优势保持。

### 6.7 🟢 agent_id 稳定性（与竞技场画像同理）
管家对接的各执行体（DeskPilot/TVPilot/memory-agent）在 MQTT/日志里的身份标识建议固定命名（`butler-*`），跨轮统计、审计归因、autoflow 竞技场画像都依赖 ID 稳定。

---

## 七、行动清单（按优先级）

| # | 事项 | 优先级 | 工作量 | 责任方 |
|---|---|---|---|---|
| 1 | compose 密码出库 + 密钥轮换 + no-secrets 门禁 | 🔴 P0 | 2h | 管家 |
| 2 | self_evolution active 模式白名单收窄 | 🔴 P0 | 2h | 管家 |
| 3 | v1.8 MCP 部分降级为只读 REST+Token | 🟡 P1 | -0.5 天（反而省） | 管家 |
| 4 | `autoflow_propose` 技能 + 触发边界路由（§3.4） | 🟡 P1 | 1 天 | 管家（autoflow 侧配合零改动） |
| 5 | push_guard / triggers / ha_tools 回归测试 | 🟡 P1 | 1 天 | 管家 |
| 6 | NR 写入纪律三方确认（前缀约定） | 🟡 P1 | 0.5 天 | 三方 |
| 7 | tools.py 拆包 | 🟢 P2 | 1 天 | 管家 |
| 8 | 补丁脚本归档 | 🟢 P2 | 0.5h | 管家 |

---

## 八、对 autoflow 侧的连带修正（已执行）

- autoflow `docs/ROADMAP.md` v2.1.0 的"memory-worker 联动消费点"更正为 **memory-agent**（memory-worker 为旧名，改名交接 `1549e5a`；生态口径统一叫 memory-agent）。

---

> 结语：五项目生态画像已经清晰——**memory-agent 感知记忆、管家决策对话、autoflow 自动化工厂、双 Pilot 设备手脚**。本建议单的全部内容都是让各项目待在自己的层里，把重复建设的最后一个口子（自建 MCP / 自研持久化触发）关上。
