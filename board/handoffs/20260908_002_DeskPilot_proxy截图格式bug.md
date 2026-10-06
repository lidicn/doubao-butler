# 交接单：双进程方案 proxy 模式截图 result 格式 bug

| 字段 | 内容 |
|------|------|
| **编号** | 20260908_002 |
| **类型** | 问题（Bug） |
| **优先级** | P1（阻塞 proxy 模式下截图工具使用，影响 LLM 观察能力） |
| **发起方** | 豆包管家 PM |
| **接收方** | DeskPilot |
| **发出日期** | 2026-09-08 |
| **状态** | 待回复 |

---

## 一、问题描述

双进程方案真机验证中，proxy 模式下调用 `GET /api/v1/desktop/screenshot?include_image=false`，返回的 `result` 字段是 **list** 而非 **dict**：

```json
{
  "ok": true,
  "tool": "desktop_screenshot",
  "result": [true, {"width": 480, "height": 202, "...": "..."}],
  "cost_ms": 2884,
  "error": null
}
```

**期望格式**（与 local 模式一致）：
```json
{
  "ok": true,
  "tool": "desktop_screenshot",
  "result": {"width": 480, "height": 202, "...": "..."},
  "cost_ms": 2884,
  "error": null
}
```

## 二、根因分析

`backend/modules/desktop_control.py` 的 `screenshot()` 函数返回 **tuple** `(ok, data)`。

- **local 模式**：`backend/api/v1/desktop.py` 的 API 层会解析 tuple（`ok, data = screenshot()`），把 `data` 放到 `result` 字段。
- **proxy 模式**：`backend/desktop_agent.py` 的 `/exec` 端点直接执行函数并把返回值（tuple）当成 `result` 返回，没有解析 tuple。`backend/modules/desktop_bridge.py` 转发时也没有重新解析。

导致管家侧 `integrations/deskpilot.py` 调用 `result.get("width")` 时报错：`'list' object has no attribute 'get'`。

## 三、影响范围

需排查所有经代理执行的桌面工具返回格式：
- `desktop_screenshot` — **已确认受影响**（返回 tuple）
- `desktop_click` — 待确认
- `desktop_type` — 端到端测试中正常（可能返回 dict 或管家侧不依赖 result.get）
- `desktop_swipe` — 待确认
- `desktop_key` — 待确认
- UIA 4 工具 — 待确认

## 四、修复建议

在 `backend/desktop_agent.py` 的 `/exec` 端点中，执行工具函数后，若返回值是 tuple（长度为 2，第一个元素是 bool），则解析为 `(ok, data)`，把 `data` 放到 `result` 字段，与 `backend/api/v1/desktop.py` 的行为保持一致。

或者在 `backend/modules/desktop_bridge.py` 的转发结果处理中，统一解析 tuple。

**修复后需保证**：proxy 模式下所有 9 个桌面工具的返回格式与 local 模式完全一致。

## 五、验收标准

1. proxy 模式下 `GET /api/v1/desktop/screenshot?include_image=false` 返回 `result` 为 dict（含 width/height/orig_width/non_black_ratio/foreground_window_title）
2. proxy 模式下 click/type/swipe/key 返回格式与 local 模式一致
3. 管家侧 ReAct 联调中 `desk_desktop_screenshot` 不再报 `'list' object has no attribute 'get'`
4. 全量测试 595+ 仍通过

## 六、验证环境（当前可复用）

- DP 服务：管理员 PowerShell，`$env:DESKPILOT_DESKTOP_MODE="proxy"`，端口 8766
- 桌面代理：`$env:DESKPILOT_DESKTOP_ADMIN_SERVICE="http://127.0.0.1:8766"`，端口 18765
- 管家：NAS 部署，DP 端口已配置 8766
