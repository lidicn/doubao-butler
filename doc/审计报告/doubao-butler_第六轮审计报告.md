# doubao-butler 审计报告 · 第六轮

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-006`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-006/`
- **报告日期**：2026-10-07

---

## 一句话结论

**本轮最重要的发现不在被测代码，而在两处判据与一处扫描范围：ERRH-05「无超时」规则只看函数自身、不看被调函数（第三次遇到同一条判据缺口）；HTTP 类命中默认假设「异常穿出=崩溃」，但项目装了全局异常兜底，实际只会返回 500；以及 `doc/ask/src/` 是 AutoForge 的代码快照，污染了 82 条命中。**

---

## 一、全局异常兜底的存在，改变了 100+ 条命中的严重度

`butler/app.py:867`：

```python
app = Starlette(
    ...
    exception_handlers={Exception: _global_exc},
)

async def _global_exc(request, exc):
    logger.exception("unhandled: %s", exc)
    return JSONResponse({"ok": False, "error": "internal_error"}, status_code=500)
```

**这是本轮最有价值的上下文发现。** 它意味着：

| 路径类型 | 是否有全局兜底 | 异常后果 |
|---|---|---|
| HTTP 路由（100+ 条 IN-02 等） | **有** | 返回 500，**不崩溃** |
| MQTT 消费者 `_consume`（app.py:99） | 有（自身 try/except） | 记日志继续 |
| **技能引擎 `run()`（D12）** | **无** | RecursionError 穿出 |
| **TTS 取令牌（D10）** | **无** | 异常穿到 `app.py:366` |
| 后台线程 `_scheduler_loop` | 部分 | 自身 try/except |

**推论**：README 承诺的「全局异常兜底，绝不崩溃退出」在 **HTTP 层面确实实现**了。但真正危险的恰恰是**不走 HTTP 请求-响应**的路径——D10、D12 都在这里。**全局 handler 给了一种虚假的安全感：它兜住了最不危险的那部分。**

这直接支持把 D10/D12 排在 IN-02 那 100+ 条前面。

---

## 二、工作流迭代

### W62：ERRH-05 应跨跳看被调函数（第三次遇到同一缺口）

`errorhandling_defects.py` 原先只在**函数自身源码**里正则找 `timeout`：

```python
src = _fn_src(fn)
has_timeout = TIMEOUT_HINT.search(src) is not None
```

`api/scene_routes.py:88 _execute_step()` 里写 `await _ha_call(client, rt, ...)`，而 `timeout=10` 在**被调的 `_ha_call` 里** ⇒ 误报为"无超时"。

修法：建立模块内函数索引，自身没有就追最多 2 跳看被调函数。**命中 4 → 3**。

**这是同一条判据缺口的第三个实例**：

| 轮次 | 表现 | 场景 |
|---|---|---|
| W54（第三轮） | 写盘委托给 helper，`dumps` 在 `_save` 里 | 写侧跨函数 |
| W61（第五轮） | 函数内 import 同名函数，规则按名字字面判递归 | 调用目标识别 |
| **W62（本轮）** | timeout 在被调函数里 | 属性跨函数 |

三次都是「静态规则的函数内视野太窄」。**该做通用化改造，而不是每次单独补**——已记入下一轮。

### W63：长连接型调用不应按「无短超时」论处

`event_stream._connect` 与 `xiaomi_ear._connect` 都是 `session.ws_connect(ws_url, heartbeat=30)`：

- `heartbeat` 是**保活**不是超时
- aiohttp 对 ws 建连有默认超时（非"永久挂起"）
- 外层 `_run` 有 `except Exception + 5s 重试` 兜底

套用「远端不响应会永久挂起调用方」不成立。规则新增 `LONG_LIVED_CALL` 识别（ws_connect / receive / listen / subscribe），这类降为 low 并注明。**两条从 high 降为 low**。

### W64：扫描范围污染（未修，记为待办）

`doc/ask/src/` 下 4 个文件是 **AutoForge 的代码快照**（`af_service.py` 开头即「AutoForge 服务层」），是 doubao-butler 仓库里 doc 目录的参考资料，**不是本项目代码**。

