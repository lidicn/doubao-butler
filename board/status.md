# 生态状态快照

> 更新：2026-09-09 14:45 | PM：豆包管家（兼任 DEV）
> 下次更新：2026-09-10

---

## 一、总览

| 项目 | 当前版本 | 状态 | 下一步 | 阻塞 |
|------|---------|------|--------|------|
| **豆包管家** | v1.2 M6.5 ✅ | ✅ 已完成 | 技能沉淀 / v1.3 规划 | 无 |
| **TVPilot** | v0.7（已完成） | ✅ | v1.0 整合联调 | Arcface 未注册人脸（v0.5 端到端待验证） |
| **DeskPilot** | v2.3（多端与唤醒） | ✅ 已上线 | v2.3 PWA桌面操控面板+DSH可选降级+语音唤醒评估完成；下一步 v2.4 发布稳定 | 无 |

**整体健康度**：🟢 良好。任务看板模块（M6.5）端到端验证通过，PM→TP/DP 工单系统完整闭环。

---

## 二、各项目详细状态

### 2.1 豆包管家

**当前版本**：v1.2 M6.5（任务看板模块 + PM 调度系统完整闭环）

**已完成**：
- ✅ v1.1 ReAct 闭环 / TVPilot 接入 / 技能系统 / agent_collab 真异步 / 429 退避
- ✅ v1.2 M5 DeskPilot 固定任务接入（11 工具，3/3 联调通过）
- ✅ v1.2 M5.5 NAS 运维能力（new-api 封装 4 工具 + docker 封装 4 工具，4/4 真机验证通过）
- ✅ Board 项目管理体系（本目录 + 开发规范 + PM 手册）
- ✅ v1.2 M6 DeskPilot 原子操控工具接入（5 个 desk_desktop_*，48 工具总数，113 测试全过）
- ✅ M6 联调验收 4/4 场景通过（记事本输入/浏览器搜索/拖拽窗口/错误恢复+确认闸）
- ✅ 坐标自动换算机制（截图坐标↔原始屏幕坐标，智能判断）
- ✅ 组合键解析（"win+right"→key+modifiers）
- ✅ max_iter 8→15，time_budget 60→120s
- ✅ M6.5 PM 调度系统：PM→TP（Ctrl+2）/ PM→DP（Ctrl+3）即时消息通道端到端验证
- ✅ M6.5 交接单文档生成（双副本同步 board/handoffs/ + 项目 docs/）
- ✅ M6.5 任务看板模块（SQLite 存储 + 5 API + 状态机 + 超时检测 + 事件历史）
- ✅ M6.5 assign_task_to_tp/dp 集成任务创建 + 上报命令（工具总数 52）
- ✅ M6.5 task_report.py 通用上报脚本（解决 PowerShell 中文乱码）
- ✅ M6.5 端到端验证：TP 模拟接手/完成上报，看板统计正确，事件历史完整

**当前任务**：
- 🔄 技能沉淀（从联调成功轨迹生成技能模板，M7）

**下一步**：
- ⏳ 技能沉淀 M7（等原子工具联调通过后，从成功轨迹生成技能模板）
- ⏳ v1.3 规划（多模态观察增强 / 技能市场 / 记忆整合）

**工具总数**：48（TVPilot 12 + DeskPilot 16 + NAS 运维 8 + 系统/其他 12）

---

### 2.2 TVPilot

**当前版本**：v0.7（已完成，含 IPTV/EPG 代理 + 用户管理 + 音频修复）

**已完成**：
- ✅ v0.1 基础换台 & PWA
- ✅ v0.2 标准化工具接口（12 工具，统一包络/错误码/health）
- ✅ v0.3 场景组合动作 + 联调支撑（combo_go_home / combo_search_play / history / tvremoteime 中文输入 / request_id 全链路）
- ✅ v0.4 无障碍服务（Arcface A11yHttpServer :8081，控件级操作）
- ✅ v0.5 智能状态识别（OCR / ui_state / play_state / screenshot_smart）
- ✅ v0.6 系统状态聚合 + 音频控制 + 无障碍增强
- ✅ v0.7 IPTV 源代理（97 频道 8 组分类）+ EPG 代理（51zmt 源+频道名映射）+ 用户管理（FaceID 自动切换）+ go2rtc 音频修复（MP2→AAC）
- ✅ 与豆包管家联调通过（换台/搜索播放/回桌面/错误码重试矩阵/操作存档对账）
- ✅ mytv 子项目 v0.1-v0.6（包名 com.tvcam.mytv / 97 频道源 / 音频修复 / 频道分类 / EPG / 收藏 API / 最近观看 / FaceID / 启动优化）

