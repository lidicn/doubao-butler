# 豆包管家 · 多角色对话 + TTS 基建 开发计划

> 版本：v1（需求冻结稿）
> 日期：2026-09-04
> 范围：在已落地的「技能层」之上，新增（1）角色（Persona）层、（2）TTS 基建扩展（edge-tts 双声音修复 + 全屋任意小爱音箱推送 + 按角色选音色/设备）。
> 关联项目：doubao2api（独立仓库，以交接单对接，不并入本计划开发线）。

---

## 0. 需求冻结清单（本次一次性理清）

### 0.1 用户已确认的需求

| # | 需求 | 说明 |
|---|------|------|
| R1 | 多角色对话 | 对话按【角色】分类（非按技能/按成员/全局单一）。每个角色是一个独立对话线程。 |
| R2 | 角色定义 | 角色 = 名称 + 性别 + 音色(edge-tts) + 拥有的技能 + 可出现的房间 + 输出设备。WebUI 可编辑。 |
| R3 | 主角色「豆包管家」 | 女、全屋可现身、拥有 `豆包hello` 技能、默认系统人格。 |
| R4 | 衍生角色「晓月」 | 女、健康专家、拥有 `拍照识别食物热量`、仅用厨房摄像头、仅用客厅小爱音箱PRO右 TTS。 |
| R5 | 排他规则 | 任一角色同一时刻只能在一个房间/设备出现（主角色=允许房间=全屋，仍遵守"单房间同时"）。 |
| R6 | edge-tts 双声音修复 | 当前只有一个声音能响，另一个无法发声，需诊断并修复。 |
| R7 | TTS 推送至全屋任意小爱音箱 | 当前只推 TV；需扩展到按房间/设备推到任意小爱音箱。 |
| R8 | 手机端续聊（跨端上下文） | 依赖 doubao2api「会话保留」特性；按角色各自的 conversation_id 复用。 |

### 0.2 架构师建议补进冻结清单的基建项（非"功能"，但缺则产品不成立）

| # | 基建项 | 为什么必须 |
|---|--------|-----------|
| I1 | **设备登记表**（房间→设备：类型 tv / xiaomi，entity 或 MQTT 主题，可选 voice 覆盖） | R7 与 R4/R5 的物理基础；没有它"任意小爱""晓月只用某音箱"都无从路由。 |
| I2 | **按角色选音色 + 按设备分发**的 TTS 路由 | R6/R7 的实现落点；当前 `_voice_for` 只用全局声音（已有 TODO）。 |
| I3 | **per-角色 conversation_id 持久化**存储 | R8 跨端续聊的前提；重启不丢上下文。 |
| I4 | **在场检测 → 角色上下文映射**稳定性 | 谁在说话决定角色行为；依赖 MA 人脸识别，需稳定落到角色/成员。 |
| I5 | **per-角色 presence 锁 + 全局播报仲裁** | R5 排他；现有 `_out_lock` 是全局单锁，多角色需 per-角色 + 全局仲裁防"双声"。 |
| I6 | **设备离线兜底策略** | 某角色设备离线时：沉默 / 降级 TV / 降级 Bark 文字，需明确。 |

> 除上述外，暂未发现其他"核心功能"缺口。本期冻结范围为 R1–R8 + I1–I6。

---

## 1. 现状与缺口（基于源码核实）

