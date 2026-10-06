# Memory-Agent 接口标准 v1.0（生态共享版）

> 版本：v1.0 | 日期：2026-09-10 | 维护方：豆包管家（PM）
> 适用项目：TVPilot / DeskPilot / 小甜菜 / 豆包管家
> MA 地址：`http://192.168.2.200:8086`
> MA 版本：v0.6（实体身份层 + 对外查询 + MQTT推送 + 记忆统一入库 + 治理增强）

---

## 一、概述

Memory-Agent（MA）是豆包管家生态的**记忆与设备洞察中枢**，提供：
1. **设备使用时长统计**（HDMI3/Xbox 防沉迷等）—— 免 ADB，直接 HTTP 查询
2. **逻辑设备身份层**—— entity_id 漂移免疫，按逻辑设备名查询
3. **成员在场实时推送**—— ArcFace 视觉识别结果，MQTT 订阅
4. **记忆统一入库**—— 生态各项目写入记忆，按 source 隔离
5. **设备健康监控**—— 失效设备清单，主动告警

---

## 二、鉴权

### 2.1 令牌类型

| 令牌类型 | 用途 | 访问范围 | 获取方式 |
|---|---|---|---|
| **app_token** | 应用层查询（TV/DP/小甜菜） | 仅 `/api/insights/query` + `/api/agent/memories` | MA WebUI → 设置 → 应用令牌，或 `POST /api/config/app-tokens` |
| **JWT（WebUI）** | 管理端 | 全部接口 | WebUI 登录获取 |
| **butler_token** | 豆包管家专用 | 全部接口 | 管家配置 |

### 2.2 app_token 特性（v0.6）

- **多令牌**：可为每个客户端（TV/DP/小甜菜）创建独立令牌，可单独吊销
- **按 token 派生 source**：令牌绑定 source（如 `vision`/`butler`），写入记忆时**强制采用**令牌的 source，忽略调用方 body 自报（防止伪造）
- **来源白名单**：默认 `["ma","butler","vision","manual"]`，新来源需在 config 扩展
- **永不过期**，仅可手动吊销（后续可加 scope/过期时间）

### 2.3 鉴权示例

```bash
# app_token 查询设备使用时长
curl -X POST http://192.168.2.200:8086/api/insights/query \
  -H "Authorization: Bearer <app_token>" \
  -H "Content-Type: application/json" \
  -d '{"template_id":"xbox_daily_usage","start":"2026-09-09","end":"2026-09-10"}'

# app_token 越权访问身份接口 → 403
curl -H "Authorization: Bearer <app_token>" http://192.168.2.200:8086/api/identity/devices
# → 403 Forbidden
```

---

## 三、核心接口

### 3.1 设备使用时长查询（防沉迷核心）

**`POST /api/insights/query`**

> **重要**：HDMI3/Xbox 使用时长现在可以直接通过此接口查询，**不需要 TVPilot 的 ADB 查询了**。MA 已从 TVPilot 采集 HDMI3 数据并持久化，提供统一查询接口。

#### 模式一：按模板查询（推荐，语义最稳定）

```json
{
  "template_id": "xbox_daily_usage",
  "start": "2026-09-09T00:00:00",
  "end": "2026-09-10T00:00:00"
}
```

#### 模式二：按逻辑设备即时查询（无需预存模板）

```json
{
  "logical_id": "客厅电视",
  "attribute": "source",
  "value": "HDMI 3",
  "metric": "duration",
  "days": 2
}
```

- `logical_id`：逻辑设备名（如"客厅电视"），由身份层解析为当前 entity_id，HA 重登/双集成漂移后依然可用
- `attribute` + `value`：筛选条件（如 source=HDMI 3）
- `metric`：`duration`（时长）/ `count`（次数）
- `days`：查询最近 N 天

#### 响应

```json
{
  "ok": true,
  "window": {"start": "...", "end": "...", "label": "近2天"},
  "entities": [
    {
      "entity_id": "media_player.live_9",
      "friendly_name": "客厅电视",
      "metric": "duration",
      "result": {"total_seconds": 7200, "total_hours": 2.0, "sessions": 3},
      "resolved": ["media_player.live_9"],
      "stale": false
    }
  ],
  "summary_text": "客厅电视近2天HDMI3使用时长：2.0小时（3次）"
}
```