污染量：**82 条命中**（high 7 / medium 55 / low 20），其中包括本轮核验的 `API-01-unknown-kwarg`、`AUTH-01-route-no-auth`。

**剔除 doc/ 后待办从 1339 降为 1257，high 从 73 降为 66。**

---

## 三、本轮核验结果

| 命中 | 判定 | 理由 |
|---|---|---|
| `scene_routes.py:88 _execute_step` ERRH-05 | **假阳性**（W62 已消除） | `timeout=10` 在被调的 `_ha_call` 里 |
| `event_stream.py:119` / `xiaomi_ear.py:51` ERRH-05 | **降为 low**（W63）→ **D13** | 长连接，但接收循环确实无超时 |
| `tts_baidu.py:28 _get_token` ERRH-05 | **保持 high** | 已是 D10，请求-响应型，无超时且异常穿出 try |
| `roles/store.py:131 load` GOD-01 | **假阳性** | `ensure_defaults()` 是播种默认角色，不是护栏；顺序正确（先读后补） |
| `doc/ask/src/*` API-01 / AUTH-01 | **不适用** | AutoForge 代码快照，非本项目 |

### 新增 D13【P3·low】HA 事件流接收循环无超时

`event_stream.py:151`：

```python
while not snapshot_done:
    raw = await ws.receive()      # ← 无超时
```

若 HA 接受连接、认证通过但对 `get_states` 不返回结果帧，该循环会**永久挂起**。`heartbeat=30` 只在连接健康时发 ping，**不能替代接收超时**（半开连接下 ping 也可能无响应）。`xiaomi_ear.py` 同形。

定 low 是因为外层有重试兜底，且需 HA 处于半开状态才触发。

---

## 四、审计结果

| 指标 | 数值 |
|---|---|
| ERRH-05-no-timeout | **4 → 3**（W62）+ 2 条降 low（W63） |
| GOD-01 判假阳性 | 1 |
| 扫描范围污染 | doc/ 下 **82 条**（已量化，未剔除） |
| 剔除 doc/ 后待办 | **1257**（high 66 / medium 817 / low 374） |
| 本轮新增确证 | **D13** |
| 台账累计 | **D1–D13** |
| 门禁 | ok=True，analyzers=33 |

---

## 五、按根因合并（更新）

| 根因 | 缺陷 | 计数 |
|---|---|---|
| 读路径的失败被当成「没有数据」 | D1 D2 D3 D4 D6 D7 D8 D9 | 8 |
| 失败路径没兜住 / 无超时 | D10 D12 D13 | 3 |
| 配置面不一致 | D5 | 1 |
| 观测语义矛盾 | D11 | 1 |

第二族升到 3 项——**共同点是都发生在非 HTTP 路径**，恰好是全局异常兜底覆盖不到的地方。这不是巧合：全局 handler 把 HTTP 路径的问题都变成了 500，反而让后台路径成了唯一的真实风险面。

---

## 六、下一轮方向

1. **W62 通用化**：把「跨函数追属性」做成通用能力（timeout / 写盘 / 递归目标 / 锁覆盖），而不是每条规则单独补——同一缺口已出现三次
2. **剔除 `doc/` 目录**，重跑全量，把待办从 1339 收敛到 1257
3. **核验 CONC-08（aliases.py:26）**：`_load` 裸写、`learn` 持锁，需确认是否真有并发窗口
4. **继续分诊 AFS-01 剩余约 12 条**
5. **给分诊层加「调用方如何处理失败返回值」信号**（第四、五轮累计 8 条假阳性都靠人工读出）

---

## 七、本轮最该记住的一条

**全局异常兜底会改变"异常穿出"类缺陷的严重度排序，但改变的方式和直觉相反。**

`exception_handlers={Exception: _global_exc}` 让 HTTP 路由里的 ValueError / KeyError 全部变成 500 —— 那些"异常穿出"的命中实际上**不危险**。真正的风险是 D10、D12 这种**不走 HTTP 请求-响应**的路径：后台任务、技能引擎、初始化流程。它们没有全局 handler。

所以：**看到"装了全局兜底"不应下调所有命中，而应重新排序——把非 HTTP 路径的命中往上提。** 静态规则不知道全局 handler 的存在，这个上下文只能靠审计员建立。