**文档已同步**：
- ✅ board/projects/tvpilot.md 已更新到 v0.7 实际进度
- ✅ 原"文档滞后警告"已消除

**已知问题**：
- ⚠️ Arcface 引擎未就绪、未注册人脸（v0.5 FaceID 端到端验证阻塞，需用户配合）
- ⚠️ mytv 冷启动 8 秒（目标 <5 秒，v0.6 深度优化待用户反馈卡顿点）
- ⚠️ ADB 连接不稳定（电视休眠断连，工具层自动重连）

**下一步**：
- ⏳ v0.5 FaceID 端到端验证（需用户在 Arcface 注册人脸）
- ⏳ v0.6 深度优化（需用户反馈具体卡顿场景）
- ⏳ v1.0 整合与联调（所有工具稳定 + 全功能联调 + 文档完善）
- ⏳ 与 memory-agent 对接（观看历史数据接口）

---

### 2.3 DeskPilot

**当前版本**：v2.3（多端与唤醒；PWA 桌面手动操控面板 + DSH 可选降级 + 语音唤醒评估）

**已完成**：
- ✅ v2.0 工具层规范（统一包络/错误码/鉴权/审计）
- ✅ v2.1 豆包管家 Agent 整合（13 固定任务工具接入，3/3 联调）
- ✅ v2.5 原子操控 MVP（5 工具：desktop_screenshot/click/type/swipe/key，517 测试全过，P0 全通过）
  - DPI 感知 ✅ / 截图非全黑 ✅ / 用户会话 ✅ / 中文输入闭环 ✅
  - 危险区域确认闸 ✅ / 审计日志 ✅
- ✅ M6 UIA 语义操控（uia_snapshot/uia_click/uia_type/uia_wait，40 用例）
- ✅ M6.5 Session0 双进程（交接单 20260908_001 已回复接受并实现）：
  - desktop_agent（用户态代理 127.0.0.1:18765，desktop/uia 9 动作）+ desktop_bridge（auto/local/proxy）
  - token 节省：screenshot include_image=false 默认 + 480px + non_black_ratio + foreground_window_title
  - 心跳/状态端点 + Startup 自启脚本 + config 段；代理离线 → 503 明确提示（不冒泡 BitBlt）