- `stale: true`：设备已失效或待重匹配，`result.error` 包含原因（**不静默返回空**）

---

### 3.2 记忆写入（生态统一入库）

**`POST /api/agent/memories`**

生态各项目（TV/DP/小甜菜/管家）产生的记忆统一写入 MA，按 `source` 隔离。

#### 请求

```json
{
  "text": "Kevin 2026-09-09 玩 Xbox 2.5小时，超过工作日限制",
  "source_refs": ["insight:2026-001"],
  "source": "butler",
  "dry_run": false
}
```

- `text`：记忆内容
- `source_refs`：来源引用（可选）
- `source`：来源标识（`ma`/`butler`/`vision`/`manual`），**v0.6 下 app_token 会强制覆盖为令牌绑定的 source**
- `dry_run`：true 时只验证不写入

#### 低置信隔离机制（MA 内置，调用方无需处理）

1. 写入默认 `state=staging`，**永不自动进 live**，需 promote/sweep 晋升
2. 晋升需满足"高置信佐证 insight"或"跨 ≥N 天观测"——低置信摘要很难自动升级
3. 检索时可按 source 过滤，隔离低置信内容

---

### 3.3 记忆检索

**`POST /api/agent/memories/retrieve`**

```json
{
  "question": "Kevin 最近玩 Xbox 多久了？",
  "source": "ma",
  "limit": 5
}
```

- `source`：可选，按来源过滤（`ma`=仅高置信原生事实 / `butler`=仅管家摘要 / 不传=混合）

---

### 3.4 记忆列表

**`GET /api/agent/memories?source=butler&limit=20`**

---

## 四、MQTT 实时推送（v0.4）

> 为电视端设计的 HTTP 查询通道是 v0.3 的 `/api/insights/query`（电视端可能跑不动 MQTT 客户端）。MQTT 推送是给能订阅 MQTT 的客户端（管家/DP）用的。

### 4.1 主题与载荷

| 主题 | 触发时机 | retain | 载荷 |
|---|---|---|---|
| `ma/presence` | 周期（默认 60s），**内容变化才发** | ✅ | `{"members":[{"name","member_id","room","via","confidence","last_seen","trigger"}],"total":N,"ts"}` |
| `ma/device-health` | 对账发现状态变化（active↔unknown↔stale） | ❌ | `{"entity_id","stable_id","from","to","ts"}` |

### 4.2 ma/presence 载荷详解

```json
{
  "members": [
    {
      "name": "lidicn",
      "member_id": "lidicn",
      "room": "客厅",
      "via": "arcface",
      "confidence": 0.95,
      "last_seen": 1789000000,
      "trigger": "face_detected"
    }
  ],
  "total": 1,
  "ts": "2026-09-10T10:00:00"
}
```

- `via`：识别方式（`arcface`=视觉识别 / `device_tracker`=设备定位 / `person`=人员实体）
- `room`：中文房间名（如"客厅"/"书房"），消费方需自行映射到内部 room_id
- `confidence`：0-1，ArcFace 识别通常 ≥ 0.9

### 4.3 启用方式

MA 的 `.env`（`/vol1/1000/docker/memory-agent/.env`）：
```env
MA_MQTT_ENABLED=1
TV_MQTT_HOST=192.168.2.200
TV_MQTT_PORT=1883
TV_MQTT_USER=butler
TV_MQTT_PASS=HP…
```

- broker 复用 TV Cam 的 MQTT 配置
- v0.6 已支持 broker 不可达时周期重连（默认 30s 退避）

### 4.4 订阅验证

```bash
mosquitto_sub -h 192.168.2.200 -p 1883 -u butler -P 'HP…' -t 'ma/#' -v
```

---

## 五、身份层只读接口（仅 JWT，app_token 不放行）

### 5.1 逻辑设备清单

**`GET /api/identity/devices`**