| 模块 | 现状（代码事实） | 缺口 |
|------|----------------|------|
| `butler/tts/manager.py` | 双引擎 edge-tts(主)→kokoro(备)→Bark(文字)；`_voice_for` 仅用全局 `tts_edge_voice` | 不支持按角色/成员选声音（TODO 明写）；R6 根因之一 |
| `butler/tts/edge_tts.py` | `synthesize(text, voice, speed)` 已接收 voice 参数；注释注明"偶发 403 按连接限流" | 第二声音若 ID 非法或撞 403→空音频→静默；需诊断 |
| `butler/integrations/ha.py` | `notify_message` / `tts_speak(entity_id)` 已可让小爱发声 | 只接全局 `xiaomi_notify_entity`，非"任意"；R7 需设备感知 |
| `butler/integrations/tv.py` | `play_url` 经 MQTT `PUB_TV_TTS` 播音频 URL；`play_text` HTTP 降级 | 正常，无需改 |
| `butler/skills/schema.py` | `_OUTPUTS={"tv_notify","xiaomi_speak","bark"}`；无 persona/role 字段 | 需加 `persona`/`role` 链接字段（R1/R2） |
| `butler/skills/runner.py` | `_apply_outputs` 已分发 tv_notify/xiaomi_speak/bark；`_out_lock` 全局单锁 | 需按角色解析 voice+devices；需 per-角色 presence 锁（R4/R5/I5） |
| `butler/skills/defaults.py` | 已播种 `hello`、`food-calorie`（均 tv_notify） | 需给技能加 role；新增 `晓月` 角色种子 |
| `butler/config.py` | `PersonaConfig`=系统单一人格（system+members）；`xiaomi_notify_entity` 单设备 | 需新增 roles 存储 + 设备登记表；`PersonaConfig` 降级为"主角色种子" |

**关键澄清**：现有 `PersonaConfig`（系统人格）≠ 本计划的"多角色"。本计划新增**角色实体（roles）**，主角色种子可复用 `PersonaConfig` 的 `system`，衍生角色（晓月）为独立文件。

---

## 2. 架构设计

### 2.1 实体关系

```
成员(Member) ──在场──> 触发技能(Skill) ──owner──> 角色(Role)
                                        │
                                        ├─> 角色.voice (edge-tts 音色)
                                        ├─> 角色.presence_rooms (可出现房间)
                                        └─> 角色.output_devices (设备 id 列表)
                                                              │
设备登记表(DeviceRegistry) ──id──> 设备(tv / xiaomi, entity/topic, voice_override)
                                                              │
                                                  角色.presence_lock (单房间同时)
                                                              │
                                          doubao2api conversation_id (per-role, R8)
```

### 2.2 角色 schema（新增 `data/roles/*.json`）

```jsonc
{
  "id": "butler", "name": "豆包管家", "gender": "女",
  "is_primary": true,
  "voice": "zh-CN-XiaoxiaoNeural",        // edge-tts 音色
  "tts_backend": "edge-tts",               // edge-tts | kokoro
  "presence_rooms": ["*"],                  // * = 全屋
  "output_devices": ["tv_living", "xiao_living"],
  "system": "你是「豆包管家」…",           // 复用 PersonaConfig.system
  "skills": ["hello"]                       // 反向展示用
}
{
  "id": "xiaoyue", "name": "晓月", "gender": "女",
  "is_primary": false,
  "voice": "zh-CN-XiaoyiNeural",
  "tts_backend": "edge-tts",
  "presence_rooms": ["厨房", "客厅"],
  "output_devices": ["xiao_living_right"],  // 客厅小爱音箱PRO右
  "system": "你是家庭健康管家…",
  "skills": ["food-calorie"]
}
```

### 2.3 设备登记表（`data/devices.json` 或 `config.devices`）

```jsonc
{
  "tv_living":      {"type": "tv",     "room": "客厅", "mqtt": "tv/livingroom/cmd/tts", "voice_override": null},
  "xiao_living":    {"type": "xiaomi", "room": "客厅", "ha_entity": "notify.xiaomi_xxx", "channel": "left"},
  "xiao_living_right":{"type":"xiaomi","room":"客厅","ha_entity":"notify.xiaomi_xxx","channel":"right"},
  "xiao_study":     {"type": "xiaomi", "room": "书房", "ha_entity": "notify.xiaomi_study"}
}
```

推送分发：
- `tv` → `TVClient.play_url(url)`（先 TTS 合成音频 URL）。
- `xiaomi` → `HAClient.notify_message(text, ha_entity)`（HA 侧合成）；若需指定声道用 `execute_text_directive` 的 channel 参数。

