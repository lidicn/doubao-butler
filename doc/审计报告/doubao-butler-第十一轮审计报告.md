# doubao-butler 第十一轮审计报告 · MQTT 契约 + 孤儿检测自动化

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮主线**：① `dupfiles` 阶段收敛到可自动复现 P0-15 ② 新增 **`mqtt` 阶段**（发布/订阅主题契约，前轮列为下一步）
- **结论**：**1 个 P1（新增，新维度）+ 0 个 P0 新增**；`dupfiles` 首次实现**全自动化复现**第十轮人工发现

---

## 第一部分：工作流迭代（v1.3 → v1.4）

### 1.1 `dupfiles` 收敛：23 → **5**，与第十轮人工结论完全一致

第十轮我人工找出的 5 个分叉孤儿模块，本轮 `dupfiles` 阶段**全自动复现**：

```
butler/engine.py           (249 行, 与 butler/proactive/engine.py 差异 737)
butler/core/agent_routes.py (74 行, 与 butler/api/agent_routes.py 差异 235)
butler/defaults.py        (111 行, 与 butler/skills/defaults.py 差异 161)
butler/deskpilot.py       (206 行, 与 butler/integrations/deskpilot.py 差异 50)
butler/doubao.py          (129 行, 与 butler/integrations/doubao.py 差异 47)
```

**剔除误报的四道过滤**（本轮调试重点）：

| 过滤 | 剔除的误报 |
|---|---|
| ① 精确引用（全仓非 .md 文本，含 Dockerfile/yml/sh/toml） | `butler/app.py`（被 `.gates.toml` smoke_targets + Dockerfile:36 引用） |
| ② 括号导入 `from X import (a, b, ...)` | `butler/api/agent_routes.py`（app.py 多行括号导入） |
| ③ **coverage 存活**（执行过 ⇒ 动态加载，非孤儿） | 7 个 `skills/engines/*/engine.py`（引擎加载器按路径动态导入）、`butler/timeseries/store.py` |
| ④ 排除同 stem twin 文件 / `importlib` 精确模块名 | `butler/engine.py` 曾被 `importlib.import_module("butler.triggers.engine")` 子串误判；`butler/doubao.py` 被 twin 的 logger 名误判 |

> 关键改进是 **③**：`skills/engines/*/engine.py` 报出 7 个假孤儿，是因为它们由引擎加载器动态导入——**静态引用分析天然抓不到，只有 runtime 覆盖能证明它们活着**。

### 1.2 新增 `mqtt` 阶段 —— 发布/订阅主题契约

前轮列为"下一步"，本轮实现。三类缺陷：

- `orphan_pub`：发布了但无人订阅（消息石沉大海）
- `orphan_sub`：订阅了但无人发布（回调永不触发）
- `near_miss`：字面量近似但不相等（拼写/层级错误，最难发现）

实现要点：
- 从 `bus/topics.py` 预解析常量，**支持 f-string 前缀变量**（`f"{TV_PREFIX}/status"` → `tv/livingroom/status`；初版只取 Constant 片段，错误解析成 `/status`，造成 15 个订阅全部误报）
- 支持 `SUB_TOPICS` 元组、`client.subscribe(t)`、常量名参数
- 通配符 `+`/`#` 用片段匹配

### 1.3 `secrets` severity 判据修正（承接第十轮遗留）

第十轮我人工指出"`config.py` 13 处标 high 是判据局限"，本轮已修：新增 `_collect_env_overridden_fields()` 从 `Settings.load()` 收集被 `_env("X")` 覆盖的字段名。

**high 从 21 → 8**，与第十轮人工修正结果一致。

### 1.4 v1.4 实测数据

| 阶段 | 结果 |
|---|---|
| tests | 7 failed / 586 passed / 10 skipped / 1 xfailed |
| secrets | **36 处硬编码（8 处 high）** ← 已修正 |
| httpcontract | 16 处未检查状态码 |
| **mqtt** | **订阅 15 / 发布 6 → orphan_pub 6（其中 1 真实）/ orphan_sub 15（均为外部系统，设计如此）/ near_miss 0** |
| **dupfiles** | **5 个孤儿模块**（自动复现 P0-15） |
| graph | 707 节点 / 1638 边 |
| runtime | 覆盖 22.07%（1362/1733） |
| deadcall | 249 处，4 处 `hasattr` 保护 |
| contract | 签名不匹配 0 / 前端孤儿路由 1 |

执行顺序：`bootstrap / static / graph / tests / secrets / httpcontract / mqtt / runtime / dupfiles / orphans / deadcall / cycles / contract`

---

## 第二部分：第十一轮审计发现

### P1-38 定时任务 `tv_notify` 输出到**死主题** `sdd/notify`，且谎报成功

- **位置**：`butler/core/cron_task.py:393-404`

