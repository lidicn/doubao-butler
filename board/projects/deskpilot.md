# DeskPilot 进度跟踪

> 项目路径：`E:\NAS\DeskPilot` | 部署：Windows 桌面（`C:\Users\lidicn\AppData\Local\Programs\DSH Desktop` 相关）
> API：`http://192.168.2.200:8765/api/v1` | 鉴权：Bearer deskpilot（开发默认）
> 更新：2026-09-08

---

## 一、版本路线

| 版本 | 内容 | 状态 | 完成时间 |
|------|------|------|---------|
| v2.0 | 工具层规范（统一包络/错误码/鉴权/审计） | ✅ | 2026-09-06 |
| v2.1 | 豆包管家 Agent 整合（13 固定任务工具） | ✅ | 2026-09-07 |
| v2.5 | 原子操控 MVP（5 desktop_* 工具） | ✅ | 2026-09-08 |
| v2.6 | UIA 语义操控（pywinauto，4 uia_* 工具） | ✅ | 2026-09-08（DP 代码 + 管家侧接入完成，真机联调待定） |
| v2.7 | SoM（点击准确率 <80% 时启动） | ⏳ | 规划中（触发式） |
| v3.0 | 完整 DeskPilot（所有工具稳定 + 技能沉淀 + 多显示器） | ⏳ | 规划中 |

**M8 SSH 约束加固**：P2，排后（5 道约束后才重新启用 SSH 工具）

---

## 二、当前状态

### ✅ v2.5 原子操控 MVP（刚完成）

**交付内容**：
- `backend/modules/desktop_control.py`：screenshot / click / type_text / swipe / press_key
- `backend/api/v1/desktop.py`：5 个 API 端点
- 安全闸：危险区域确认闸（回收站/系统托盘），未确认 → 409 confirmation_required
- 审计：所有动作经 AuditMiddleware 落盘 ops.jsonl
- DPI 感知：Per-Monitor v2 → system aware 降级，坐标与截图像素一致
- 中文输入：pyperclip + Ctrl+V（P0 方案，已验证闭环）

**P0 实测结果**（本机真实验证，非 mock）：
| # | 验证项 | 结果 |
|---|--------|------|
| 1 | DPI 感知（物理 2560×1080 → 截图 640×270，坐标一致） | ✅ |
| 2 | 截图非全黑（non_black_ratio=1.0） | ✅ |
| 3 | 用户桌面会话（process_session=1 == console_session=1） | ✅ |
| 4 | 中文输入闭环（记事本输入"你好世界"，UIA 读回逐字一致） | ✅ |

**测试**：517 passed / 0 failed（全量回归），新增 21 例（模块层 12 + API 层 9）

**管家侧接入状态**（v2.5 desktop 5 工具 + v2.6 UIA 4 工具均已完成）：
- `butler/integrations/deskpilot.py`：5 desktop + 4 uia 客户端方法（2026-09-08 补齐 uia）
- `butler/core/tools.py`：9 个 `desk_desktop_*` / `desk_uia_*` 工具注册 + 分发
- 观察验证器：click 后自动 screenshot 验证画面变化
- ReAct 策略：UIA 优先（先 uia_snapshot 拿控件树，能定位就用 uia_click/type，否则回退截图+坐标）
- 管家全量测试 **123 passed**（deskpilot 专项 21）

### ✅ v2.6 UIA 语义操控（M6，2026-09-08 完成代码 + 管家侧接入）

**交付内容**：
- `backend/modules/uia_control.py` + `backend/api/v1/uia.py`：uia_snapshot / uia_click / uia_type / uia_wait
- DP 全量测试 **603 passed**（含 proxy 格式 bug 20260908_002 修复回归）
- 管家侧 4 个 `desk_uia_*` 工具接入（本次完成，见 doubao-butler 项目页 M6.4）

---

## 三、工具清单

### 固定任务工具（v2.1，13 个，已接入管家）
lxmusic_search_play / lxmusic_control / volume_get / volume_set / windows_list / windows_activate / windows_close / screenshot / health / system_info / open_url / ssh_connect / ssh_exec（SSH 已从管家 LLM 移除，P2）

### 原子操控工具（v2.5，5 个，已接入管家）
desktop_screenshot / desktop_click / desktop_type / desktop_swipe / desktop_key

### UIA 语义操控工具（v2.6，4 个，已接入管家）
uia_snapshot / uia_click / uia_type / uia_wait

### 规划中工具
- v2.7 SoM：som_annotate（触发式）

---

## 四、联调验收记录

| 联调项 | 日期 | 场景 | 结果 | 报告 |
|--------|------|------|------|------|
| 固定任务工具 | 2026-09-08 | LX Music/音量/窗口激活 | ✅ 3/3 | `docs/M2_联调准备与回执处理.md` |
| 原子操控工具 | 待 | 记事本输入/浏览器搜索/拖拽窗口/技能模板生成 | ⏳ | 等管家接入 |

---

## 五、下一步（PM 视角）

