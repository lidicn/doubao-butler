# 豆包管家进度跟踪

> 项目路径：`E:\NAS\doubao-butler` | NAS 部署：`/vol1/1000/docker/doubao-butler`
> API：`http://192.168.2.200:8095` | 鉴权：Bearer <redacted>
> 更新：2026-09-08

---

## 一、版本路线

| 版本 | 内容 | 状态 | 完成时间 |
|------|------|------|---------|
| v1.0 | 基础框架（FastAPI/ReAct/工具层/技能系统） | ✅ | 2026-09-06 |
| v1.1 | TVPilot 接入 + ReAct 闭环 + agent_collab 真异步 + 429 退避 | ✅ | 2026-09-07 |
| v1.2 M5 | DeskPilot 固定任务接入（11 工具） | ✅ | 2026-09-08 |
| v1.2 M5.5 | NAS 运维能力（new-api + docker 封装） | ✅ | 2026-09-08 |
| v1.2 M6 | DeskPilot M5 原子工具接入（5 desktop_* 工具） | ✅ | 代码+测试完成（待真机联调） |
| v1.2 M6.4 | DeskPilot v2.6 UIA 语义操控接入（4 uia_* 工具） | ✅ | 2026-09-08（代码+测试 123 passed） |
| v1.3 | 技能沉淀 + 多模态观察增强 + 记忆整合 | ⏳ | 规划中 |

---

## 二、当前任务

### 🔄 M6：DeskPilot M5 原子工具接入

**背景**：DP v2.5 已完成原子操控 MVP（5 工具，517 测试全过，P0 全通过），等管家侧接入。

**任务分解**：
1. `butler/integrations/deskpilot.py` 加 5 个方法（screenshot/click/type/swipe/key）
2. `butler/core/tools.py` 注册 5 个 `desk_desktop_*` 工具
3. 观察验证器：`desktop_click` 后自动 `desktop_screenshot` 验证画面变化
4. 危险操作确认闸：`desktop_click` 命中危险区域时，向用户确认后带 confirm=true 重试
5. 部署 NAS + 冒烟测试
6. 联调验收 4 场景

**预计工期**：0.5 天（接入）+ 0.5 天（联调）

**依赖**：DP v2.5 已部署（`http://192.168.2.200:8765`）

### ✅ M6.4（2026-09-08）：DP v2.6 UIA 语义操控 4 工具接入

- `butler/integrations/deskpilot.py`：新增 `uia_snapshot / uia_click / uia_type / uia_wait` 4 个客户端方法；
- `butler/core/tools.py`：注册 4 个 `desk_uia_*` 工具 schema + 分发逻辑；
- ReAct 策略：工具描述注入「UIA 优先」——先 `desk_uia_snapshot` 拿控件树，能定位就用
  `desk_uia_click/type` 精确操作，UIA 不可用才回退 `desk_desktop_screenshot+click`；
- 测试：deskpilot 专项 21 passed（+4 分发 +2 schema 策略），管家全量 **123 passed**；
- 遗留：真机联调 4 场景（记事本输入/浏览器搜索/拖拽/UIA 等待对话框）待 DP 桌面代理就绪。

---

## 三、工具清单（56 个）

### TVPilot 工具（12）
tv_screenshot / tv_keyevent / tv_tap / tv_swipe / tv_input_text / tv_launch_app / tv_switch_channel / tv_current_channel / tv_channels / tv_combo_go_home / tv_combo_search_play / tv_health

### DeskPilot 固定任务工具（11）
desk_system_status / desk_system_notify / desk_system_run / desk_volume_get / desk_volume_set / desk_volume_mute / desk_windows_list / desk_windows_activate / desk_windows_maximize / desk_windows_close / desk_music_play

### DeskPilot 原子操控工具（5，v2.5）
desk_desktop_screenshot / desk_desktop_click / desk_desktop_type / desk_desktop_swipe / desk_desktop_key

### DeskPilot UIA 语义操控工具（4，v2.6）
desk_uia_snapshot / desk_uia_click / desk_uia_type / desk_uia_wait

### NAS 运维工具（8）
newapi_list_channels / newapi_list_models / newapi_get_model_allocation / newapi_set_model_allocation / docker_ps / docker_restart / docker_logs / docker_compose_up

### 系统/其他工具（12）
agent_collab_delegate_task / memory_search / memory_store / tts_speak / web_search / weather_get / calendar_query / todo_list / todo_add / todo_complete / skill_list / skill_execute

---

## 四、联调验收记录

| 联调项 | 日期 | 场景 | 结果 | 报告 |
|--------|------|------|------|------|
| TVPilot v0.3 | 2026-09-08 | 换台/搜索播放/回桌面/错误重试/存档对账 | ✅ 4/4 + 错误矩阵 | `doc/联调验收_TVPilot_v0.3_20260908.md` |
| DeskPilot 固定任务 | 2026-09-08 | LX Music/音量/窗口激活 | ✅ 3/3 | `doc/回执_DeskPilot工具层对接.md` |
| NAS 运维 | 2026-09-08 | new-api 渠道/模型/分配 + docker ps | ✅ 4/4 | （本轮，真机验证） |
| DeskPilot 原子操控 | 待 | 记事本输入/浏览器搜索/拖拽/技能模板 | ⏳ | 待 M6 完成 |

---

## 五、下一步计划

1. **M6 真机联调**（待 DP 桌面代理就绪）：记事本输入/浏览器搜索/拖拽窗口/UIA 等待对话框 4 场景 + ReAct 实测 UIA 优先策略
2. **技能沉淀**（M6 后）：从联调成功轨迹生成技能模板（如 `open_notepad_type_text`）
3. **v1.3 规划**：
   - 多模态观察增强（结构化优先，截图兜底，减少 token）
   - 技能市场（技能模板的存储/检索/版本/分享）
   - 记忆整合（操作历史/用户偏好/设备状态的长期记忆）
   - 多设备协调（TV + Desktop 联动场景）

---

## 六、风险与问题

| # | 问题 | 等级 | 状态 |
|---|------|------|------|
| 1 | 多模态截图 token 消耗大 | P2 | v1.3 规划智能观察 |
| 2 | ReAct 循环偶发 429 | P2 | 已有 1.5s sleep + 退避，new-api 已扩容 |
| 3 | 技能沉淀依赖原子工具联调 | P1 | 管家侧已接入 9 工具（desktop+uia），真机联调通过后启动 M3 技能沉淀 |

---

*更新：2026-09-08 | PM：豆包管家（兼任 DEV）*
