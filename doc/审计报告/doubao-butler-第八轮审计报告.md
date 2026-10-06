# doubao-butler 第八轮审计报告 · 工作流升级 + 图谱驱动审计

- **审计对象**：`lidicn/doubao-butler` @ `836df79`（220 文件 / 41211 行）
- **本轮内容**：① 完善 auditkit 工作流至 **v1.1** ② 用新工作流执行第八轮审计
- **结论**：**1 个 P0（三合一死文件）+ 2 个 P1**，全部经执行验证

---

## 第一部分：工作流完善（auditkit v1.0 → v1.1）

### 1.1 新增 `runtime` 阶段 —— 用真实执行覆盖替代名称推断

**动机**：v1.0 的 AST 孤儿通道有**不可消除的误报**（Python 运行时单例注入让基于名称的接收者推断失效：312 个"高置信"里 `TVPilotClient`/`DockerClient` 等全是误报）。

**方案**：新增 `/data/workspace/auditkit/drive.py`（363 行桩件驱动 harness），用**万能桩**（`_Stub`：任意属性返回自身、任意调用返回自身、支持 await）替换 MQTT/HA/LLM/音箱等外部依赖，驱动 12 个真实场景，用 `coverage.py` 记录**哪些方法真的被执行过**。

**效果**：

| 指标 | 值 |
|---|---|
| 覆盖行 | 12.73% |
| 函数总数 | 1733 |
| **执行过** | **775** |
| 未执行 | 958 |
| 12 步驱动 | 全 OK |

`orphans` 阶段新增 runtime 剔除通道（按 `file::Cls.method` 与 alive 集合比对），**剔除 157 个 AST 误报**（965 → 808）。

> 覆盖仅 12.73%，说明驱动场景仍不够；这是当前最大短板，见第三部分。

### 1.2 `deadcall` 阶段提纯

新增两级白名单，**562 → 249 处**（`hasattr` 保护点 5 → 4）：

- `BUILTIN_METHODS`：过滤 `str.strip`、`dict.setdefault`、`f-string.encode` 等内置方法
- `EXTERNAL_RECEIVERS`：过滤第三方/外部接收者

### 1.3 `contract` 阶段修正两处逻辑 bug

| bug | 现象 | 修复 |
|---|---|---|
| `cls` 未排除 | `_extract_list(cls, text, ...)` 被误报 missing `text` | 排除 `self` **和 `cls`** |
| `**kwargs` 无法静态判定 | `register_agent(**agent)` 误报 4 个 missing | 遇 `**解包` 跳过 |

修复后**签名不匹配 4 → 0**（全部为误报，已确认）。

### 1.4 其他

- 支持逗号分隔多阶段：`--stage graph,runtime`
- `order` 改为 `bootstrap / static / graph / runtime / orphans / deadcall / cycles / contract`

### 1.5 v1.1 实测数据

| 阶段 | 结果 |
|---|---|
| bootstrap | 220 文件 / 41211 行 / **2 个 BOM 文件** |
| graph | 713 节点 / 1662 边 / 0.3s |
| runtime | 覆盖 12.73%，775 方法执行过 |
| orphans | runtime 剔除 157 FP，剩 808 |
| deadcall | 249 处，**4 处 hasattr 保护** |
| cycles | 模块级循环 **0** |
| contract | 签名不匹配 **0** / 前端孤儿路由 4 |

---

## 第二部分：第八轮审计发现

### P0-13 `people_routes.py` 三合一死文件 —— 人员状态 API 从未存在

- **位置**：`butler/api/people_routes.py`（51 行，全仓唯一）

这是本轮最重要的发现，由工作流 `deadcall` 阶段的 `rt.perception_engine` 线索深挖得到。**三个独立缺陷叠加**，任一单独存在都足以让功能失效：

#### (1) 依赖缺失 —— `fastapi` 不在 requirements.txt

```python
# people_routes.py:5
from fastapi import APIRouter, Request
```

- `requirements.txt` **无 fastapi**（只有 `starlette==0.37.2`）
- `Dockerfile`（`python:3.11-slim`）**只装 requirements.txt**
- 全仓**只有这一个文件** import fastapi

→ 容器里**一旦导入该文件就 ImportError**。

#### (2) 未注册 —— app.py 从不导入它

`app.py` 通过显式 import 注册 25+ 个 `*_routes`（`agent_routes` / `role_routes` / `skill_routes` / …），**列表中不含 `people_routes`**。

→ 该文件**从未被加载**，所以 (1) 的 ImportError **反而不会在启动时暴露**（这是它至今没被发现的原因）。

→ `/api/people/status` **路由根本不存在**，调用即 404。

#### (3) 属性未装配 —— `rt.perception_engine` 不存在

```python
# people_routes.py:29
pe = rt.perception_engine
matches = pe.check()
```

