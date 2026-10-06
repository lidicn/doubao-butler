# doubao-butler 第十四轮审计报告 · lifecycle 双通道 + 深挖未审计区

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮主线**：① `lifecycle` 扩为**双通道**（补上第十三轮漏报的实例属性）② 深挖前十三轮**从未通读过**的 `morning/` `modes/` `timeseries/anomaly.py`
- **结论**：**1 个 P1（三处同源，跨三个 critical 模块）**，无新增 P0

---

## 第一部分：工作流迭代（v1.6 → v1.7）

### 1.1 `lifecycle` 双通道：模块级全局 + **实例属性**

第十三轮我坦白过：`lifecycle` 只扫模块级全局变量，**漏掉了 `PresenceStore._conn`（实例属性）**。本轮补齐。

| 通道 | 判据 |
|---|---|
| `module-global` | 模块级 `Assign`/`AnnAssign`，变量名匹配 `_?(conn\|connection\|session\|client\|pool\|db)` |
| **`instance-attr`（新）** | 类方法内 `self.X = None` / `self.X: T = None`，同样命名规则 |

**结果 11 处 high**（模块级 10 / 实例属性 1），实例属性通道**成功捕获第十三轮漏报的 `PresenceStore`**。

### 1.2 一处必须记录的修复：`\bconn\b` 匹配不到 `_conn`

初版双通道把 `butler/store/db.py` 也判成 high——而它明明有正确的 `close()`：

```python
def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()      # ← 明显操作了 _conn
            _conn = None
```

根因：`\bconn\b` 中的 `\b` 是单词边界，而 `_conn` 里 `c` 前面是 `_`（同属 `\w`），**边界不成立** → 判定"未操作资源"。修正为 `(?<!\w)_?name\b` 后 `store/db.py` 正确排除。

> 这类 bug 的隐蔽性在于：它只在**带下划线的私有变量**上发生，而 Python 模块级单例恰好几乎全是 `_conn`。若未发现，报告会多出一个假阳性、掩盖真问题。

### 1.3 误报同步修正

`butler/store/write_failures.py` 上一轮在列、本轮**正确排除**——它有 `_reset_conn()`（真正 `.close()`）。`store/db.py` 同理。

### 1.4 v1.7 实测数据（audit.py 本轮经重建后全量复跑）

| 阶段 | 结果 |
|---|---|
| tests | 7 failed / 586 passed / 10 skipped / 1 xfailed |
| **lifecycle** | **11 处 high（模块级 10 / 实例属性 1）** |
| errpath | 2 处（1 真实：`config_routes.py:23`） |
| dupimpl | 24 对 |
| dupfiles | 5 个孤儿模块（连续四轮稳定复现 P0-15） |
| mqtt | 订阅 15 / 发布 6 → orphan_pub 6（1 真实） |
| httpcontract | 16 处未检查状态码 |
| secrets | 36 处硬编码（8 处 high） |
| deadcall | 120 处（4 处 `hasattr` 保护） |
| orphans | runtime 剔除 234 FP，剩 731（advisory） |

> **过程说明**：本轮开发中 `audit.py` 因一次字符串替换逻辑错误被破坏为 200MB 并截断，后半段按既有逻辑重建。重建后**全 17 个阶段均复跑通过，且稳定复现前十三轮全部关键结论**（P0-15 的 5 个孤儿、`errpath` 的 `config_routes.py`、`dupimpl` 的 24 对、`mqtt` 的 orphan_pub 6）——可视为一次意外的**回归验证**。

---

## 第二部分：第十三轮遗留的确认

### `PresenceStore` 确认为第 10 个独立连接，且无 close（已在第十三轮报出）

`lifecycle` 实例属性通道独立确认：

```
[high] butler/presence/store.py  instance-attr  ['_conn']  类 PresenceStore
```

- 只设 `PRAGMA journal_mode=WAL`，**无 `synchronous=NORMAL`**
- 全文件**无 `close()`、无 `conn.close()`**
- `app.py:407` 装配，在役

---

## 第三部分：第十四轮审计发现

### P1-42 三个 critical 模块的定位判定**硬编码成员名 `lidicn`**，绕过可配置的成员系统

- **位置**：
  - `butler/morning/routine.py:94-96`
  - `butler/modes/auto_switch.py:71-73`
  - `butler/timeseries/anomaly.py:155-157`（另含 `:161` `entity_id="person.lidicn"`）

`.gates.toml` 的 `critical_globs` **同时包含 `butler/morning/*` 与 `butler/timeseries/*`**——作者自己标注了这两处为关键路径。

#### 代码

