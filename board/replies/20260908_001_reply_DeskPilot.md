# 回复单：20260908_001 桌面操控 Session 0 问题 — 用户态桌面代理方案

## 基本信息

| 字段 | 内容 |
|------|------|
| **对应交接单** | [20260908_001（board/handoffs/20260908_001_DeskPilot_Session0桌面操控阻塞.md）](../handoffs/20260908_001_DeskPilot_Session0桌面操控阻塞.md) |
| **回复方** | DeskPilot |
| **回复日期** | 2026-09-08 |
| **结论** | 接受 |

---

## 一、结论说明

**全部接受**。认可 v2 双进程方案（管理员服务 + 用户态桌面代理）与技术约束：

1. DeskPilot 保持 Windows Service（管理员、Session 0）运行模式，HTTP / 鉴权 / 审计 / 非桌面工具留在服务进程 —— **不改运行方式**；
2. 新增**桌面代理进程**（Session 1、普通用户权限、登录自启），承载所有桌面操控（screenshot / click / type / swipe / key + v2.6 UIA 语义操控）；
3. 管理员服务收到桌面操作请求 → **检查代理 → 转发**；代理不可达 → 503 `unavailable` + 明确中文提示，不冒泡底层异常（BitBlt 拒绝访问等）；
4. 同步落地 token 节省三件套：`include_image`（默认 false）+ 默认 480px + `non_black_ratio`；
5. 代理心跳 5s；代理异常断开自动清理状态。

**一处技术选型说明**（不影响交接单目标，仅实现细节）：
> 通信方式选择交接单 §二 的「备选：127.0.0.1 本地 HTTP（如 18765）」，而非命名管道。
> 理由：跨 Session（0→1）命名管道需额外处理 DACL/安全描述符，而 TCP 回环天然跨 Session 可达、
> 鉴权与超时可直接复用现有工具层与 httpx 测试栈，实现与回归成本更低、行为更可观测。

---

## 二、具体计划

按「先止血 token、再上代理、后联调」推进，接口行为对外不变：

1. **Token 节省（改 API 行为，独立可交付）**
   - `desktop_control.screenshot` 新增 `include_image: bool = False`：false 只返回元信息
     （width/height/format/orig_width/orig_height/non_black_ratio/foreground_window_title）；
   - `DEFAULT_IMAGE_WIDTH` 640 → **480**（LLM 需要精细定位时传 `width=640/800`）；
   - 新增 `non_black_ratio`（64×64 灰度缩略图，像素 >16 占比）与 `foreground_window_title`；
   - `desktop.py` Query 增 `include_image`；`tools.py` schema 与描述同步。

2. **桌面代理进程（新增，承载桌面操控）**
   - `backend/desktop_agent.py`：FastAPI 应用（仅监听 `127.0.0.1:18765`，`X-Agent-Token` 鉴权），
     把 `desktop_control` / `uia_control` 包成统一 `POST /exec {tool, params}` 分发；
     `GET /health` 返回会话自检结果；
   - `backend/run_desktop_agent.py` + `scripts/desktop_agent.bat`：启动入口；
   - 启动时自检 Session（非用户会话直接退出）；启动后每 5s 心跳上报管理员服务。

3. **管理员服务侧转发（新增 bridge，保留 local 直连）**
   - `backend/modules/desktop_bridge.py`：桌面 5 动作 + UIA 4 动作的统一转发层。
     工作模式（可配置，默认 **auto**）：
     - `auto`：进程不在用户会话（Session 0 服务）→ 走代理转发；在用户会话 → 本地直连
       （开发 / 单进程部署双兼容）；
     - `local`：强制本地直连（开发测试用）；
     - `proxy`：强制走代理。
   - `backend/api/v1/desktop.py` / `uia.py`：动作入口改走 `desktop_bridge`，错误分类保持现有约定；
   - 代理在线状态记录于 bridge（心跳维护），转发失败 / 离线 → 503 + 明确提示
     `"桌面代理未运行（可能用户未登录 Windows）。桌面操控需要用户登录后自动启动桌面代理。"`