**当前状态**：
- ✅ 交接单 20260908_001 已回复（接受），代码完成，全量 595 passed
- ✅ PM 代码验收通过（desktop_agent/desktop_bridge 设计清晰，token 节省三件套落地，心跳/审计/自启齐全）
- ✅ 管家侧 M6 联调已通过（GUI 线程临时方案，4/4 场景）
- ✅ P1 bug 20260908_002（proxy result 格式）已修复：desktop_bridge 补 _restore_tuple，9 桌面工具 proxy/local 格式一致；回归 +8，**全量 603 passed**（真机 proxy 复验待用户登录会话）
- ✅ 部署修复 20260908（dp.local 不可用）：清理三重启动冲突（计划任务/损坏 lnk/Hermes 禁用桩），服务改 auto 自启，desktop_agent pythonw 静默运行，验证 625 passed
- ✅ 网关假死修复 20260908（前端全面报错）：根因 memory `_run_coro` 在 async 路由内嵌套事件循环 → 整个 uvicorn 事件循环挂死；修复：`_run_coro` 改独立线程执行 + memory 路由 `asyncio.to_thread` 卸载 + Supervisor 增加 /health 假死看门狗（连续 3 次失败自动重启）。**全量 625 passed**（详见 docs/网关挂死修复_20260908_事件循环死锁.md）
- ✅ Agent 对话持久化（切 tab/刷新不丢）：Agent.vue localStorage 持久化 + 流式显式落盘 + assistant content 改响应式引用；真实浏览器 E2E 验证通过
- ✅ windows/* 认证失败排查：确认为 pytest 测试日志噪音（401 用例/boom mock 写同一 log），非真实客户端；顺带修 butler 默认端口 8766→8765、认证失败日志加 client IP
  - ✅ PM 消息管道集成（交接单 20260909_003，P0）：新增 POST /api/v1/pm/send + GET /api/v1/pm/health；消息发送器运行在 desktop_agent（Session 1，ctypes UTF-16 剪贴板，PM/TP/DP 多目标 Ctrl+1/2/3，可配置）；审计日志 logs/pm_send.jsonl（按天轮转+保留天数）+ config pm_messenger 段；**真实发送 E2E 通过（用户确认消息到达豆包 DP 对话）**，全量 **659 passed**；详见 docs/20260909_003_PM消息管道_DeskPilot插件_完成报告.md
  - ✅ PM 消息管道插件化（前端可见 + 可开关 + 实时状态）：新建 backend/plugins/pm_messenger.py 插件（name=pm_messenger），pm_messenger_control 加运行时开关（set_runtime_enabled），/send 加 503 门禁（插件停用时拒绝转发）；GET /api/v1/plugins 现返回 2 个插件，status 走 desktop_bridge 代理反映 Session 1 真实桌面（doubao_window_found=true）；+1 门禁测试，**35 passed**
- ✅ v2.7 SoM 精确定位：desktop_screenshot(annotate=true) 叠加 4x6 数字网格返回 marks（数字→虚拟屏幕坐标映射 JSON）；desktop_click(mark=N) 按标记点击；三层（api→bridge→agent→desktop_control）全链路贯通；+12 桌面/桥测试（含 mark_not_found、缺参 400），**全量 669 passed**；真机 E2E：annotate 返回 24 marks、click(mark) 真实点击、mark_not_found 400；详见 docs/v2.7_SoM精确定位_交付报告.md
- ✅ v2.2 技能化沉淀+可观测增强：audit 中间件提取 X-Session-Id 并记录；trace_store 加 session_id 过滤 + read_sessions 会话列表；skill_miner 按会话分组挖掘（_group_by_session + 时间隙回退），跨会话 support 累加；新增 GET /api/v1/traces/sessions 端点；自动挖掘触发（累计 auto_mine_every=30 次成功操作后台 daemon 触发 mine_drafts）；+11 测试（4 新测试类），**全量 680 passed**；E2E 全链路通过（session_id 记录/16 会话列表/过滤/挖掘/自动挖掘触发）；详见 docs/v2.2_技能化沉淀_交付报告.md
- ✅ v2.3 多端与唤醒：PWA 桌面手动操控面板（新增 Desktop.vue 页面 + desktop.js API，支持截图/点击坐标换算/SoM标注精确点击/中文输入/快捷键/自动刷新/操作日志，响应式适配）；DSH 模块降级为可选（dsh.enabled=false 默认禁用，run 返回 dsh_disabled，+4 测试）；语音唤醒可行性评估（4 方案对比，推荐 Vosk 离线）；前端 57 模块构建成功，**全量 684 passed**；E2E 全链路通过；详见 docs/v2.3_多端与唤醒_交付报告.md
- ⏳ 双进程方案真机验证待用户配合：管理员启动 DP Service + 用户会话安装桌面代理 → 验证 proxy 模式桌面操控

**下一步**：
- ⏳ v2.4 发布稳定：Windows 服务化 + Supervisor 守护；安全加固；文档与实现一致；发布 v1.0.0
- ⏳ 真机代理联调（09-09 窗口，需用户会话）→ 管家接入桌面/uia 工具联调 4 场景
- ⏳ M8 SSH 约束加固（P2，排后，5 道约束后才重新启用）

**测试覆盖**：684 passed / 0 failed（全量回归；含 20260908_002 proxy result、004 PM 转发、005 管道、网关假死修复）

---

## 三、联调状态

| 联调项 | 状态 | 说明 |
|--------|------|------|
| 豆包管家 ↔ TVPilot | ✅ 已通过 | v0.3 联调验收（换台/搜索播放/回桌面/错误重试/存档对账） |
| 豆包管家 ↔ DeskPilot 固定任务 | ✅ 已通过 | v2.1 联调（3/3 场景：LX Music/音量/窗口激活） |
| 豆包管家 ↔ DeskPilot 原子操控 | ✅ 已通过 | M6 联调验收 4/4 场景（记事本输入/浏览器搜索/拖拽窗口/错误恢复+确认闸），坐标自动换算+组合键解析修复 |
| 豆包管家 ↔ new-api | ✅ 已通过 | 直连 SQLite，3/3 场景（渠道/模型/分配查询） |
| 豆包管家 ↔ Docker | ✅ 已通过 | docker.sock，1/1 场景（容器列表） |

---

## 四、风险登记

| # | 风险 | 等级 | 影响 | 应对 | 状态 |
|---|------|------|------|------|------|
| R1 | ~~TVPilot 文档滞后于实际开发~~ | ~~P1~~ | 已同步更新到 v0.7 | ✅ 已解决 |
| R2 | ~~mytv HTTP API 10481 不可达~~ | ~~P1~~ | 已修复（mytv 重新安装配置） | ✅ 已解决 |
| R3 | DP Session 0 阻塞原子操控联调 | P0 | 所有桌面工具不可用，联调完全阻塞 | 双进程方案代码已交付（595 passed），GUI 线程临时方案联调已通过，长期方案真机验证待用户配合 | ✅ 代码已交付，联调已突破（临时方案），真机验证待配合 |
| R8 | DP 进程稳定性 | P1 | 联调过程中 DP 进程挂了一次，全部 API 超时 | 双进程方案已交付，proxy 模式真机验证核心链路通过（代理在线+type经代理成功）；截图格式 bug（20260908_002）已修复 | ✅ 已解决（proxy bug 已修复，603 passed） |
| R10 | ~~DP proxy 模式截图 result 格式 bug~~ | ~~P1~~ | ~~proxy 模式下 screenshot 返回 result 为 list 而非 dict，管家侧报错~~ | 已修复：desktop_bridge 补 _restore_tuple，9 工具 proxy/local 一致，603 passed | ✅ 已解决 |
| R9 | 8765 端口僵尸进程占用 | P2 | 原端口无法使用，临时用 8766 | 需重启电脑释放，管家 config.py 已改为 8766，长期方案实现后改回 8765 | ⏳ 待重启电脑 |
| R4 | 多模态截图 token 消耗 | P2 | 成本/延迟 | v1.3 规划智能观察（结构化优先，截图兜底），TP v0.5 OCR 可复用 | ⏳ 规划中 |
| R5 | SSH 安全风险 | P2 | 误操作/安全漏洞 | 已降级为 P2，固定脚本优先，5 道约束后才重新启用 | ✅ 已缓解 |
| R6 | Arcface 引擎未就绪/未注册人脸 | P1 | TVPilot v0.5 FaceID 端到端验证阻塞 | 需用户在 Arcface 中注册人脸并检查引擎状态 | ⏳ 待用户配合 |
| R7 | mytv 冷启动 8 秒（目标 <5 秒） | P2 | 用户体验 | v0.6 已做 EPG 异步优化，深度优化待用户反馈卡顿点 | 🔄 进行中 |

---

## 五、需用户决策事项

| # | 事项 | 背景 | 建议 | 状态 |
|---|------|------|------|------|
| D1 | ~~TVPilot 文档更新要求~~ | ~~TP 实际到 v0.5 但路线图还标 v0.3~~ | 已同步更新到 v0.7 | ✅ 已完成 |
| D2 | ~~mytv API 不可达处理~~ | ~~10481 端口不可达，换台走 fallback~~ | 已修复 | ✅ 已完成 |
| D3 | DP M5/M6.5 联调窗口 | DP 双进程实现完成 | 联调已通过（GUI 线程临时方案），长期双进程方案待 DP 排期实现 | ✅ 联调已完成，长期方案待 DP |
| D4 | Arcface 人脸注册 | TVPilot v0.5 FaceID 端到端验证需 Arcface 注册人脸 | 需用户在电视 Arcface 应用中注册人脸并确认引擎就绪 | ⏳ 待用户配合 |

---

## 六、决策记录

| 日期 | 决策 | 决策方 | 影响 |
|------|------|--------|------|
| 2026-09-08 | 豆包管家自研轻量 ReAct，不引开源框架 | 用户 | 架构方向确定 |
| 2026-09-08 | DeskPilot SSH 降级 P2，从 LLM 移除 | 用户 | 安全策略，5 道约束后才重新启用 |
| 2026-09-08 | NAS 运维走管家本地工具（new-api SQLite + docker.sock），不绕 DeskPilot 跳板 | 用户 | 架构简化，少一跳故障点 |
| 2026-09-08 | OpenCode 定位开发期编码工具，不做运行时 SSH 运维 | 用户 | 工具定位明确 |
| 2026-09-08 | PM 兼任 DEV，不搞专职 PM | 用户+PM | 当前阶段最合适 |
| 2026-09-08 | Board 项目管理体系建立 | PM | 监控/协调/交接规范化 |

---

*本快照每 2 天更新。下次更新：2026-09-10。*
*PM：豆包管家（兼任 DEV）| 2026-09-08*