### 2.4 技能 schema 增加字段

```jsonc
{ "id":"food-calorie", ..., "role":"xiaoyue", "output":[{"type":"tv_notify","tts":true}] }
```
`schema.validate_skill` 增加 `role`（默认 `"butler"`），校验存在于 roles 表。

### 2.5 对话 keying（R8）

- 角色线程 = `doubao2api conversation_id`，按 role 持久化（I3）。
- 调用 doubao2api 时带 `keep_conversation=true` + 该 role 的 `conversation_id`（复用 `交接单_会话生命周期_销毁或同步手机端.md` 所请特性）。
- 该特性未落地前，但ler 仍可正常单次调用；持久化位预留，落地后自动生效。

---

## 3. 分阶段任务

### Phase A — TTS 基建（R6/R7/I1/I2）

**A1 edge-tts 双声音诊断与修复**
- 新增调试端点 `GET /api/tts/voices`：列出候选音色 + 对配置的每个 voice 跑一次合成，返回 `{voice, ok, bytes, err}`。
- 诊断项：① voice ID 是否为合法 edge-tts（`zh-CN-*`Neural）；② 是否撞 403 空音频；③ 是否因 `_synth_once` 失败退避后仍空。
- 修复：`_voice_for(member, voice)` 支持传入角色 voice；对非法/空音频 voice 明确报错而非静默；必要时对 403 增加较长退避或切换备 voice。
- 验收：两个角色各自音色都能在 TV/小爱上出声；`/api/tts/voices` 两个 ok=true。
- 改动：`butler/tts/manager.py`、`butler/tts/edge_tts.py`、`butler/api/tts_routes.py`。

**A2 设备登记表（I1）**
- 新增 `butler/devices.py`：`DeviceRegistry` 加载 `data/devices.json`；`resolve(role, room)` 返回该角色在当前房间可用的设备列表。
- 种子文件 `data/devices.json`（客厅 TV、客厅小爱左右声道、书房小爱…）。
- 验收：registry 能按角色+房间解析设备。

**A3 TTS 按角色选音色 + 按设备分发（I2/R7）**
- `TTSManager.synthesize` 增加 `voice` 入参（角色 voice）；`runner._apply_outputs` 改为：先按角色解析 `output_devices` → 对每个设备分发（tv=合成URL播放；xiaomi=HA notify，按 entity/channel）。
- 复用现有 `xiaomi_speak` 输出类型，但改为设备感知（不再只用全局 `xiaomi_notify_entity`）。
- 验收：晓月技能只从客厅小爱PRO右出声；主角色可从 TV 出声。

**A4 兜底策略（I6）**
- 设备离线/合成失败 → 按角色配置 `fallback`（沉默 | TV | Bark）；默认 Bark 文字。
- 改动：`runner._apply_outputs`、`config.py`（每角色可选 fallback）。

### Phase B — 角色层（R1–R5/I5）

**B1 roles 存储 + schema 链接**
- 新增 `butler/roles/store.py` + `schema.py`：加载/校验/增删 `data/roles/*.json`。
- `skills/schema.py` 加 `role` 字段（默认 `butler`）。
- 改动：`butler/skills/schema.py`、`butler/skills/store.py`。

**B2 默认角色播种**
- `skills/defaults.py`：hello→role butler；food-calorie→role xiaoyue；新增 `data/roles/butler.json`、`xiaoyue.json` 种子。
- 改动：`butler/skills/defaults.py`、`butler/roles/defaults.py`（新）。

**B3 runner 角色解析 + presence 锁（I5/R5）**
- `runner.run`：从 skill 取 `role` → 加载角色 → 注入 `ctx.role`（voice/devices/system）。
- `_apply_outputs`：用角色 voice+devices 分发；加 `per-role presence lock`（角色占用某设备期间，同角色其他触发按 `on_busy` 排队/丢弃）；同时保留全局播报仲裁（防 TV+小爱双声）。
- 验收：晓月播报中，另一厨房触发排队/丢弃；主角色在客厅 TV 播报时不在书房小爱同时出声。
- 改动：`butler/skills/runner.py`、`butler/skills/runner_types.py`。