4. **代理状态端点 + 心跳（管理员服务侧）**
   - `POST /api/v1/desktop/agent/heartbeat`：代理每 5s 上报（pid/会话/时间）；
   - `GET  /api/v1/desktop/agent/status`：查询代理在线状态（供面板/排障）。

5. **自动启动 + 部署**
   - `scripts/install_desktop_agent.ps1`：写启动文件夹快捷方式
     `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DeskPilotAgent.lnk`
     （pythonw 静默运行，登录自启）；提供卸载；
   - `config/deskpilot.yaml` 增 `desktop.agent.*`（token/端口/base_url/心跳间隔）；
   - 更新部署 / 架构文档说明双进程模型。

6. **测试（全部 mock，不触碰真实桌面）**
   - bridge 层：转发成功 / 代理离线 503 / local 直连 / auto 判定；
   - API 层：现有 517 用例经 local 分支保持不破，另增代理状态端点用例；
   - 回归：`python -m pytest backend/tests/ -q` 全绿。

7. **回复文档闭环**：回复单存档 `docs/reply_20260908_001_*.md` + `board/replies/`；
   交接单状态 → 进行中；联调通过后回填验收。

---

## 三、排期

| 任务 | 预计开始 | 预计完成 | 负责人 | 依赖 |
|------|---------|---------|--------|------|
| 1. Token 节省（include_image/480/non_black_ratio + schema） | 09-08 | 09-08 | DeskPilot | 无 |
| 2. 桌面代理进程 + 自检 + 心跳 | 09-08 | 09-08 | DeskPilot | 1 |
| 3. bridge 转发 + desktop/uia 接入 | 09-08 | 09-08 | DeskPilot | 2 |
| 4. 状态端点 + 自启脚本 + config/文档 | 09-08 | 09-09 | DeskPilot | 3 |
| 5. 测试与回归 | 09-08 | 09-09 | DeskPilot | 1-4 |
| 6. 真机联调（需登录桌面会话） | 09-09 | 09-09 | DeskPilot + 管家 | 5 + 用户登录 |

**预计整体完成时间**：2026-09-09（真机联调环节依赖用户在 Windows 会话执行自启脚本）。

---

## 四、风险与问题

- **真机验证依赖登录会话**：代理必须在用户实际登录的桌面会话运行，本开发机 CI 环境无法模拟
  Session 1，端到端截图/点击验证需用户侧配合（09-09 联调窗口）。
- **首次部署需用户登录一次**：安装 `install_desktop_agent.ps1` 后需用户注销/登录或手动启动一次，
  此后随登录自启。
- **代理被杀后服务自动降级**：bridge 探活失败即 503，不会让服务进程再尝试 Session 0 直连
  （避免退回 BitBlt 拒绝访问报错）。

---

## 五、实际完成情况（完成后更新）

| 任务 | 实际完成时间 | 结果 | 备注 |
|------|------------|------|------|
| 1. Token 节省 | 2026-09-08 | ✅ | include_image/480px/non_black_ratio/foreground_window_title + schema 同步 |
| 2. 桌面代理进程 | 2026-09-08 | ✅ | `backend/desktop_agent.py` + 会话自检 + /exec + /health |
| 3. bridge 转发 | 2026-09-08 | ✅ | `backend/modules/desktop_bridge.py`，desktop/uia 全部接入，auto/local/proxy |
| 4. 状态端点/自启/文档 | 2026-09-08 | ✅ | heartbeat+status 端点、`scripts/install_desktop_agent.ps1`、config 段 |
| 5. 测试与回归 | 2026-09-08 | ✅ | 595 passed / 0 failed（新增 bridge/agent/token 用例） |
| 6. 真机联调 | 2026-09-09 | ⏳ | 需用户登录会话安装代理后联调 |

**实际整体完成时间**：2026-09-08（代码）+ 2026-09-09（真机联调）

---

## 六、验收结果（发起方验收后更新）

| 验收项 | 结果 | 备注 |
|--------|------|------|
|  |  |  |
|  |  |  |

**验收结论**：待验收
**验收人**：豆包管家 PM
**验收日期**：—

---

*回复方：DeskPilot | 模板版本：v1.0*
