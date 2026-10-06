# doubao-butler 第十五轮审计报告 · identity 阶段 + 深挖 memory/roles 一致性

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮主线**：① 新增 `identity` 阶段（固化第十四轮 P1-42）② 深挖前十四轮从未通读的 `memory/`（626 行）与角色白名单一致性
- **结论**：**1 个 P1（角色白名单三份副本 + 种子表缺 `gu_anheng`）**，无新增 P0

---

## 第一部分：工作流迭代（v1.7 → v1.8）

### 1.1 新增 `identity` 阶段 —— 判定逻辑中的硬编码身份

第十四轮结尾我承诺把 P1-42 固化为扫描。本轮实现，三层判据：

| 层 | 内容 |
|---|---|
| ① token 采集 | 从种子数据/配置收割候选身份：`members: [...]`、`MemberConfig(name=)`、`member: "x"`、`person.*` 后缀、`room=` 取值 |
| ② 逻辑行筛选 | 只保留含 `.get(` / `==` / `in [` / `entity_id=` / `users[` 的行 |
| ③ **high/low 分级** | **high** = 身份被当**查表键**（`.get("X")` / `== "X"` / `entity_id=`）→ 取不到即静默失效<br>**low** = 身份作**缺省值**（`or "X"`）→ 有兜底 |

**结果：12 处（high 4 / low 8）**，4 处 high **全部命中第十四轮人工发现的 P1-42 三处**：

```
[high] butler/modes/auto_switch.py:71      lidicn
[high] butler/morning/routine.py:94        lidicn
[high] butler/timeseries/anomaly.py:155    lidicn
[high] butler/audiobook/manager.py:431     客厅
```

第 4 处 `audiobook/manager.py:431` 是新增：`_default_device()` 用 `== "客厅"` 硬匹配默认播放设备——若部署环境无"客厅"或房间命名不同，有声书**返回空设备 ID**。判据与 P1-42 同构，应一并改。

### 1.2 噪声治理（初版 114 处 → 12 处）

初版把 `room`、`confidence`、`face_detected`、`last_seen`、`{{event.member}}`、`...` 等**字段名**误收为成员名，产生 114 处噪声。加 `noise` 集合 + 模板/纯数字过滤后降到 12。

### 1.3 v1.8 实测数据

| 阶段 | 结果 |
|---|---|
| tests | 7 failed / 586 passed / 10 skipped / 1 xfailed |
| **identity** | **12 处（high 4 / low 8）** |
| lifecycle | 11 处 high（模块级 10 / 实例属性 1） |
| dupfiles | 5 个孤儿模块（连续五轮稳定复现 P0-15） |
| errpath | 2 处（1 真实：`config_routes.py:23`） |
| dupimpl | 24 对 |
| mqtt / httpcontract / secrets | orphan_pub 6 / 16 处 / 8 处 high |

---

## 第二部分：第十五轮审计发现

### P1-43 六角色白名单有**三份独立副本**，且角色种子表**缺 `gu_anheng`** —— 安防专员静默降级为豆包管家

- **位置**：
  - `butler/memory/feeder.py:22` —— `FEEDABLE_ROLES`
  - `butler/memory/extractor.py:265` —— 内联元组
  - `butler/api/memory_routes.py:329` —— 内联元组
  - `butler/roles/store.py:70` —— `DEFAULT_ROLES`（**只有 5 个**）

#### 四份清单对比（实锤）

```
=== load() 后播种的角色 ===
   ['butler', 'caesar', 'jarvis', 'luna', 'xiaoyue']
   DEFAULT_ROLES 定义数: 5

=== 记忆投喂白名单 vs 角色表 ===
  butler       角色表=✅
  jarvis       角色表=✅
  caesar       角色表=✅
  luna         角色表=✅
  gu_anheng    角色表=❌ 缺失      ← 唯一不一致
  xiaoyue      角色表=✅

=== resolve_for_skill(skill.role='gu_anheng') ===
  → butler / 豆包管家
  → 安防专员技能会静默降级为【豆包管家】的音色与前缀
```