**实测**（`get_runtime()` 真实单例）：
```
rt.perception_engine  → AttributeError
rt.event_stream       → OK（app.py:763 已装配）
```
`Runtime` dataclass（`butler/runtime.py`）**无 `perception_engine` 字段**，全仓也无人装配。

全仓扫描 `rt.<attr>` 中 Runtime 未定义的属性：**仅 2 个** —— `rt.get`（10 处，字典误报）与 `rt.perception_engine`（1 处，本条）。

#### 三重后果

即使有人修好 (1)(2)，(3) 仍会让场景推断**永久返回"未识别"**——且被 `except Exception` 吞成 `logger.warning`，用户只看到 `current_scene: "未识别"`，**永远不知道是装配问题还是真的没识别出来**。

**孤立性确认**：前端无任何地方调 `/api/people/*`（grep `butler/static/`、`dashboard_pwa/` 为空）；全仓除本文件外无 `people` 引用。

**修复**（三选一）：
```python
# A. 确认废弃 → 直接删除文件（推荐：功能从未上线，无前端调用）
git rm butler/api/people_routes.py

# B. 要保留 → 三处一起修
#   1) requirements.txt 加 fastapi（或改用 starlette 原生写法，与项目其余部分一致）
#   2) app.py 注册 people_routes
#   3) Runtime 加 perception_engine 字段 + app.py 装配 PerceptionEngine()
```
> 建议选 A：项目其余 25+ 路由模块全部用 starlette 原生写法，这一份用 fastapi 是孤例；且无任何调用方，属开发中途遗留。

---

### P1-30 `decision_store.check_timeouts` 未定义 → 决策超时清理永久静默失效

- **位置**：`butler/app.py:572-578`

```python
try:
    from butler.core import decision_store
    if not hasattr(decision_store, "check_timeouts"):
        return                                    # ← 走了这条
    timed_out = decision_store.check_timeouts()
    ...
except Exception as e:
    logger.debug("check_decision_timeouts error: %s", e)
```

- **实测**：全仓 grep `check_timeouts` —— **仅 `app.py:572/574` 两处**（一处是 hasattr 判断，一处是调用），**无任何定义**
- **后果**：定时任务 `sched:vibe_decision_timeout` **每次静默 return**，决策超时清理永不执行；外层 `except` 只 `logger.debug`，**日志里什么都看不见**
- **由工作流自动发现**：`deadcall` 阶段的 `hasattr` 保护通道命中

**修复**：
```python
# 去掉 hasattr 探测，改为显式调用 + 启动时断言
from butler.core.decision_store import check_timeouts
timed_out = check_timeouts()
```

> **`hasattr` 能力探测的代价**：它把"方法名写错"从**启动崩溃**降级为**永久静默**。本轮确认该模式在本项目 ≥4 次（`af_bridge.by_room` ×2、`app.check_timeouts`、`mcp.create_rule`）。建议统一改为直接调用。

---

### P1-31 `trigger_engine.create_rule` 未定义 → MCP 触发器创建分支恒不可用

- **位置**：`butler/mcp/server.py:229-234`

```python
elif hasattr(trigger_engine, "create_rule"):
    rule = trigger_engine.create_rule(trigger_json)
else:
    return self._text_result("Error: trigger creation not supported", is_error=True)
```

- **实测**：全仓 grep `def create_rule` —— **无结果**
- **后果**：MCP 通道创建触发器**恒走 else 返回 "not supported"**。与 P1-30 不同，这里**至少给了错误提示**（fail-loud），所以定 P1 而非 P0
- **修复**：同样去掉 `hasattr`，直接调用或明确标记为未实现

---

## 第三部分：本轮**排除**的误报（避免误改）

| 疑似项 | 核查结论 |
|---|---|
| `agent_collab.py:366` `register_agent` 缺 4 参 | ❌ 误报：`self.register_agent(**agent)` 字典解包，AST 看不到内容。已修工具 |
| `memory_agent.py:223/295/500` `_extract_list` 缺 `text` | ❌ 误报：定义是 `classmethod(cls, text, ...)`，工具未排除 `cls`。已修工具 |
| 前端孤儿路由 `evolution/suggestions` / `ha/devices` / `memory/facts` | ❌ 误报：后端**都有**（`skill_routes.py:1051` / `ha_device_routes.py:165` / `memory_routes.py`）。工具模板匹配未处理 Starlette `{id:int}` 转换器 |
| `rt.event_stream` 未装配 | ✅ 已装配（`app.py:763`），非问题 |
| `rt.get`（10 处） | ❌ 误报：字典访问，非 Runtime 属性 |
| `PerceptionEngine` 模块本身 | ✅ 存在（`butler/core/perception_engine.py`，130 行，含 `SCENE_TEMPLATES`）——问题在**装配**而非模块 |

**注意**：4 处前端孤儿路由中，只有 `/api/skill/runs` 为真（后端仅有 `/api/skill/{skill_id}/runs`），**第五轮已报过**，此处为交叉确认。