这是 `mqtt` 阶段唯一的真实命中（其余 5 个 orphan_pub 是发往 TV / Node-RED 的外部通道，README 架构明确，属设计）。

```python
elif out_type == "tv_notify":
    from butler.runtime import get_runtime
    rt = get_runtime()
    if rt.mqtt:
        rt.mqtt.publish("sdd/notify", {          # ← 死主题
            "text": message,
            "source": "koin_action"              # ← 另一项目的遗留标识
        })
        action_results.append("tv_notify: ok")   # ← 谎报成功
```

#### 三重证据

1. **全仓零订阅**：`sdd/notify` 在整个仓库只出现这 1 次。`mqtt` 阶段的 15 个订阅主题（`tv/livingroom/*`、`butler/event/+`、`butler/trigger/+`、`ma/*`、`butler/inbox/*`、`adm/*`）**无一匹配**
2. **文档零记载**：`grep -rn "sdd" doc/ *.md` 无结果
3. **`"source": "koin_action"`** —— `koin` 与本项目无关，是**从别的仓库复制粘贴**的痕迹

#### 对照组：正确实现就在隔壁

`skills/runner.py:319-320` 对同样的 `tv_notify` 输出类型：
```python
if otype == "tv_notify":
    await self._push_tv(rt, skill, result, meta, tts=out.get("tts", True), role=role)
```
走 `PUB_TV_TTS` / `PUB_TV_NOTIFY`（`tv/livingroom/cmd/tts` / `cmd/notify`）—— **正确**。

`cron_task.py` 是一条**独立重复的**输出分发路径，`xiaomi_speak`（走 `rt.dialog.speak_as_role`）和 `bark`（走 `self.bark.push`）都写对了，**只有 `tv_notify` 抄错了主题**。

#### 后果

- **确认在役**：`CronTaskExecutor` 由 `app.py:347-348` 装配（`rt.cron_task_executor`），路径真实可达
- 定时技能配置 `output: [{"type": "tv_notify"}]` → **电视永远不会收到通知**
- 但 `action_results` 记为 `"tv_notify: ok"` → 执行日志显示**成功**
- 用户看到"任务执行成功"，电视没反应，日志里查不出任何异常

**修复**：
```python
elif out_type == "tv_notify":
    rt = get_runtime()
    if rt.mqtt:
        rt.mqtt.publish(PUB_TV_NOTIFY, {"text": message, "source": "cron_task"})
```
> 更好的做法是删掉 `cron_task.py` 这段重复分发，统一走 `skills/runner.py` 的 `_push_tv()`——同一份输出逻辑两处实现，正是本次漂移的根源。

---

### 本轮无新增 P0

`dupfiles` 复现的 5 个孤儿模块即第十轮 P0-15（已报），`mqtt` 与 `secrets`/`httpcontract` 未再产生 P0 级新发现。

---

## 第三部分：本轮**排除**的误报（重要）

| 疑似 | 核查结论 |
|---|---|
| **7 个 `skills/engines/*/engine.py` 是孤儿** | ❌ 误报：由引擎加载器**按路径动态导入**，静态引用分析抓不到。coverage 显示方法被执行过 → 已由过滤③ 剔除 |
| **`butler/timeseries/store.py` 是孤儿** | ❌ 误报：coverage 有执行记录 |
| **`butler/app.py` 是孤儿** | ❌ 误报：被 `.gates.toml` smoke_targets、`Dockerfile:36`、`docker-compose.dev.yml:87` 引用 |
| **`butler/api/agent_routes.py` 是孤儿** | ❌ 误报：`app.py` 用 `from butler.api import (agent_routes, ...)` 多行括号导入，初版正则未覆盖 |
| **`butler/sandbox_mgr` 未装配** | ❌ 误报：`runtime.py:43` 是 dataclass 字段（默认 None），且 `app.py:371` 赋值 |
| **`rt.notifier` 未装配** | ❌ 误报：`app.py:514` 有 `rt.notifier = AppNotifier(rt)` |
| **`rt.trigger_registry` 未装配** | ❌ 误报：`app.py:542` 赋值（非 dataclass 字段，动态挂） |
| **15 个订阅主题无人发布** | ⚪ 设计如此：均订阅**外部系统**（TV、memory-agent、ADM）的推送，发布方不在本仓库 |
| **`member_by_name` / `nickname_of` 未在 Settings 定义** | ❌ 误报：是 Settings 的**方法**（config.py:360/367），非字段。扫描只收集了字段 |
| **`rt.perception_engine` 未装配** | ✅ **确认为真**（第八轮 P0-13 第三组件）：`rt.xxx` 全量扫描仅剩 `rt.get`（字典误报）与此项 |