```python
# morning/routine.py:92-101（另两处结构完全相同）
snapshot = self.rt.presence_engine.snapshot()
lidicn_loc = snapshot.get("users", {}).get("lidicn", {})   # ← 硬编码
room = lidicn_loc.get("room", "")
confidence = lidicn_loc.get("confidence", 0)
if "主卧" in room and confidence >= 0.7:
    user_left_bedroom = False
```

而项目**存在**可配置成员系统，且 `dialog.py` / `persona.py` 正在正确使用它：

```python
# core/dialog.py:155 —— 正确写法
mc = self.s.member_by_name(member)
# core/persona.py:15 —— 正确写法
mc = self.s.member_by_name(member)
```

全仓 grep 确认**没有** `primary_member` / `owner` / `default_member` 之类的配置项。

#### 实锤：对照组实验

```
=== 成员名 = 代码中硬编码的 'lidicn' ===
  lidicn 仍在主卧 0.9     → user_left_bedroom=False  (还没起床)  ✅
  lidicn 已到客厅 0.9     → user_left_bedroom=True   (起床)      ✅

=== 成员名 = 其他部署者 ===
  Kevin 仍在主卧 0.9      → user_left_bedroom=True   (起床)      ❌
      实际取到 room='' confidence=0  ← 空 dict，因为查的是 'lidicn'
  张三 仍在主卧 0.95      → user_left_bedroom=True   (起床)      ❌
      实际取到 room='' confidence=0
```

#### 三处各自的失效形态（**都不报错，形态各异**）

| 模块 | 受影响逻辑 | 失效形态 |
|---|---|---|
| `morning/routine.py` | 起床判定（是否离开主卧） | 取不到 → `room=""` → 条件不成立 → `user_left_bedroom` **保持默认 `True`** → **判定为"已起床"** → 晨起播报会在人还在床上时触发 |
| `modes/auto_switch.py` | 规则 3（观影模式）、规则 4（睡眠模式）+ `_reset_if_needed` 两处复位条件，共 **4 个判断点** | `lidicn_room=""` → 所有 `"客厅" in lidicn_room` / `"主卧" in lidicn_room` 恒 False → **观影/睡眠自动切换永不触发** |
| `timeseries/anomaly.py` | 起床异常检测 | 同上 → **告警永不产生** |

一致性问题：`morning` 是**静默放行**（更危险——功能看起来在工作，只是判错），`modes`/`anomaly` 是**静默失效**（功能完全不工作）。

#### 严重度说明

对作者本人（成员就叫 `lidicn`）此功能正常，所以现网无症状。但：
- README 的部署章节面向通用部署，`.env.example` + `config.json` 提供了成员配置能力
- 任何改名、新增家庭、或第三方部署 → **三处功能同时静默降级**
- 与之并列的 `secrets` 阶段已报 8 处 high 硬编码内网地址，同属"作者环境假设渗入逻辑"

**修复**（三处同构，改动对称）：
```python
# 引入主成员概念（建议加到 Settings：primary_member: str = ""）
name = get_settings().primary_member or "lidicn"   # 兼容旧行为
loc = snapshot.get("users", {}).get(name, {})
```
`anomaly.py:161` 的 `entity_id="person.lidicn"` 同步改为动态拼接。

---

## 第四部分：本轮**排除**的误报

| 疑似 | 核查结论 |
|---|---|
| `butler/store/db.py` 无释放路径 | ❌ **假阳性**（本轮已修）：`store/db.py:504` 有正确 `close()`；误因是 `\bconn\b` 匹配不到 `_conn` |
| `butler/store/write_failures.py` 无释放 | ❌ 有 `_reset_conn()` 真正 `.close()`，正确排除 |
| `butler/guard/push_guard.py` 连接泄漏 | ❌ 每次 `sqlite3.connect()` 后都有 `conn.close()`（`_init_db:100-121`、`_audit:142-148`），是 per-call 开关而非泄漏（性能不佳但不漏） |
| `deadcall` 的 `c.patch`（memory_agent:409） | ❌ `httpx.AsyncClient` 确有 `.patch()` |
| `deadcall` 的 `text.rfind`、`math.exp`、`pool.submit` | ❌ 均为标准库/第三方真实成员，工具未解析接收者类型 |
| `morning/routine.py` 的 `device_id="xiao_touch8"` | ❌ `devices.py:128` 种子表中确有该设备（主卧室），**一致**，非缺陷 |

---

## 第五部分：工作流现状与局限

### v1.7 已解决