1. **🔄 等管家接入 M5**：管家侧 0.5 天接入 5 个 desktop_* 工具 → 联调验收 4 场景
2. **⏳ v2.6 UIA 语义操控**：pywinauto 已装，uia_snapshot/uia_click/uia_type/uia_wait，1-2 周
3. **⏳ 多显示器验证**：mss 支持，本机单屏，待双屏环境回归
4. **⏳ M8 SSH 约束加固**：P2，排后（5 道约束：主机白名单/命令过滤/只读默认+WebUI开关/确认闸/审计+超时）
5. **⏳ v2.7 SoM**：触发式（点击准确率 <80% 才启动）

---

## 六、与豆包管家的依赖关系

| 依赖项 | 方向 | 状态 |
|--------|------|------|
| DP 固定任务工具 → 管家 ReAct | DP 提供工具，管家调用 | ✅ 已接入（11 工具，SSH 已移除） |
| DP 原子操控工具 → 管家 ReAct | DP 提供工具，管家调用 | 🔄 等管家接入（5 工具） |
| 管家技能沉淀 → DP 成功轨迹 | 管家从联调成功轨迹生成技能模板 | ⏳ 等原子工具联调 |
| DP UIA 语义操控 → 管家结构化观察 | DP 提供能力，管家减少截图依赖 | ⏳ 待 v2.6 |
| DP SSH → 管家 NAS 运维 | 已降级，管家走本地工具（new-api SQLite + docker.sock） | ✅ 已替代 |

---

## 七、风险与问题

| # | 问题 | 等级 | 影响 | 状态 |
|---|------|------|------|------|
| 1 | 原子工具联调可能发现坐标/DPI 问题 | P2 | 联调延期 | DP 已做 DPI 感知和 P0 验证，联调重点验证 |
| 2 | 多显示器未验证 | P2 | 双屏环境坐标可能偏移 | 待双屏环境回归 |
| 3 | SSH 安全风险 | P2 | 误操作/安全漏洞 | 已降级 P2，5 道约束后才重新启用 |
| 4 | 中文输入覆盖剪贴板 | P3 | 用户体验 | pyperclip+Ctrl+V 方案固有问题，v2.6 UIA 可改善 |

---

## 八、交接单记录

| 编号 | 类型 | 主题 | 状态 | 日期 |
|------|------|------|------|------|
| （历史） | 需求 | 工具层对接（13 固定任务工具） | ✅ 已完成 | 2026-09-08 |
| （历史） | 变更 | SSH 降级 P2 + 5 道约束 | ✅ 已完成 | 2026-09-08 |
| 20260908_001 | 问题+需求 | Session0 桌面操控阻塞（双进程方案 + token 节省） | 🔄 进行中（DP 已回复并实现，待真机联调） | 2026-09-08 |
| 20260908_002 | 问题（Bug） | proxy 模式 screenshot result 格式 bug | ✅ 已修复（desktop_bridge 补 _restore_tuple，9 工具 proxy/local 一致，603 passed；真机复验待用户会话） | 2026-09-08 |
| 20260908_004 | 变更/交接 | **DP Agent 更换项目交接** | ✅ 已确认接手（接任 Agent 已通读交接并修复 002） | 2026-09-08 |
| 20260908_005 | 问题 | 看板系统接入 + proxy 截图格式 bug 修复 | ✅ 已完成（TASK-20260908-003 已上报完成，见 board/replies/20260908_005_reply_DeskPilot.md） | 2026-09-08 |
| 20260908_004(插件) | 需求 | PM 消息转发插件集成（POST /api/v1/pm-forward/send，经 desktop_agent 执行） | ✅ 已完成（代码+测试 625 passed；真机联调待用户会话，见 board/replies/20260908_004_reply_DeskPilot.md） | 2026-09-08 |
| （待发） | 联调 | M5/M6.5 原子操控联调（4 场景验收） | ⏳ 等 DP 真机代理就绪后发 | 2026-09-09 |

### M6.5 双进程（2026-09-08 完成代码）

- ✅ `desktop_agent`（Session 1 用户态代理，127.0.0.1:18765）+ `desktop_bridge`（auto/local/proxy）
- ✅ token 节省：screenshot include_image=false 默认 + 480px + non_black_ratio
- ✅ 心跳/状态端点 + Startup 自启脚本 + config 段
- ✅ 测试：603 passed（新增 bridge/agent/token 用例；20260908_002 proxy result 格式 bug 已修复，再 +8 回归用例）
- ⏳ 真机联调（09-09，需用户会话安装 `scripts/install_desktop_agent.ps1`）

---

*更新：2026-09-08 | PM：豆包管家（兼任 DEV）*
*DP v2.6 双进程方案代码完成，Session 0 阻塞已解（待真机代理联调）。*
*✅ Agent 更换交接已完成：接任 Agent 已通读并接手，P1 bug 20260908_002（proxy result 格式）已修复，全量 603 passed；真机 proxy 复验待用户登录会话。*