```json
{
  "devices": [
    {"logical_id": "客厅电视", "entity_id": "media_player.live_9", "state": "active", "area": "客厅"},
    {"logical_id": "书房空调", "entity_id": "climate.study_ac", "state": "active", "area": "书房"}
  ],
  "total": 2
}
```

### 5.2 设备健康清单

**`GET /api/identity/health?state=stale`**

- `state`：`stale`（失效）/ `unknown`（未知）/ `active`（正常），不传返回全部

### 5.3 合并审计（v0.6）

**`GET /api/identity/merges`** — 自动合并的设备列表
**`POST /api/identity/merges/split`** — 拆分回滚（把 auto-merged 设备拆为独立 user-pinned 设备）

---

## 六、各项目对接建议

### 6.1 TVPilot

| 接口 | 用途 | 优先级 |
|---|---|---|
| `POST /api/insights/query` | HDMI3/Xbox 使用时长查询（**替代 ADB 查询**） | P0 必须 |
| `POST /api/agent/memories` | 写入电视端事件记忆（source=vision） | P1 |
| `ma/presence` MQTT | 发布 ArcFace 识别结果（MA 已从 TVPilot 采集，TV 无需订阅） | 已有 |

**关键变更**：HDMI3 防沉迷数据现在由 MA 统一采集和查询，TVPilot 不再需要通过 ADB 查询电视状态来获取使用时长。小甜菜防沉迷模块直接调用 MA 的 `/api/insights/query` 即可。

### 6.2 DeskPilot

| 接口 | 用途 | 优先级 |
|---|---|---|
| `POST /api/insights/query` | 查询设备使用时长（如电脑使用时长统计） | P2 |
| `POST /api/agent/memories` | 写入桌面操作记忆（source=butler） | P1 |
| `ma/presence` MQTT | 订阅成员在场，桌面端显示"谁在家" | P2 |
| `ma/device-health` MQTT | 订阅设备失效，桌面端通知 | P2 |

### 6.3 小甜菜

| 接口 | 用途 | 优先级 |
|---|---|---|
| `POST /api/insights/query` | **防沉迷核心**：查询 Kevin Xbox/HDMI3 使用时长 | P0 必须 |
| `POST /api/agent/memories` | 写入防沉迷事件记忆（source=vision） | P1 |
| 豆包管家 Bark/TTS API | 超时告警推送（已有交接单） | P0 已有 |

**防沉迷流程（更新后）**：
1. 小甜菜定时调用 `POST /api/insights/query`（template_id=xbox_daily_usage）获取 Kevin 当天 Xbox 使用时长
2. 超过阈值 → 调用豆包管家 Bark API 推送 critical 通知 + 小爱 TTS 警告
3. 调用 `POST /api/agent/memories` 写入防沉迷事件记忆（source=vision）
4. **不再需要** TVPilot ADB 查询电视状态

### 6.4 豆包管家

| 接口 | 用途 | 优先级 |
|---|---|---|
| `ma/presence` MQTT | 定位引擎最强信号源（ArcFace 视觉识别，权重 1.0） | P0 v1.5 |
| `POST /api/agent/memories` | 记忆统一入库（source=butler，专属 app_token） | P0 v1.5 |
| `ma/device-health` MQTT | 设备失效触发提醒 | P2 v1.6 |
| `query_device_usage` MCP | 逻辑设备名查询（漂移免疫） | P1 v1.6 |

---

## 七、注意事项

1. **app_token 只放行查询和记忆接口**，身份层/配置接口返回 403
2. **v0.6 source 强制派生**：用 app_token 写入记忆时，body 中的 source 字段会被忽略，强制采用令牌绑定的 source
3. **设备漂移免疫**：用 logical_id 查询时，即使 HA entity_id 变化也能正确解析
4. **stale 不静默**：设备失效时返回 `stale: true` + `result.error`，不会返回空数据
5. **MQTT 推送为旁路能力**：任何失败都不影响 MA 主流程，v0.6 已支持周期重连
6. **记忆默认 staging**：生态写入的记忆默认 state=staging，不自动进 live，需晋升

---

## 八、变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-10 | 初始版本：汇总 MA v0.2-v0.6 全部接口，明确各项目对接方式，HDMI3 查询替代 ADB |
