# 交接单：DeskPilot 项目 Agent 更换 — 项目交接

## 基本信息

| 字段 | 内容 |
|------|------|
| **编号** | 20260908_004 |
| **类型** | 变更 / 交接 |
| **优先级** | P0（项目主线，接任 Agent 据此起步） |
| **发起方** | 原 DeskPilot 开发 Agent（现任） |
| **接收方** | 接任 DeskPilot 开发 Agent（新） |
| **发出日期** | 2026-09-08 |
| **状态** | 待接任 Agent 确认 |

---

## 一、背景与原因

当前 DeskPilot 开发由本 Agent 负责，因工程进展与流程需要，更换 Agent 接手后续开发。
本交接单汇总项目现状、关键上下文、进行中的工单、技术决策与接手清单，确保接任 Agent
无需从零摸索即可继续。

**项目定位**：DeskPilot = 豆包管家（管家 Agent 中枢）下属的 **Windows 工具层** 项目，
与 TVPilot（电视工具层）同构。对外是 FastAPI HTTP 服务（端口 8765），供豆包管家 ReAct
通过 function calling 调用；不做独立大脑。

---

## 二、项目结构速览

```
E:\NAS\DeskPilot\
├── backend/
│   ├── main.py / service.py / gateway.py    # 启动入口（uvicorn / Windows Service / 守护）
│   ├── desktop_agent.py                     # 【新】用户态桌面代理（Session 1）
│   ├── api/v1/                              # 20+ 路由：desktop/uia/system/volume/windows/lxmusic/ssh/tools/traces...
│   ├── modules/                             # 实现：desktop_control/uia_control/desktop_bridge/window_manager/...
│   ├── core/                                # config/logger/audit/tool_response/responses/app.py
│   └── tests/                               # 约 600 用例（595 passed）
├── config/deskpilot.yaml                    # 主配置（含新增 desktop.mode/agent.*）
├── scripts/                                 # deskctl.bat、run_service.py、desktop_agent.bat、install_desktop_agent.ps1
├── frontend/                                # Vue3 + Vite PWA 手动运维面板（可不管，测试不依赖）
└── docs/                                    # 权威文档：路线图/架构/开发计划/交接单/交付报告
```

**配套看板（豆包管家生态项目管理中枢，非本 repo）**：`E:\NAS\doubao-butler\board\`
- `README.md`（PM 手册）、`开发规范_v1.0.md`、`status.md`、`projects/deskpilot.md`、
  `handoffs/`、`replies/`。开发需遵循其中交接单规范（24h 回复，P0 4h）。

---

## 三、版本与近期里程碑（接任起点 = v2.6）

| 版本 | 内容 | 状态 |
|------|------|------|
| v2.0 | 工具层规范（统一包络/错误码/鉴权/审计） | ✅ |
| v2.1 | 豆包管家 Agent 整合（13 固定任务工具） | ✅ |
| v2.5 | 原子操控 MVP（desktop_screenshot/click/type/swipe/key） | ✅ 517 用例 |
| M6 / v2.6 | UIA 语义操控（uia_snapshot/click/type/wait） | ✅ 40 用例 |
| **M6.5 / v2.6** | **Session0 双进程桌面代理方案** | ✅ 代码完成 595 passed，⏳ 待真机联调 |
| v2.7 | SoM（点击准确率 <80% 触发） | ⏳ 规划 |
| M8 | SSH 约束加固（P2，5 道约束） | ⏳ 排后 |

权威路线：`docs/路线图_v2.1_原子操控阶段.md`、`docs/开发计划_v2.1_原子操控阶段.md`。
各里程碑交付报告：`docs/M0..M6.5_*交付报告.md`、`docs/v2.5_原子操控MVP_交付报告.md`。

---

## 四、关键技术上下文（接任必读，最容易踩坑）

### 4.1 Session 0 隔离 → 双进程架构（M6.5 核心）

DeskPilot 作为 Windows Service 运行于 **Session 0**（管理员），无法访问用户桌面
（mss BitBlt 拒绝访问 / pyautogui SendInput 隔离 / UIA 拿不到控件树）。
**不能**改为用户态进程（用户明确要求服务保持管理员运行）。

方案（交接单 20260908_001）：
```
管家(NAS) →HTTP→ DP 管理员服务(Session 0) → desktop_bridge → 桌面代理(Session 1,登录自启)
                                    127.0.0.1:18765