#### 而 `gu_anheng` 在另外四处都被当作**合法角色**

| 位置 | 用法 |
|---|---|
| `feeder.py:22` | 记忆投喂白名单成员 |
| `extractor.py:265` | 事实归属校验白名单 |
| `memory_routes.py:329` | `/api/memory/facts/{id}/role` 入参校验 |
| `agent_collab.py:360` | 内置 agent 注册（"顾安恒 · 安防专员"） |
| `doubao_webhook.py:479/556/705` | 硬编码兜底 CID → `role_id="gu_anheng"`、专属 system prompt、摄像头指令 |

**唯独角色表没有它。**

#### 后果链（三层，全部静默）

1. **技能播报降级**：任何 `role: "gu_anheng"` 的技能，`resolve_for_skill()` 兜底返回 `butler` → 安防告警用**豆包管家的音色与【豆包管家】前缀**播出。用户听到"管家在报安防"，无法区分
2. **音色表同样缺失**：`tts_routes.py` 的 `VOICE_MAP` 只有 `butler/emily/jarvis/kevin/lidicn/xiaoai/...`，**`caesar`、`luna`、`xiaoyue`、`gu_anheng` 四个白名单角色均无音色**
3. **WebUI 无法创建**：角色不在种子表中，WebUI「人格」页看不到顾安恒，用户无从补建（`upsert()` 可用但需手工构造）

#### 与 P1-42 的同构性

| | P1-42 | P1-43 |
|---|---|---|
| 形态 | 同一概念多份真源 | 同一概念多份真源 |
| 数量 | 成员名散在 3 处逻辑 + 无配置项 | 角色白名单 3 份内联副本 + 种子表 |
| 失效 | 静默（空 dict / 恒 False） | 静默（兜底 butler） |
| 对作者 | 无症状 | 无症状（顾安恒走 webhook 硬编码 CID，不经角色表） |

> 注意：顾安恒在作者的现网是"能用"的——`doubao_webhook.py` 用硬编码 CID `38440360274498562` 直接指定 `role_id`，**绕过了角色表**。所以缺失从未暴露。这正是"多真源"的典型掩护：一条路径绕过损坏的源，问题就永远不显现。

**修复**：
```python
# 1) 单一真源
# butler/roles/store.py
FEEDABLE_ROLES = ["butler", "jarvis", "caesar", "luna", "gu_anheng", "xiaoyue"]
# feeder.py / extractor.py / memory_routes.py 改为 import 该常量

# 2) DEFAULT_ROLES 补 gu_anheng（与 agent_collab.py:360 对齐，含 name/性别/音色）

# 3) VOICE_MAP 补 caesar / luna / xiaoyue / gu_anheng
```

---

### 本轮无新增 P0

`identity` 的 4 处 high 中 3 处是第十四轮已报 P1-42，1 处（`audiobook`）为同构新增，定 P1 附带。

---

## 第三部分：本轮**排除**的误报

| 疑似 | 核查结论 |
|---|---|
| `identity` 初版 114 处 | ❌ 全部为字段名噪声（`room`/`confidence`/`face_detected`/模板变量），已加 noise 过滤降至 12 |
| 8 处 `客厅` low（缺省值） | ⚪ 属 `payload.get("room") or "客厅"` 形式的**兜底默认值**，非查表键，定 low。虽仍环境相关，但有 fallback，不静默失效 |
| `person.homeassistant` 是身份 | ❌ 非人名，已排除 |
| `gu_anheng` 完全没实现 | ❌ 不成立——webhook 路径通过硬编码 CID 绕过了角色表，功能可用。缺陷是**角色表缺失导致技能侧降级**，不是整体不可用 |

---

## 第四部分：工作流现状与局限

### v1.8 已解决