> **一处自我更正**：第十一轮初检时我用 `Runtime.__dataclass_fields__` 判断 `sandbox_mgr` "不在 dataclass"，一度怀疑第八轮结论有误。经复核 `Runtime` **确为 dataclass**，`sandbox_mgr` 是字段，`perception_engine` **确实不是** —— **第八轮 P0-13 的结论成立**。

---

## 第四部分：工作流现状与局限

### v1.4 已解决

| v1.3 短板 | v1.4 状态 |
|---|---|
| dupfiles 23 个含大量误报，需人工核验 | ✅ 收敛至 5 个，与人工结论一致 |
| secrets 判据误标 13 处 | ✅ 修正，high 21 → 8 |
| MQTT 契约未覆盖（前轮列为下一步） | ✅ 新增 `mqtt` 阶段 |
| 孤儿检测无法识别动态加载 | ✅ coverage 存活过滤 |

### 仍存在的短板

1. **runtime 覆盖 22.07%** —— 371 个方法未执行；orphans 的 731 条仍是 advisory-only
2. **`settings.X` 扫描未区分字段与方法** —— 产生 19 个疑似，经核查多为方法误报（应加入方法名集合）
3. **MQTT 无法判断"外部发布方"** —— 15 个 orphan_sub 全靠人工判断为设计如此，工具无法区分"外部系统发布"与"真的没人发布"
4. **tests 失败分类未自动化** —— 7 个失败中 2 真 5 环境
5. **沙盒不稳定** —— 本轮遇到多次 502 与依赖丢失，`pytest` 需在同一条命令内安装后立即运行
6. **未覆盖**：`modes/`、`morning/`、`ilink/`、`memory/`、`mcp/server.py`、`agent_collab.py` 深层逻辑

---

## 第五部分：十一轮累计 · 经执行验证的结论

| 轮 | 编号 | 结论 |
|---|---|---|
| 六 | V1-V6 | `by_room` 不存在 / `speak` 参数名 / 房间映射 / 穿透 / 别名只写不读 / worker Task |
| 七 | V7-V12 | 缺环境变量启动崩 / 空 payload 全屋误控 / 过载清空队列 / SSRF 绕过 / 文件损坏静默丢失 |
| 八 | V13-V15 | `people_routes` 三合一死文件 / `check_timeouts` / `create_rule` |
| 九 | V16-V18 | 出站 HTTP 从不检查状态码 / 门禁红的：12 个硬编码内网 URL / 天气接口硬编码 |
| 十 | V19-V21 | 五个分叉孤儿模块（769 行）/ `get_weather` 等 3 处硬编码 / 三份 DeskPilot 并存 |
| **十一** | **V22** | **定时任务 `tv_notify` 输出到死主题 `sdd/notify` 且谎报成功** |

---

## 第六部分：下一步建议

### 修复优先级

| 优先级 | 项 | 成本 |
|---|---|---|
| **立刻** | P0-15 删 5 个孤儿文件（769 行）—— **本轮已由工具自动确认** | 极低 |
| **立刻** | P0-14 补状态码检查（`integrations/deskpilot.py` + `tools/desk_pilot.py` 两处） | 低 |
| **本周** | **P1-38 改 `sdd/notify` → `PUB_TV_NOTIFY`**，或更好：删除 cron_task 重复分发，统一走 runner | 低 |
| 本周 | P1-32/35 清理 8 处真 high 硬编码 | 中 |
| 排期 | P1-30/31 去掉 `hasattr` 探测（4 处） | 低 |

### 工作流下一步

1. **`settings` 扫描加入方法名集合** —— 消除 19 个方法误报
2. **MQTT 区分"外部发布方"** —— 可白名单化已知外部前缀（`tv/`、`ma/`、`adm/`）
3. **tests 失败自动分类** —— 重跑 + 环境标记
4. **runtime 覆盖提到 40%+** —— 目标消除 orphans 误报
5. **新增"重复实现"检测** —— P1-38 的根因是 `cron_task.py` 与 `skills/runner.py` 两份输出分发逻辑。可比对同类方法的结构相似度

---

## 附：环境注意事项（会复现）

- **沙盒会重置**：本轮两次遇到 502 / 依赖丢失。依赖清单：
  ```
  pytest pytest-asyncio paho-mqtt starlette==0.37.2 aiohttp httpx
  edge-tts apscheduler coverage onecode-pycg python-multipart pillow
  ```
- **`pycg` 必须装 `onecode-pycg`** —— `pip install pycg` 装出的目录名为 `PyCG` 且内容为空，`import pycg` 失败
- **Python 3.10 vs 项目要求 3.11** —— `homesdk` 因 `requires-python >=3.11` 装不上，需 `PYTHONPATH=vendor/homesdk/src`
- bash 默认 60s 超时，**命令内 `timeout` 无效**，须传工具参数（毫秒）
