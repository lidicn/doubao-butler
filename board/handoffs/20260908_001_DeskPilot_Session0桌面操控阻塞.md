# 交接单：桌面操控 Session 0 问题 — 用户态桌面代理方案

| 字段 | 内容 |
|------|------|
| **编号** | 20260908_001 |
| **类型** | 问题 + 需求 |
| **优先级** | P0（阻塞原子操控联调） |
| **发起方** | 豆包管家 PM |
| **接收方** | DeskPilot |
| **发出日期** | 2026-09-08 |
| **修订** | v3（2026-09-08，联调已通过 GUI 线程临时方案，长期双进程方案待 DP 排期） |
| **建议截止** | 2026-09-12（长期双进程方案） |
| **状态** | 联调已突破（GUI 线程临时方案验证通过），长期双进程方案待 DP 实现 |
| **回复单** | [20260908_001_reply_DeskPilot.md](../replies/20260908_001_reply_DeskPilot.md) |

---

## 一、背景与原因

豆包管家已完成 DeskPilot v2.5 原子操控工具的接入（5 个 desk_desktop_* 工具，48 工具总数，全量测试 113 passed，NAS 部署完成）。

2026-09-08 联调冒烟测试时，调用 `desk_desktop_screenshot` 失败，DP 返回：

```
{"ok": false, "error": "unavailable", "message": "截图失败: Windows graphics function failed: BitBlt: 拒绝访问。"}
```

**根因**：DeskPilot 以 Windows Service 方式运行（管理员，Session 0）。Windows 从 Vista 开始实行 Session 0 隔离，服务进程无法访问用户桌面会话（Session 1+）的图形子系统和用户输入子系统。

**影响范围不只是截图**：所有桌面操控工具（screenshot/click/type/swipe/key）在 Session 0 中都不可用——mss 用 BitBlt（GDI），pyautogui 用 SendInput（用户输入），两者都被 Session 0 隔离拦住。当前只是截图先暴露了错误。

**约束**：DP 服务必须以管理员身份运行（用户明确要求），不能简单改为用户态进程。

---

## 二、推荐方案：管理员服务 + 用户态桌面代理（双进程）

### 架构

```
豆包管家（NAS）──HTTP──→ DP 管理员服务（Session 0，管理员权限）
                            │
                            │ 命名管道 / 127.0.0.1 本地端口
                            ▼
                          DP 桌面代理（Session 1，普通用户权限）
                            • screenshot / click / type / swipe / key
                            • v2.6 UIA 语义操控
                            • 极轻量，启动文件夹自启
```

### 进程职责划分

| 进程 | 运行环境 | 职责 |
|------|---------|------|
| **DP 管理员服务** | Session 0，管理员 | HTTP 服务 / 鉴权 / 配置 / 审计日志 / 非桌面工具（system_status/volume/windows/lxmusic）/ 桌面操作请求转发 |
| **DP 桌面代理** | Session 1，普通用户 | 只做桌面操控：screenshot / click / type / swipe / key / UIA（v2.6） |

### 通信方式

- **推荐**：命名管道（Named Pipe），服务端创建 `\\.\pipe\deskpilot_desktop`，代理连接
- **备选**：127.0.0.1 本地 HTTP 端口（如 18765），实现简单但多一个端口
- 协议：JSON 请求/响应，与现有工具接口同构

### 桌面代理自动启动