| v1.6 短板 | v1.7 状态 |
|---|---|
| `lifecycle` 漏实例属性（第十三轮自陈） | ✅ 双通道，已捕获 `PresenceStore` |
| `\bname\b` 对 `_name` 失效 | ✅ 改 `(?<!\w)_?name\b` |
| audit.py 被破坏 | ✅ 重建完成，17 阶段全量复跑、结论一致 |

### 仍存在的短板

1. **`dupimpl` 仍未检出 P1-38 那一对** —— 同概念不同实现风格，阈值不敏感（连续两轮未解决）
2. **`errpath` 的 6 轮调参可能过拟合** —— 未在第二个仓库验证泛化性
3. **runtime 覆盖 22.07%** —— 371 个方法未执行，orphans 731 条仍 advisory-only
4. **`deadcall` 不解析接收者类型** —— 120 处中绝大部分是标准库/第三方成员误报，信噪比低
5. **未通读**：`memory/`（626 行）、`mcp/`（1006 行）、`agent_collab.py`、`integrations/ilink/`（3 文件）

---

## 第六部分：十四轮累计 · 经执行验证的结论

| 轮 | 编号 | 结论 |
|---|---|---|
| 六 | V1-V6 | `by_room` 不存在 / `speak` 参数名 / 房间映射 / 穿透 / 别名只写不读 / worker Task |
| 七 | V7-V12 | 缺环境变量启动崩 / 空 payload 全屋误控 / 过载清空队列 / SSRF 绕过 / 文件损坏静默丢失 |
| 八 | V13-V15 | `people_routes` 三合一死文件 / `check_timeouts` / `create_rule` |
| 九 | V16-V18 | 出站 HTTP 从不检查状态码 / 门禁红的 / 天气接口硬编码 |
| 十 | V19-V21 | 五个分叉孤儿模块（769 行）/ 3 处硬编码 / 三份 DeskPilot 并存 |
| 十一 | V22 | 定时任务 `tv_notify` 发到死主题 `sdd/notify` 且谎报成功 |
| 十二 | V23 | 九份 `get_conn` 各自独立连接，七份缺 `synchronous=NORMAL`（慢 7.5×） |
| 十三 | V24-V25 | 18 处 connect 仅 2 处设 synchronous / `_presence_poll_task` 关停不取消 |
| **十四** | **V26** | **三处 critical 模块硬编码成员名 `lidicn`，绕过可配置成员系统（4 个判断点失效 + 1 处静默放行）** |

---

## 第七部分：下一步建议

### 修复优先级

| 优先级 | 项 | 成本 |
|---|---|---|
| **立刻** | P0-15 删 5 个孤儿文件（工具连续四轮自动确认） | 极低 |
| **立刻** | P0-14 补状态码检查（`integrations/deskpilot.py` + `tools/desk_pilot.py`） | 低 |
| **本周** | **P1-42 引入 `primary_member` 配置，替换三处硬编码 `lidicn`** | 中（改动对称） |
| 本周 | P1-40 收敛 SQLite 连接工厂（18 处 → 1 处） | 中 |
| **本周** | P1-41 关停时 cancel `_presence_poll_task` | 极低（3 行） |
| 排期 | P1-32/35 清理 8 处 high 硬编码内网地址 | 中 |
| 排期 | P2-08 / P1-30/31 / `config_routes.py:23` | 低 |

### 工作流下一步

1. **新增 `hardcoded-identity` 扫描** —— 本轮 P1-42 是先看到 `morning/routine.py` 再 grep 全仓才发现的。应固化为"逻辑判定中出现个人名/房间名字面量"检查（区分种子数据与判定逻辑：只看含 `.get(` / `==` / `in [` 的行）
2. **`dupimpl` 加"常量簇"独立信号** —— 连续两轮未解决 P1-38 那类同概念重复
3. **`deadcall` 解析接收者类型** —— 当前信噪比过低（120 处中绝大多数误报）
4. **补扫未通读区** —— `memory/`、`mcp/`、`agent_collab.py`、`ilink/`

---

## 附：环境注意事项（会复现）

- **沙盒会重置**（本轮开局缺 paho-mqtt 等）：
  ```
  pytest pytest-asyncio paho-mqtt starlette==0.37.2 aiohttp httpx
  edge-tts apscheduler coverage onecode-pycg python-multipart pillow
  ```
- **`pycg` 必须装 `onecode-pycg`**
- **Python 3.10 vs 项目要求 3.11** —— `homesdk` 需 `PYTHONPATH=vendor/homesdk/src`
- bash 默认 60s 超时，**命令内 `timeout` 无效**，须传工具参数（毫秒）
- **建议为 `audit.py` 加备份** —— 本轮因一次 `s.replace()` 逻辑错误导致文件被破坏为 200MB 并截断，后续靠重建恢复