| v1.7 短板 | v1.8 状态 |
|---|---|
| P1-42 靠人工 grep 发现 | ✅ `identity` 阶段固化，自动命中全部 3 处 + 1 处新增 |
| audit.py 无备份 | ✅ 已加 `audit.py.bak` |
| `lifecycle` 实例属性漏报 | ✅ 双通道（v1.7，本轮稳定复现） |

### 仍存在的短板（诚实）

1. **`identity` 的 token 采集依赖启发式正则**（`members: [...]` / `MemberConfig(name=)`）——换项目需调整
2. **`dupimpl` 仍未检出 P1-38 那一对** —— 同概念不同实现风格，连续三轮未解决
3. **`errpath` 调参可能过拟合** —— 未在第二个仓库验证
4. **runtime 覆盖 22.07%** —— 371 个方法未执行
5. **未通读**：`mcp/server.py`（1006 行）、`agent_collab.py`、`integrations/ilink/`（3 文件）、`decision/`、`notifier/`

---

## 第五部分：十五轮累计 · 经执行验证的结论

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
| 十四 | V26 | 三处 critical 模块硬编码成员名 `lidicn`，绕过可配置成员系统 |
| **十五** | **V27** | **六角色白名单三份副本；种子表缺 `gu_anheng` → 安防技能静默降级为豆包管家；音色表缺 4 个角色** |
| **十五** | **V28** | **`audiobook._default_device()` 硬编码 `== "客厅"`（identity 新增 high）** |

---

## 第六部分：下一步建议

### 修复优先级

| 优先级 | 项 | 成本 |
|---|---|---|
| **立刻** | P0-15 删 5 个孤儿文件（工具连续五轮自动确认） | 极低 |
| **立刻** | P0-14 补状态码检查（`integrations/deskpilot.py` + `tools/desk_pilot.py`） | 低 |
| **本周** | **P1-43 角色白名单单一真源 + 补 `gu_anheng` 种子 + 补 4 个音色** | 中 |
| 本周 | P1-42 引入 `primary_member` 配置（含 `audiobook` 的 `客厅`） | 中 |
| 本周 | P1-40 收敛 SQLite 连接工厂（18 处 → 1 处） | 中 |
| **本周** | P1-41 关停时 cancel `_presence_poll_task` | 极低（3 行） |
| 排期 | P1-32/35 清理 8 处 high 硬编码内网地址 | 中 |

### 工作流下一步

1. **新增"多真源"检测** —— P1-42（成员名 3 处）、P1-43（角色白名单 3 份 + 种子表）、P1-38（输出分发 2 份）、P1-21（DeskPilot 3 份客户端）**是同一个根因**。可扫描"跨文件重复出现的字面量序列（≥3 元素、≥2 处）"自动发现
2. **补扫未通读区** —— `mcp/server.py`、`agent_collab.py`、`ilink/`、`decision/`、`notifier/`
3. **`dupimpl` 加"常量簇"信号** —— 连续三轮未解决 P1-38 那类同概念重复
4. **runtime 覆盖提到 40%+**

---

## 附：一个贯穿十五轮的模式

累计 28 条结论中，**至少 8 条根因相同：同一概念存在多份真源**。

| 结论 | 真源份数 |
|---|---|
| V21 三份 DeskPilot 客户端 | 3 |
| V22 两份输出分发逻辑（→ `sdd/notify` 漂移） | 2 |
| V23 九份 `get_conn` | 9 |
| V24 十八处 `sqlite3.connect` | 18 |
| V26 成员名散在 3 处 + 无配置项 | 3+ |
| **V27 角色白名单三份内联 + 种子表** | **4** |

共同特征：**每份都不报错，只是彼此不一致**；且**作者对现网路径可用，所以永远无症状**。静态扫描能发现零件坏了，但发现不了"同一个东西被抄了四份且其中一份少抄了一个"——这需要一个专门的"多真源"检测器。这是我下一步最想做的。

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
- `audit.py.bak` 已就位，修改前先备份