- 放入当前用户启动文件夹：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DeskPilotAgent.lnk`
- 或创建计划任务，触发器为"用户登录时"，运行级别"普通用户"
- 代理启动后自动连接管理员服务的命名管道，注册就绪
- 代理无窗口运行（pythonw.exe 或编译为窗口子系统 exe），不打扰用户

### 代理未运行时的优雅降级

管理员服务收到桌面操作请求时：
1. 尝试连接桌面代理的命名管道
2. 连接失败 → 立即返回 503 `unavailable`，message 明确说明：
   `"桌面代理未运行（可能用户未登录 Windows）。桌面操控需要用户登录后自动启动桌面代理。"`
3. **不要**让 mss/pyautogui 抛出底层异常（BitBlt 拒绝访问），用户看不懂

---

## 三、Token 节省需求（同步实现）

### 需求 3.1：screenshot 加 `include_image` 参数（最大收益）

当前 screenshot 每次都返回完整 base64（约 1.5-2 万 token），但 ReAct 循环中大部分步骤不需要看完整画面。

**方案**：screenshot API 加 `include_image` 参数，**默认 false**：

| include_image | 返回内容 | token 消耗 |
|---------------|---------|-----------|
| `false`（默认） | 元信息：width/height/format/orig_width/orig_height/non_black_ratio/foreground_window_title | ~200 token |
| `true` | 元信息 + image_base64（完整 JPEG） | ~1.5-2 万 token |

**LLM 使用策略**（管家侧工具描述里说明）：
- 首次观察界面 / 需要定位元素 → `include_image=true`
- 点击后验证画面是否变化 → `include_image=false`（看 non_black_ratio 是否变化）
- 重复操作 / 确认状态 → `include_image=false`

**预计节省 60-70% 截图 token**。

### 需求 3.2：默认分辨率从 640px 降到 480px

- 当前默认 640px，对于定位按钮/文字已经够用
- 改为默认 **480px**，需要精细操作时 LLM 传 `width=640` 或 `800`
- 480px 比 640px 少 44% 像素，再省 30-40% token
- `DEFAULT_IMAGE_WIDTH` 常量从 640 改为 480

### 需求 3.3：non_black_ratio 计算

screenshot 返回元信息时，计算并返回 `non_black_ratio`（近黑像素占比的补集）：
- 用于判断截图是否有效（全黑 = 0，正常画面 > 0.9）
- 用于判断操作后画面是否变化（与上一次的 non_black_ratio 对比）
- 计算方式：将图片缩放到 64×64 灰度，统计像素值 > 16 的比例（轻量，不耗性能）

---

## 四、其他需求

### 需求 4.1：强化 Session 检查（管理员服务侧）

在管理员服务的桌面操作转发前，先检查桌面代理是否连接：
- 代理已连接 → 转发请求
- 代理未连接 → 返回 503 + 明确提示（见上文）
- 不要在服务进程中直接调用 mss/pyautogui（必然失败）

### 需求 4.2：桌面代理心跳

- 代理每 5 秒向服务发送心跳（通过命名管道）
- 服务记录代理状态（已连接/已断开/最后心跳时间）
- 代理异常断开后，服务清理状态，后续请求返回 503

### 需求 4.3：更新部署文档

- 说明双进程架构（管理员服务 + 用户态桌面代理）
- 说明桌面代理的自动启动配置（启动文件夹 / 计划任务）
- 说明 Session 0 限制的原因和影响
- 说明 include_image 参数的使用建议

---

## 五、验收标准

- [ ] DP 管理员服务保持管理员权限运行，HTTP 服务正常
- [ ] DP 桌面代理以普通用户权限运行在用户会话中，启动文件夹自动启动
- [ ] 两进程通过命名管道通信，代理心跳正常
- [ ] `GET /api/v1/desktop/screenshot?include_image=false` 返回元信息（含 non_black_ratio），无 image_base64，~200 token
- [ ] `GET /api/v1/desktop/screenshot?include_image=true` 返回完整 base64，图片可解码非全黑
- [ ] screenshot 默认宽度 480px（传 width=640 可覆盖）
- [ ] `POST /api/v1/desktop/click` 可正常执行（鼠标移动到指定坐标）
- [ ] `POST /api/v1/desktop/type` 可输入中文
- [ ] 桌面代理未运行时，所有桌面操作返回 503 + 明确提示（非底层异常）
- [ ] 非桌面工具（system_status/volume/windows/lxmusic）不受影响，正常工作
- [ ] 部署文档更新，说明双进程架构和自动启动配置
- [ ] 豆包管家联调 4 场景全部通过（记事本输入/浏览器搜索/拖拽窗口/错误恢复）

---

## 六、影响范围

- 影响模块：`backend/modules/desktop_control.py`（移到代理进程）、新增桌面代理进程、服务进程增加转发逻辑、部署方式
- 影响接口：所有 `/api/v1/desktop/*` 端点（行为不变，内部实现改为转发）
- 影响文档：部署文档、README、架构文档
- 依赖项：无（纯 DP 侧修改）
- 不影响：非桌面工具（system_status/volume/windows/lxmusic/ssh）

---

## 七、参考资料

- DP v2.5 交付报告：`E:\NAS\DeskPilot\docs\v2.5_原子操控MVP_交付报告.md`（§二 P0 实测是在本机用户态完成的，非服务模式）
- 管家联调 trace：`cdfb4226be424554`（截图失败 → 降级 system_status → 向用户汇报）
- Session 0 隔离：Microsoft 官方文档 "Impact of Session 0 Isolation on Services and Drivers in Windows"
- 命名管道：Python `win32pipe` / `win32file`（pywin32 已在依赖中）或 `asyncio` 的 `open_pipe_connection`

---

## 八、状态跟踪

| 日期 | 动作 | 备注 |
|------|------|------|
| 2026-09-08 | 发出交接单 v1 | 方案为"改用户态进程"，状态：待回复 |
| 2026-09-08 | 修订为 v2 | 用户明确服务必须管理员运行，方案改为"管理员服务 + 用户态桌面代理双进程"，新增 token 节省需求（include_image + 默认 480px + non_black_ratio），状态：待回复 |
| 2026-09-08 | 方案确认 | 用户确认双进程方案，DP 按此路线开发。临时用户态联调终止，恢复 Windows Service 运行。联调暂停待 DP 实现。状态：已确认方案，待 DP 实现 |
| 2026-09-08 | DP 回复并实现 | 回复单 `board/replies/20260908_001_reply_DeskPilot.md`：接受方案；token 节省 + desktop_agent + desktop_bridge + 心跳端点 + 自启脚本已落代码，595 passed。状态：进行中 |

---

*发起方：豆包管家 PM（兼任 DEV）| 模板版本：v1.0 | 交接单版本：v2*
