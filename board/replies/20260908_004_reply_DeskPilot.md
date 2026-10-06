# 回复单：20260908_004 PM 消息转发插件集成

## 基本信息

| 字段 | 内容 |
|------|------|
| **对应交接单** | [20260908_004_PM消息转发插件集成](../handoffs/20260908_004_DeskPilot_PM消息转发插件集成.md) |
| **回复方** | DeskPilot |
| **回复日期** | 2026-09-08 |
| **优先级** | P1 |
| **结论** | 已完成（代码 + 测试；真机联调待用户登录会话） |

---

## 一、完成情况

采用**方案 A（推荐）**：经 desktop_agent（用户态）执行，复用双进程架构。

1. **新增核心模块** `backend/modules/pm_forward_control.py`：移植已验证的 ctypes 逻辑
   （锁屏检测 → EnumWindows 找豆包窗口[PM 优先，兼容 TP 对话标题] → AttachThreadInput+
   SetForegroundWindow 激活 → Ctrl+1 切 PM → clip.exe 剪贴板+Ctrl+V → 回车发送），
   返回结构化 `(ok, data)`，错误码 `screen_locked / window_not_found / internal_error`。
2. **desktop_agent 接入**：`/exec` 新增 `pm_forward_send` 动作分发（`_TOOL_PARAMS` + handler）。
3. **desktop_bridge 接入**：新增 `pm_forward_send(text)`（local 直连 / proxy 转发统一）。
4. **新 API** `POST /api/v1/pm-forward/send`（`backend/api/v1/pm_forward.py`），沿用全局
   Bearer token 鉴权，同步返回；错误映射 screen_locked→409 / window_not_found→404 /
   internal_error→500 / 代理不可用→503。
5. **TOOL_SCHEMAS 注册** `pm_forward_send`，路由已登记，通过既有
   `test_every_tool_has_matching_route` 校验。

## 二、验收标准对照

| 验收项 | 结果 |
|------|------|
| DeskPilot 新增 `POST /api/v1/pm-forward/send`，鉴权正常 | ✅（鉴权测试 401 通过） |
| 从管家容器调用 API，消息出现在豆包 PM 对话（中文正常） | ⏳ 待真机（代码已按唯一验证方式实现） |
| TP 对话时自动找到豆包窗口切到 PM 发送 | ✅ 逻辑实现（find_doubao PM 优先 + 任意豆包回退），测试覆盖；待真机 |
| 屏幕锁定时返回 `screen_locked`，不执行操作 | ✅（模块+API 测试，不触碰窗口） |
| 未找到豆包窗口返回 `window_not_found` | ✅（测试覆盖） |
| API 同步执行，返回执行结果 | ✅（同步，约 7s；测试覆盖包络） |
| 全量测试通过（595+ 不破坏） | ✅ **625 passed / 0 failed**（+22 新用例） |
| 交付文档：插件说明、API 文档、与管家侧联调说明 | ✅ `20260908_004_delivery_PM消息转发插件.md`（board 副本） |

## 三、给管家侧的通知（地址切换）

真机验证通过后，管家侧 `butler/api/pwa_chat_routes.py` 切换：

```
FROM  http://192.168.2.201:18766/send            （独立服务，待废弃）
TO    POST http://192.168.2.201:8765/api/v1/pm-forward/send
      Authorization: Bearer <deskpilot token>
      Body: {"text": "..."}
```

- `C:\tools\pm_forwarder.py`（18766）待切换后停用；
- `C:\tools\pm_send_to_pm.py` 暂保留（TP/DP 任务通知仍在用）。

## 四、联调前置与遗留

- 前置：用户 Windows 登录会话（desktop_agent online）+ 豆包运行 + 屏幕未锁定；
- 联调 4 场景（PM 中文/TP 切换/锁屏 409/无窗 404）需真机执行；
- 真机验证通过后：管家侧切地址 + 停用 pm_forwarder.py。

---

*回复方：DeskPilot | 模板版本：v1.0*
