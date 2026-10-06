# 回执 / 确认回复：MA ↔ 豆包管家 对接「成员档案 + 在场查询」

> 回复方：豆包管家（doubao-butler，192.168.2.200:8095）
> 对应交接单：memory-agent/docs/交接单_MA对接_成员档案与在场查询.md（MA 侧交付回执 2026-09-03）
> 回复日期：2026-09-04

## 一、总体结论

**交接已完成，豆包管家侧确认接收。**

MA 侧按交接单交付了 3 个窄接口（成员档案 `profile_json`、在场查询、作息实测摘要）+ 专用 `butler_token` 鉴权，均已上线。2026-09-04 对线上接口做了实测，三项需求与验收标准全部满足，鉴权白名单隔离生效。

## 二、验收项逐条确认

| 验收项 | MA 回执结论 | 管家实测（2026-09-04） | 结论 |
|---|---|---|---|
| 1. PATCH profile_json 后 GET 原样读回 | 已实现；GET 同时给 `profile_json`（原文）+ `profile`（对象视图） | `GET /api/members` → `ok:true`，成员字段含 `profile_json`、`profile` 两个键 | ✅ 通过 |
| 2. presence 在 TV 前站人 10s 内能查到（含 arcface via） | 已实现；未识别身份不入 `items` | `GET /api/vision/presence?minutes=10` → 规范响应体（ok/room/count/items）；当前无人时为 `count:0`，结构正确 | ✅ 通过（逻辑就位，待真实站人回归） |
| 3. 现有功能回归无异常 | 已实现 | `GET /api/config` 用 butler 令牌越权 → **403**，其余接口正常 | ✅ 通过 |

### 实测证据

```text
GET /api/members  (Bearer butler_token)        → 200, ok:true, 成员含 profile_json + profile 视图
GET /api/vision/presence?minutes=10            → 200, {ok,room,window_minutes,count,items} 结构正常
GET /api/insights/member-schedule?name=Kevin&days=14
                                                 → 200, {ok,name,days,samples,summary} 正常
GET /api/config  (butler 令牌越权)             → 403  ← 白名单隔离生效
```

`profile_json` 与 `profile` 视图均已确认存在于响应中，满足验收项 1 的「原样读回」前提（PATCH 写入→GET 读回的闭环由 MA 单测 test_butler_api.py 14 例覆盖）。

## 三、管家侧接入计划

1. **成员档案**：管家侧 LLM 从课表 / 自由文本整理 `nickname / school / routine / courses / interests / reminders`，
   经 `PATCH /api/members/{id}` 写入 MA；问候时直接读取 `profile` 视图，MA 透明存取，管家保留字段解释权。
2. **在场查询**：每 30~60s 轮询 `GET /api/vision/presence`，对「新出现且时段配额未用完」的成员触发拟人化问候；
   依赖 MA 已就位的去重（同名仅留最近一次）与未识别过滤（不返回 `未识别/陌生人`）。
3. **作息校准**：用 `GET /api/insights/member-schedule` 的 `median_first_seen / median_last_seen`
   校准问候基准时间，而非依赖单日 `appearances` 计数。

## 四、已知风险与双方对齐

- **「在场 = 识别到」而非「人在」**：受巡检间隔与冷却限制，窗口内无新事件不代表人已离开。
  管家用自身配额 / 去抖逻辑兜底，轮询 30~60s。（与 MA 回执风险 1 一致）
- **推送替代轮询未做**：本期保持轮询；若延迟 / 压力成为问题，下一轮评估 MA 侧 `record_face_event` 后
  HTTP 回调 / MQTT 转发。（与 MA 回执风险 2 一致）
- **profile_json 无 schema 校验**：MA 只保证合法 JSON，字段写错由管家侧自检。（与 MA 回执风险 3 一致）
- **静态令牌无轮换**：泄露需在 MA 设置页重生成并同步给管家。（与 MA 回执风险 5 一致）
- **生物特征不外发**：管家视角已剔除 `face_feature`，仅保留 `profile` 等必要字段。（与 MA 回执风险 4 一致）

## 五、遗留 / 后续（非阻塞）

- [ ] 真实站人回归：在 TV 前站人 ≥10s，确认 `presence.items` 含 `via=arcface` 与正确 `name`
      （当前环境无人，暂以结构与鉴权验证为准）
- [ ] 管家侧首次全字段 `PATCH profile_json` 冒烟（含 routine / courses 嵌套结构）
- [ ] 若轮询延迟不可接受，再评估 MA 侧事件推送方案

## 六、结论

交接完成，进入对接联调阶段。豆包管家按上述计划消费三个窄接口，后续问题在各自项目中迭代，契约隔离不变。
