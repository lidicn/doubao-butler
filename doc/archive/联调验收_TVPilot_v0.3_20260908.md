# 验收节点 3：豆包管家 ReAct 联调验收报告

> 日期：2026-09-08
> 验收方：豆包管家（脑）
> 被测方：TVPilot v0.3.0（NAS:8090）
> 关联：`开发计划_轻量Agent框架_20260908.md` M2、TVPilot 交接单

---

## 一、验收环境

| 项 | 值 |
|----|-----|
| TVPilot 版本 | v0.3.0 |
| TVPilot 地址 | http://192.168.2.200:8090 |
| 电视状态 | 在线（adb_connected=true） |
| 电视型号 | 小米电视（前台 com.mitv.tvhome） |
| 管家侧工具数 | 24（含 9 个 TVPilot 工具：7 原子 + 2 combo） |
| 管家侧测试 | 52 全通过 |

---

## 二、场景验收

### 场景 1：换台（zap → 验证 mytv 前台）

| 步骤 | 结果 |
|------|------|
| zap 湖南卫视 | ❌ `mytv_api_error`：mytv HTTP API 不可达（10481 Connection refused） |
| mytv 启动 | ✅ foreground 从 com.mitv.tvhome → com.tvcam.mytv（mytv 成功启动到前台） |
| current 频道查询 | ❌ 同样 mytv API 不可达 |

**结论**：⚠️ **部分通过**。zap 工具能正确启动 mytv 到前台，但换台核心动作（调 mytv HTTP API /api/zap）失败。根因是 **mytv 应用的 HTTP 服务（端口 10481）未启动或不可达**。mytv 应用本身能正常启动和显示，但其内嵌 HTTP 服务模块可能未启用。

**建议**：TVPilot 侧确认 mytv HTTP API 的启动条件（是否需要 mytv 设置里开启远程控制/HTTP 服务，或端口是否变更）。

---

### 场景 2：搜索播放（combo/trim_search_play，中文输入）

| 步骤 | 结果 |
|------|------|
| launch 飞牛TV | ✅ |
| tap 搜索框 | ✅ |
| 中文输入「流浪地球」 | ✅ tvremoteime 广播方案成功 |
| 搜索 | ✅ |
| 选择首条 | ✅ |
| 播放 | ✅ |
| 前台验证 | ✅ com.trim.tv（飞牛TV） |
| 总耗时 | 14.1 秒 |

**结论**：✅ **通过**。6 步组合动作一气呵成，**P0 中文输入验证通过**（tvremoteime 广播方案可靠）。这是本次验收最重要的成果——搜索播放场景完全跑通。

---

### 场景 3：回桌面兜底（combo/go_home）

| 步骤 | 结果 |
|------|------|
| KEYCODE_HOME | ✅ |
| 前台验证 | ✅ is_home=true，com.mitv.tvhome |
| 耗时 | 1.9~2.9 秒 |

**结论**：✅ **通过**。回桌面组合动作可靠，带前台验证，可作为 ReAct 逃生通道。

---

## 三、错误码重试矩阵验证

| 注入错误 | 返回错误码 | 符合矩阵 |
|----------|-----------|---------|
| 无效频道「不存在的频道12345」 | `invalid_parameter` | ✅ |
| 无效按键「INVALID_KEY」 | `invalid_parameter` | ✅ |
| 无效包名「com.nonexistent.app」 | `wrong_foreground` | ✅ |
| mytv API 不可达 | `mytv_api_error` | ✅ |
| 电视离线（模拟） | `tv_unreachable` | ✅（单元测试覆盖） |

**结论**：✅ **通过**。所有错误码与交接单 §3.3 矩阵一致，管家侧 `_extract_error_code` 能正确解析并标记到轨迹。

---

## 四、操作存档对账（/api/history + ops.jsonl）

| 项 | 结果 |
|----|------|
| /api/history 接口 | ✅ 可用，返回最近操作记录 |
| 记录字段 | ✅ tool/ok/error/cost_ms/request_id 齐全 |
| request_id 全链路 | ✅ combo 动作带 request_id（fix-home-001、trim-search-xxx 等） |
| 存档数量 | ✅ 本次联调产生 20+ 条记录，可追溯 |

**结论**：✅ **通过**。操作存档可用于管家侧轨迹与 TVPilot 侧操作的对账复盘。

---

## 五、管家侧交付（联调期间完成）

| 项 | 说明 |
|----|------|
| `butler/integrations/tvpilot.py` | TVPilotClient：12 原子工具 + 2 combo（go_home/search_play）+ history + ensure_foreground |
| `tools.py` 注册 | 9 个 TVPilot 工具：tv_foreground/keyevent/launch_app/input_text/tap/swipe/screenshot + tv_go_home/tv_search_play |
| 观察验证器 | Agent 集成 async observe_fn：tv_launch_app / switch_channel 后自动 foreground 验证，结果回灌 ReAct |
| 配置 | `tvpilot_http_url`（默认 NAS:8090）+ 环境变量 `TVPILOT_HTTP_URL` |
| 测试 | `test_tvpilot_tools.py` 10 用例，全量 52 通过 |

---

## 六、发现的问题与建议

### P0：mytv HTTP API（10481）不可达 → 换台场景阻塞

- **现象**：zap 能启动 mytv 到前台，但调 mytv HTTP API 失败（Connection refused）
- **影响**：换台场景（场景 1）和 current/channels 查询不可用
- **建议**：TVPilot 侧确认 mytv HTTP 服务的启动条件。可能原因：
  1. mytv 应用需要在设置里开启"远程控制/HTTP 服务"
  2. mytv HTTP 服务端口变更（非 10481）
  3. mytv 启动后 HTTP 服务有延迟，需要等待后重试
- **临时方案**：换台可降级为 ADB 数字键直接注入（TVPilot 侧已有 channels.json 频道号映射，zap 工具可在 mytv API 不可达时降级为 adb input keyevent 数字键）

### P1：搜索播放耗时 14 秒，可优化

- combo/trim_search_play 总耗时 14 秒，其中 launch + wait 占比较大
- 建议：飞牛TV 已在前台时跳过 launch 步骤

### P2：管家侧 ReAct 端到端联调未执行

- 本次验收通过 curl/Python 直接调 TVPilot API 验证了工具层
- 管家侧 ReAct 闭环（LLM 规划 → 调工具 → 观察验证 → 调整）的端到端测试需要真实 LLM 调用
- 建议：电视保持在线时，通过 `POST /api/agent/chat` 发送"换到湖南卫视""在电视上放流浪地球"做端到端验证

---

## 七、验收结论

| 验收项 | 状态 |
|--------|------|
| 场景 1：换台 | ⚠️ 部分通过（mytv 启动✅，换台❌因 mytv HTTP API 不可达） |
| 场景 2：搜索播放 | ✅ 通过（含 P0 中文输入） |
| 场景 3：回桌面兜底 | ✅ 通过 |
| 错误码重试矩阵 | ✅ 通过 |
| 操作存档对账 | ✅ 通过 |
| 管家侧工具接入 | ✅ 通过（9 工具 + 观察器 + 52 测试） |

**总体结论**：**有条件通过**。核心场景（搜索播放、回桌面、错误码、存档）全部通过，P0 中文输入验证成功。换台场景因 mytv HTTP API 不可达而阻塞，需 TVPilot 侧排查 mytv HTTP 服务启动条件。管家侧 ReAct 端到端联调待电视保持在线时执行。

---

*报告生成：豆包管家（脑）开发者 | 2026-09-08*
