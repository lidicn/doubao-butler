# doubao-butler 第十二轮审计报告 · 重复实现检测

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮主线**：新增 `dupimpl` 阶段 —— 检测**跨文件重复实现**（第十一轮 P1-38 的根因：两份输出分发逻辑 → 主题漂移）
- **结论**：**1 个 P1（新维度，实测 7.5× 性能差异）+ 2 个 P2**，无新增 P0

---

## 第一部分：工作流迭代（v1.4 → v1.5）

### 1.1 新增 `dupimpl` 阶段 —— 跨文件重复实现检测

**动机**：第十一轮 P1-38（定时任务发到死主题 `sdd/notify`）的表面原因是"抄错一个字符串"，**根因是 `cron_task.py` 与 `skills/runner.py` 存在两份独立的输出分发逻辑**。只要有两份，漂移必然再现。

**方法** —— AST 结构指纹，不比较变量名/格式，能抓到"改名换姓的复制粘贴"：

```
指纹 = (调用名序列, 属性名集合, 字符串常量集合)
判定 = 调用 Jaccard ≥0.6 且 属性 ≥0.6 且 常量 ≥0.5
排序 = 按「常量集合差异」降序 → 常量不同 = 已发生漂移（最危险）
```

实测：扫描 990 个函数，检出 **24 对**重复实现。

### 1.2 v1.5 实测数据

| 阶段 | 结果 |
|---|---|
| tests | 7 failed / 586 passed / 10 skipped / 1 xfailed |
| **dupimpl** | **24 对重复实现 / 扫描 990 函数** |
| dupfiles | 5 个孤儿模块（稳定复现第十轮 P0-15） |
| mqtt | 订阅 15 / 发布 6 → orphan_pub 6（1 真实） |
| runtime | 覆盖 22.07%（1362/1733） |

执行顺序：`bootstrap / static / graph / tests / secrets / httpcontract / mqtt / dupimpl / runtime / dupfiles / orphans / deadcall / cycles / contract`

---

## 第二部分：第十二轮审计发现

### P1-39 九份 `get_conn` 各自持有**独立连接**，七份已漂移 —— 写入慢 7.5×

- **位置**：`butler/store/db.py:35`（参考）+ **8 份副本**

`dupimpl` 检出的 24 对中，`get_conn` 重复出现 6 次。深挖后发现这不只是重复代码，而是**已产生实际行为差异**。

#### 全仓清单（9 份）

| 模块 | `synchronous` 实测 | 有 close 路径 |
|---|---|---|
| `butler/store/db.py` | **1 (NORMAL)** ✅ | ✅ `close()` |
| `butler/store/task_store.py` | **1 (NORMAL)** ✅ | ❌ |
| `butler/core/decision_store.py` | **2 (FULL)** ❌ | ❌ |
| `butler/core/notification_store.py` | **2 (FULL)** ❌ | ❌ |
| `butler/core/pwa_chat_store.py` | **2 (FULL)** ❌ | ❌ |
| `butler/core/user_location.py` | **2 (FULL)** ❌ | ❌ |
| `butler/ha_tools/whitelist.py` | **2 (FULL)** ❌ | ❌ |
| `butler/proactive/engine.py` | **2 (FULL)** ❌ | ❌ |
| `butler/timeseries/store.py` | **2 (FULL)** ❌ | ❌ |

参考实现（`store/db.py:35-56`）设了三条 PRAGMA：
```python
c.execute("PRAGMA journal_mode=WAL")
c.execute("PRAGMA synchronous=NORMAL")     # ← 7 份副本都没有
c.execute("PRAGMA busy_timeout=5000")
c.row_factory = sqlite3.Row
```
副本只设了 WAL + row_factory。

#### 实测一：写入性能差 7.5×

```
WAL + synchronous=NORMAL (store/db.py 参考):    3 ms / 300 次提交
WAL + synchronous=FULL   (7 个副本的默认值):   22 ms / 300 次提交
→ 副本比参考实现慢 7.5×
```

WAL 模式下 `synchronous=FULL` 会为**每次提交**强制 fsync。这 7 个 store 承担决策、通知、PWA 聊天、用户位置、设备白名单、主动问询、时序数据 —— 全是高频写入路径。

#### 实测二：九份模块持有 9 个**互不相同**的连接对象

```
9 个 store 模块 → 9 个连接对象，去重后 9 个
两个模块的连接是否同一对象: False
同一 DB 文件: True
```

