# 回复单：20260908_002 proxy 模式截图 result 格式 bug — 已修复

## 基本信息

| 字段 | 内容 |
|------|------|
| **对应交接单** | [20260908_002（board/handoffs/20260908_002_DeskPilot_proxy截图格式bug.md）](../handoffs/20260908_002_DeskPilot_proxy截图格式bug.md) |
| **回复方** | DeskPilot |
| **回复日期** | 2026-09-08 |
| **优先级** | P1 |
| **结论** | 已修复（代码 + 回归测试，603 passed） |

---

## 一、根因确认

`desktop_control.screenshot()` / `uia_control.snapshot()` 返回模块 tuple `(ok, data)`。

- **local 模式**：`backend/api/v1/desktop.py` / `uia.py` 解包 tuple，把 `data` 放入 `result`（dict）。
- **proxy 模式**：`desktop_agent.py` 的 `/exec` 直接返回模块原样返回值，`_jsonable()` 把 tuple
  转成 list，于是 `result` 变成 `[true, {...}]`；`desktop_bridge.py` 的 `screenshot()` /
  `uia_snapshot()` 未做还原，直接透传该 list。

**受影响范围核实（9 个工具逐一排查）**：

| 工具 | 模块返回 | proxy 是否受影响 | 原因 |
|------|---------|:---:|------|
| `desktop_screenshot` | `(ok, data_dict)` | ✅ **受影响** | bridge 未还原 tuple，`result` 为 `[true, dict]` |
| `desktop_click` | `(ok, detail, zone)` 三元组 | ✅ 已 OK | bridge `_restore_tuple(data,3)` 还原；API 层重建 result dict |
| `desktop_type` | `(ok, detail)` | ✅ 已 OK | bridge `_restore_tuple(data,2)` 还原 |
| `desktop_swipe` | `(ok, detail)` | ✅ 已 OK | bridge `_restore_tuple(data,2)` 还原 |
| `desktop_key` | `(ok, detail)` | ✅ 已 OK | bridge `_restore_tuple(data,2)` 还原 |
| `uia_snapshot` | `(ok, data_dict)` | ✅ **受影响（同源）** | 与 screenshot 相同：bridge 未还原 tuple |
| `uia_click` | `(ok, detail)` | ✅ 已 OK | bridge `_restore_tuple(data,2)` 还原 |
| `uia_type` | `(ok, detail)` | ✅ 已 OK | bridge `_restore_tuple(data,2)` 还原 |
| `uia_wait` | `(ok, detail)` | ✅ 已 OK | bridge `_restore_tuple(data,2)` 还原 |

即：**实际受影响的是 `desktop_screenshot` 与 `uia_snapshot` 两个「data 型」工具**，
其余 7 个「message/zone 型」工具由 bridge 的 `_restore_tuple` 还原、API 层重建 result，
返回格式本已与 local 一致。

## 二、修复内容

采用交接单「修复建议」中的第二种路径：**在 `desktop_bridge.py` 的转发结果处理中统一解析 tuple**，
与其余 7 个工具的处理方式保持一致，`/exec` 保持通用透传不改造。

- `backend/modules/desktop_bridge.py`
  - `screenshot()`：`_forward` 成功后 `ok_b, data = _restore_tuple(data, 2)`，返回 `(bool(ok_b), data)`；
  - `uia_snapshot()`：同上（同源问题一并修复）。
  - 效果：proxy 模式下两工具的 `result` 与 local 模式一致（screenshot 为元信息 dict，
    uia_snapshot 为控件树 dict），同时保留失败路径（`[False, msg]` / `window_not_found` dict）语义。

## 三、测试（回归）

`backend/tests/test_desktop_bridge.py`：
- 修正 `test_screenshot_proxy_success`：原用例 mock 的是「代理直接返回 dict」这一**错误契约**，
  改为按真实代理输出 `result: [true, meta]` 断言 bridge 还原为 dict（回归 bug 本体）；
- 新增 `test_uia_snapshot_proxy_success` / `test_uia_snapshot_proxy_window_not_found`（同源修复回归）；
- 新增 `TestProxyResultFormatConsistency`：覆盖 type/swipe/key/uia_click/uia_type/uia_wait
  6 个工具 proxy 返回 `[true, detail]` → bridge 还原 `(true, detail)` 且 detail 不丢。
- 至此 **9 个桌面工具 proxy/local 返回格式一致性均有测试覆盖**。

验证结果：

```
$ pytest backend/tests/test_desktop_bridge.py -q
39 passed in 5.93s

$ pytest backend/tests/ -q
603 passed, 1 warning in 83.06s      # 基线 595 → 603（+8 新增回归用例）
```

另做 API 层全链路验证（mock 代理返回 `[true, meta]` / `[true, tree]`）：
`GET /api/v1/desktop/screenshot?include_image=false` 与 `GET /api/v1/uia/snapshot`
的响应 `result` 均为 **dict**。

## 四、验收项对照

| 交接单验收项 | 结果 |
|------|------|
| 1. proxy 模式 screenshot `result` 为 dict（含 width/height/orig_width/non_black_ratio/foreground_window_title） | ✅ 通过 |
| 2. proxy 模式 click/type/swipe/key 返回格式与 local 一致 | ✅ 通过（既有 + 新增用例覆盖） |
| 3. 管家侧 ReAct 联调不再报 `'list' object has no attribute 'get'` | ✅ 根因已消除（真机复验见下） |
| 4. 全量测试 595+ 通过 | ✅ 603 passed |

## 五、遗留说明

- 本修复为 mock 级验证（测试规范：全 mock 不触碰真实桌面）；**真机 proxy 联调**（交接单
  §六验证环境）需在用户登录会话安装 `scripts/install_desktop_agent.ps1` 后复验一次，
  纳入 M6.5 待真机验收清单。

---

*回复方：DeskPilot | 模板版本：v1.0*