```

- **管理员服务**：HTTP/鉴权/审计/非桌面工具；desktop_* / uia_* 动作经
  `backend/modules/desktop_bridge.py` 转发。
- **桌面代理** `backend/desktop_agent.py`：仅监听 127.0.0.1:18765，`POST /exec`
  分发到 desktop_control / uia_control，启动时会话自检，每 5s 心跳上报。
- **bridge 三模式**（`desktop.mode`，默认 auto）：auto（不在用户会话→proxy，在→local）/
  local / proxy。
- 代理离线 → 503 + 明确中文提示，**绝不冒泡**底层异常。
- 心跳端点 `POST /api/v1/desktop/agent/heartbeat`（**跳过审计**，见 audit.py 白名单）；
  状态查询 `GET /api/v1/desktop/agent/status`。
- **部署/启动脚本**：`scripts/desktop_agent.bat`（前台）、
  `scripts/install_desktop_agent.ps1`（登录自启，Startup 快捷方式 + pythonw 静默）。
- config 新段：`desktop.mode`、`desktop.agent.{service_url,admin_service_url,token,heartbeat_interval,offline_stale_after}`。
  **生产必须改 `desktop.agent.token`**（服务与代理共享密钥；env `DESKPILOT_DESKTOP_AGENT_TOKEN` 优先）。

### 4.2 Token 节省（交接单 20260908_001 需求 3，已实现）

- screenshot 新增 `include_image`，**默认 false** → 只返回元信息（~200 token）
  width/height/orig_width/orig_height/`non_black_ratio`/`foreground_window_title`/format/monitor；
  `include_image=true` 才返回 base64（~1.5-2万 token）。
- `DEFAULT_IMAGE_WIDTH` 640 → **480**（精细定位传 640/800）。
- LLM 策略已在 `tools.py` schema 描述说明：首次观察→true；点击后验证画面→看
  non_black_ratio/foreground_window_title 用 false。

### 4.3 既有测试约束（重要，别破坏）

- `test_tools.py` 校验「TOOL_SCHEMAS 注册的 tool 名确有对应路由」，新增/改路由须同步 schema。
- **SSH 不在 TOOL_SCHEMAS**（v2.1 SSH 降级决策），`/api/v1/ssh/*` 保留仅供前端手动。
- API 层测试**严禁 patch 全局 `time.monotonic`**（TestClient 内部大量调用，side_effect 耗尽会挂起），
  一律 patch 顶层函数/模块函数。
- 测试全 mock，不触碰真实桌面；桌面代理需在真机（用户会话）才能端到端验证。

### 4.4 本机环境

- OS win32 / PowerShell Core。全量测试：`python -m pytest backend/tests/ -q`（~595，约 2 分钟）。
  可用 `-p no:cacheprovider` 避开缓存；PowerShell 无 `tail`，用 `Select-Object -Last`。

---

## 五、进行中的工单 / 交接单（接手第一优先处理）

| 编号 | 主题 | 优先级 | 状态 | 要点 |
|------|------|--------|------|------|
| **20260908_002** | **proxy 模式 screenshot result 格式 bug** | **P1** | **待修复** | 见 DeskPilot docs/交接单_proxy截图格式bug_20260908.md。真机 proxy 模式 `result` 返回 list `[true,{...}]` 而非 dict `{...}`。根因：desktop_agent /exec 未把模块 tuple `(ok,data)` 解包成 data 放 result，`_jsonable` 把 tuple 转 list。接任**第一件事**修复，需保证 9 个桌面工具 proxy/local 返回格式一致 |
| 20260908_001 | Session0 桌面操控阻塞（双进程+token） | P0 | 进行中（代码完成） | 回复单 `reply_20260908_001_*.md`；待真机联调 |
| 20260908_001/002/003 | 手动测试/DP工单通道/存在性测试 | P2/P3 | 待回复 | **PM 自动化测试通道的测试单**，非实际功能需求，简短确认即可 |
| — | DP ↔ 管家 原子操控联调（4 场景） | — | 待发 | 等桌面代理真机就绪后发起 |

**真机联调是解 P0 的最后一步**（需用户登录会话）：安装 `scripts/install_desktop_agent.ps1`
→ 确认 `/api/v1/desktop/agent/status` online → 验证 screenshot/click/type（中文）→
管家接入联调 4 场景。proxy bug（20260908_002）需在此环境复现验证修复。

---

## 六、已知待办 / 后续路线（非本次阻塞）

1. 修复 20260908_002 proxy tuple bug（见上）。
2. 真机联调验收（`M6.5_Session0桌面代理双进程_交付报告.md` §五待真机项逐条勾选）。
3. 管家侧接入 desktop_*/uia_* 9 工具（管家 repo：butler/integrations/deskpilot.py + tools.py，联调 4 场景）。
4. `docs/测试结果汇总.md` 更新到 595+。
5. 后续 v2.7 SoM、M8 SSH 约束加固（均触发式/排后，不急于做）。
6. 若干 P2 优化见 `M6_UIA语义操控_交付报告.md` §六（uia_wait 条件扩展/控件树增量/嵌套窗口/错误结构化）。

---

## 七、关键决策记录（勿违背）

- DeskPilot = 豆包管家工具层，不做独立大脑（P1 不引 DSH 作运行时大脑）。
- SSH 降级 P2，从管家 LLM 工具移除；重新启用前需 5 道约束。**勿把 ssh_* 加回 TOOL_SCHEMAS**。
- 桌面服务必须保持管理员运行（用户明确），桌面操控走双进程代理。
- PM 兼任 DEV，沿用 board 体系；交接单 24h 内回复、P0 4h。
- 每次对外新端点/改端点，须同步 `tools.py` TOOL_SCHEMAS（test_tools.py 自动校验）。

---

## 八、接任起步清单（建议顺序）

1. 通读本交接单 + `docs/路线图_v2.1_原子操控阶段.md`、`docs/开发计划_v2.1_原子操控阶段.md`、
   `M6.5_Session0桌面代理双进程_交付报告.md`、`M6_UIA语义操控_交付报告.md`。
2. 跑一次全量测试确认基线 `python -m pytest backend/tests/ -q`（应 595 passed）。
3. 处理 P1 bug `20260908_002`（proxy result 格式），补单测 + 更新 tools schema（如需）。
4. 与用户确认真机联调窗口，走 `scripts/install_desktop_agent.ps1` 部署代理做端到端验证。
5. 发起/承接 DP↔管家联调 4 场景；完成后更新交接单状态与 board `status.md` / `projects/deskpilot.md`。

---

## 九、回复方式

接任 Agent 收到本单后：
- 在 `docs/` 与 `board/replies/` 各落一份回复单（模板见 board/templates/reply_template.md）；
- 将本交接单状态改为「进行中」；完成后按 board 规范更新。

---

*发起方：DeskPilot 原开发 Agent | 交接单版本：v1.0 | 2026-09-08*