每个模块各自 `sqlite3.connect()` 同一个 `butler.db`，各自维护模块级 `_conn` 单例。

#### 实测三：跨连接写锁 —— 阻塞满 5 秒后抛异常

```
持锁期间另一连接写入: {'err': 'OperationalError: database is locked', 'ms': 5058.97}
```

一个模块持有写事务时，**另一个模块的连接会阻塞满 `busy_timeout`(5000ms) 然后抛 `OperationalError`**。9 个连接之间**无任何协调**。

> 说明：普通并发写（50 次 × 2 线程）实测无错误 —— WAL + busy_timeout 在大多数情况下能兜住。异常只在**长事务持锁**时出现（如 `_init()` 里的 `executescript`、多语句批量写入）。

#### 实测四：关停时 8/9 的连接从不关闭

`app.py:792-794` 只调用 `butler.store.db.close()`。**其余 8 个模块没有任何 close 路径**（`decision_store` 的 `close_decision` 是业务方法，不是关连接）。

进程退出时 OS 回收 fd，SQLite 不会做 WAL checkpoint → 残留 `-wal` 文件，下次启动需 WAL 恢复。数据不丢，但属资源卫生问题，且容器 graceful shutdown 的意义被削弱。

#### 后果汇总

| 维度 | 影响 |
|---|---|
| 性能 | 7 个高频 store 的每次提交慢 7.5× |
| 稳定性 | 长事务持锁时，其他模块写入阻塞 5s 后抛 `database is locked` |
| 一致性 | 9 个独立连接无协调，无共享事务边界 |
| 资源 | 8/9 连接关停时不关闭 |

**修复**：收敛为单一连接工厂（推荐）。
```python
# 新增 butler/store/conn.py
def get_conn() -> sqlite3.Connection:
    """全仓唯一连接工厂：WAL + synchronous=NORMAL + busy_timeout + row_factory。
    各 store 模块只保留 _init(c) 建表，不再各自 connect。"""
```
最小改动版：先给 7 份副本补上 `c.execute("PRAGMA synchronous=NORMAL")`（一行 × 7）。

---

### P2-08 `_parse_llm_json` / `_parse_json` 两份 LLM JSON 提取器

- `butler/memory/extractor.py:214` 与 `butler/memory/feeder.py:284`
- 逻辑等价（去 markdown 代码块 → 找 `{`/`}` → `json.loads`），仅代码块剥离写法略异
- 相似度：调用 0.889 / 属性 0.875 / 常量 0.5
- 当前**行为一致**，非缺陷；但未来任一处加健壮性处理（如处理嵌套、BOM、多 JSON 块）另一处不会同步

### P2-09 `upsert_device` / `upsert_role` 两份 API 处理器

- `butler/api/device_routes.py:18` 与 `butler/api/role_routes.py:25`
- 调用序列完全相同（Jaccard 1.0），仅资源名不同（`device` vs `role`）
- 属合理的同类 CRUD 抽象不足，风险低于 P2-08

---

## 第三部分：本轮**排除**的误报

| 疑似 | 核查结论 |
|---|---|
| `butler/schema.py:58 validate_trigger` vs `butler/triggers/schema.py:58` | ⚠️ **判据命中但需人工确认**：仅差一个常量 `resources`。两个模块都在役（`schema.py` 被引用），可能是"抽出公共 schema"的过渡态，也可能是孤儿副本 —— 与 P0-15 的 5 个孤儿属同类，建议与 P0-15 一并核查 |
| `memory/extractor.py` / `feeder.py` 两份解析器行为不同 | ❌ 实测等价（都返回 `{}` 兜底） |
| `get_conn` 副本缺 `busy_timeout=5000` | ❌ **不成立**：Python `sqlite3.connect()` 默认 `timeout=5.0` 会设置 busy_timeout，实测 9 个连接**全部**为 5000。差异只在 `synchronous` |
| `dupimpl` 检出的 24 对都是缺陷 | ❌ 多数是合理的同类 CRUD / 脚本模板（`scripts/*_ruler.py`），已按 drift 排序，仅前 3 对有实质意义 |

> **一处自我更正**：我最初据源码文本推断"7 份副本缺 `busy_timeout=5000`"，实测 9 个连接的 busy_timeout **全部为 5000**（Python 默认值）。**该条不成立，已从结论中移除** —— 只剩 `synchronous` 是真差异。这正是"验证优先"的价值：源码里没写 ≠ 运行时没有。

---

## 第四部分：工作流现状与局限

### v1.5 已解决