**B4 兼容端点**
- `/api/hello` 内部转 `runner.run("hello")`；响应保持超集（tv_online/person/nickname/scene/greeting）。
- 改动：`butler/api/hello_routes.py`（已存在，微调）。

### Phase C — WebUI（角色管理页）

**C1 角色管理页**
- 侧边栏新增「角色」view（沿用现有 glass/amber 体系）。
- 列表：角色卡片（名称/性别/音色/启用/拥有技能/出现房间）；点开抽屉：编辑（名称、edge-tts 音色下拉含试听、房间多选、设备多选、system 提示词）+ JSON 源码视图 + 启用/试跑。
- 设备登记表管理入口（或并入角色页的"设备"子页）。
- 改动：`butler/static/index.html` + `butler/static/js/app.js` + 新增 `butler/api/role_routes.py`。

### Phase D — 对话持久化对接 doubao2api（R8）

**D1 per-role conversation_id 持久化（I3）**
- 新增 `data/role_state.json` 或 `store` 表：记录每个 role 的 `conversation_id`、`updated_at`。
- 调用 doubao2api 时带 `keep_conversation=true` + 角色 `conversation_id`；响应回传的 `conversation_id` 写回。
- **依赖 doubao2api「会话保留」特性落地**（见 §4，已发交接单，未实现）。落地前本阶段代码就位但处于"单次调用"模式；落地后自动切换为持久线程。
- 验收：晓月角色在手机豆包 App 可见连续 thread，可续聊。

### Phase E — 部署与端到端验证

- scp 变更 → `docker compose up -d --build` → curl 冒烟（edge-tts 双声音、xiaomi 任意音箱、角色排他、/api/hello 兼容、food-calorie 走晓月）。
- 真机：客厅 TV 播主角色；厨房触发 food-calorie→客厅小爱PRO右播晓月。

---

## 4. doubao2api 关系（明确：不并入，维持交接单）

- **多角色对话是否需要 doubao2api 对接？** 需要，但**只需已发交接单覆盖的能力**，无需新交接单。
  - 已发：`E:\NAS\doubao2api\doc\交接单_会话生命周期_销毁或同步手机端.md` → 请开发者实现 `keep_conversation` 开关 + 响应回传 `conversation_id`。
  - 本计划的 R8/D1 = 按角色复用各自的 `conversation_id` + `keep_conversation=true`。**这正是该交接单的能力**，故不再发新交接单。
- **doubao2api 是否交付本 agent 直接开发？** 建议**维持原对话 + 交接单模式**（不把 doubao2api 并入 butler 开发线）：
  - doubao2api 是独立的逆向工程仓库，风险面不同，作"稳定外部 API 依赖"更清晰；
  - 本期只需它一个微小特性（keep_conversation），已交接；
  - 若你希望更快，我也可直接改 doubao2api 源码实现该特性（同 CodeBuddy 内），需你确认。
- 若后续多角色需要 doubao2api 新增能力（如"按角色隔离会话空间"），再发新交接单。

---

## 5. 开放决策点（待你确认后开工）

1. **doubao2api 开发模式**：维持交接单（推荐）/ 本 agent 直接改源码。
2. **角色概念命名**：UI 显示「角色(Persona)」（推荐，避免与成员「人设」混淆）/ 沿用「人格」。
3. **本期范围**：R1–R8 + I1–I6 全做（推荐）/ 先 R6+R7 基建、角色层后续。
4. **主角色排他**：全屋可去但单房间同时（推荐）/ 主角色可多房间同现。

> 确认后我将把本计划拆为可执行的 todo 并进入 Phase A 实现。