---

## 第四部分：工作流现状与局限（诚实评估）

### 已解决

| v1.0 短板 | v1.1 状态 |
|---|---|
| AST 孤儿通道误报不可消除 | ✅ runtime 覆盖剔除 157 FP |
| deadcall 噪声大（562 处） | ✅ 提纯至 249 处 |
| contract 签名误报 | ✅ 降至 0 |

### 仍存在的短板

1. **runtime 覆盖仅 12.73%** —— 775/1733 方法被执行过，958 个未覆盖。意味着**仍有大量代码路径的"孤儿"判定依赖名称推断**，误报未根除。提升覆盖需要更多驱动场景（当前 12 步）。
2. **orphans 结果仍为 advisory-only** —— 剩 808 个，未经 runtime 覆盖验证，不可直接进报告。
3. **contract 路由匹配未支持 Starlette 转换器** —— 导致 3 处误报（已人工排除，工具待修）。
4. **未覆盖模块**：`mcp/server.py`、`agent_collab.py`、`modes/*`、`audiobook/`、`ilink/` 的深层逻辑本轮未通读。

### 环境约束记录

- bash 工具默认 60s 超时，**命令内 `timeout` 无效**，须传工具的 `timeout` 参数（毫秒）
- PyCG 全量迭代 >400s → 改用真实入口点 + `--max-iter 1`（0.3s）
- `roles/store.py` 首行含 U+FEFF → PyCG SyntaxError，bootstrap 已内置规范化

---

## 第五部分：八轮累计 · 经执行验证的结论

| 轮 | 编号 | 结论 | 状态 |
|---|---|---|---|
| 六 | V1/V2 | `DeviceRegistry` 无 `by_room` / `speak` 参数名 `device_id` | ✅ 实锤 |
| 六 | V3 | 房间映射 7 房间只 3 个可播 + 2 臆造键 | ✅ 实锤 |
| 六 | V4 | 草稿过期漏 return，闲聊喂进生成器 | ✅ 对照实验 |
| 六 | V5 | `AliasStore.match()` 只写不读 | ✅ 实锤 |
| 六 | V6 | `create_task(_queue.run())` 返回值未保存 | ✅ 实锤 |
| 七 | V7 | `DESKPILOT_API_TOKEN`/`TASK_REPORT_TOKEN` 启动硬失败 | ✅ 运行时撞到 |
| 七 | V8 | 简单命令空 payload → 全屋执行 | ✅ 实测 |
| 七 | V9/V10 | 过载清空队列 + `on_overload=None` + `overload_room='living'` | ✅ 实测 |
| 七 | V11 | SSRF 7 种绕过 | ✅ 实测 |
| 七 | V12 | 技能/设备/角色文件损坏静默丢失 | ✅ round-trip |
| **八** | **V13** | **`people_routes` 三合一死文件**（fastapi 缺失 + 未注册 + 属性未装配） | ✅ 实锤 |
| **八** | **V14** | **`decision_store.check_timeouts` 未定义，静默失效** | ✅ 实锤 |
| **八** | **V15** | **`trigger_engine.create_rule` 未定义** | ✅ 实锤 |

---

## 第六部分：下一步建议

### 修复优先级

| 优先级 | 项 |
|---|---|
| **立刻** | **P0-13 `people_routes` 删除或三处全修**（删文件成本最低） |
| **立刻** | P0-11 缺环境变量启动崩 / P0-12 空 payload 全屋误控 |
| **立刻** | P0-1~P0-10 各轮 P0 |
| **本周** | **P1-30 / P1-31 去掉 `hasattr` 探测**（同类共 4 处，一次性清理） |
| 排期 | 其余 P1/P2 |

### 工作流下一步

1. **扩充 drive.py 驱动场景**（12 步 → 30+），把 runtime 覆盖从 12.73% 提到 40%+，从根本上消除 orphans 误报
2. **修 contract 的 Starlette 转换器匹配**（`{id:int}`、 `{skill_id}`）
3. **mypy 严格模式**：能直接抓 `device=` vs `device_id=` 这类签名错配（P0-10 第二层），当前 mypy 未启用
4. **MQTT 主题契约**：发布/订阅配对检查（当前只查 HTTP 路由）

### 一条方法论沉淀

本轮 P0-13 的发现路径值得记录：**工具报 `rt.perception_engine` 疑似未定义 → 实测确认 AttributeError → 反查发现整个文件是死文件**。

`hasattr` 探测 + 未注册 + 依赖缺失三者叠加，让一个**从未工作的功能模块**在仓库里安静躺了很久——因为三者的故障形态都是"静默"而非"崩溃"。**静态分析能发现零件坏了，但发现不了整台机器从未通电**；这需要用 runtime 覆盖去回答"这段代码到底跑没跑过"。