| v1.4 短板 | v1.5 状态 |
|---|---|
| 重复实现靠人工比对发现 | ✅ `dupimpl` 阶段固化，检出 24 对 |
| P1-38 根因（两份分发逻辑）未工具化 | ✅ 同类问题现可自动检出 |

### 仍存在的短板

1. **`dupimpl` 未自动区分"合理重复"与"危险漂移"** —— 24 对中多数是同类 CRUD 或脚本模板，需人工筛。当前只按 drift 排序
2. **`dupimpl` 未检出 P1-38 的那一对** —— `cron_task.py` 的输出分发与 `skills/runner.py` 的 `_push_tv` 结构差异过大（一个同步 publish、一个 async await），相似度未达阈值。**说明阈值对"同一个概念、不同实现风格"的重复不敏感**
3. **runtime 覆盖 22.07%** —— 371 个方法未执行
4. **`settings` 扫描未区分字段与方法**（19 个疑似多为方法误报）
5. **MQTT 无法判断外部发布方** —— 15 个 orphan_sub 全靠人工判断
6. **未覆盖**：`modes/`、`morning/`、`ilink/`、`memory/`（本轮仅触及 memory 的两份解析器）、`mcp/server.py`、`agent_collab.py`

---

## 第五部分：十二轮累计 · 经执行验证的结论

| 轮 | 编号 | 结论 |
|---|---|---|
| 六 | V1-V6 | `by_room` 不存在 / `speak` 参数名 / 房间映射 / 穿透 / 别名只写不读 / worker Task |
| 七 | V7-V12 | 缺环境变量启动崩 / 空 payload 全屋误控 / 过载清空队列 / SSRF 绕过 / 文件损坏静默丢失 |
| 八 | V13-V15 | `people_routes` 三合一死文件 / `check_timeouts` / `create_rule` |
| 九 | V16-V18 | 出站 HTTP 从不检查状态码 / 门禁红的 / 天气接口硬编码 |
| 十 | V19-V21 | 五个分叉孤儿模块（769 行）/ 3 处硬编码 / 三份 DeskPilot 并存 |
| 十一 | V22 | 定时任务 `tv_notify` 发到死主题 `sdd/notify` 且谎报成功 |
| **十二** | **V23** | **九份 `get_conn` 各自独立连接，七份缺 `synchronous=NORMAL`（实测慢 7.5×），8/9 关停不关闭** |

---

## 第六部分：下一步建议

### 修复优先级

| 优先级 | 项 | 成本 |
|---|---|---|
| **立刻** | P0-15 删 5 个孤儿文件（工具已连续两轮自动确认） | 极低 |
| **立刻** | P0-14 补状态码检查（`integrations/deskpilot.py` + `tools/desk_pilot.py`） | 低 |
| **本周** | **P1-39 收敛 `get_conn` 为单一工厂**；最小改动版 = 给 7 份副本各加一行 `PRAGMA synchronous=NORMAL` | 低 |
| 本周 | P1-38 改 `sdd/notify` → `PUB_TV_NOTIFY`，或删掉 cron_task 重复分发 | 低 |
| 排期 | P1-30/31 去掉 `hasattr` 探测（4 处） | 低 |
| 排期 | P2-08 合并两份 JSON 解析器 | 低 |

### 工作流下一步

1. **降低 `dupimpl` 阈值 + 增加"概念级"匹配** —— 当前抓不到 `cron_task` vs `runner` 那类同概念不同风格的重复，可加入"相同字符串常量簇"作为独立信号
2. **`dupimpl` 结果自动分级** —— 用 coverage 判断两份是否都在役；只有一份在役 → 高危（漂移已发生且副本未同步）
3. **新增"连接/资源生命周期"检查** —— 本轮发现 8/9 连接无 close 路径，可固化为"有 `get_X` 无 `close_X`"扫描
4. **runtime 覆盖提到 40%+**

---

## 附：环境注意事项（会复现）

- **沙盒会重置**：本轮开局 8 个依赖全丢。清单：
  ```
  pytest pytest-asyncio paho-mqtt starlette==0.37.2 aiohttp httpx
  edge-tts apscheduler coverage onecode-pycg python-multipart pillow
  ```
- **`pycg` 必须装 `onecode-pycg`**
- **Python 3.10 vs 项目要求 3.11** —— `homesdk` 需 `PYTHONPATH=vendor/homesdk/src`
- bash 默认 60s 超时，**命令内 `timeout` 无效**，须传工具参数（毫秒）
