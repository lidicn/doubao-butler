# doubao-butler 第二轮审计报告（工具链驱动 · 功能性/稳定性）

- **审计对象**：`lidicn/doubao-butler` @ `836df79`（同第一轮，main 分支公开快照）
- **本轮方法**：安装 GitHub 审计 Skill（`awesome-skills/code-review-skill`）+ 静态工具链，对第一轮的人工审计做**交叉验证与补漏**
- **工具链**：`ruff 0.16.10`、`bandit 1.9.4`、`vulture 2.16`、`pyflakes 4.0.1`、`pylint 4.1.1`
- **与第一轮的关系**：本轮**不重复**第一轮已列的 P0/P1，只报告**新增发现**与**对旧结论的验证结果**

---

## 一、本轮新增确认的缺陷

### 🔴 NEW-1 `config_routes.py:23` — `logger` 未定义，配置接口 500（必现于异常路径）

- **位置**：`butler/api/config_routes.py:18-30`（`_load_cfg()`）
- **现象**：`config.json` 损坏/非法 JSON 进入 `except` 分支时，执行 `logger.warning(...)` → `NameError: name 'logger' is not defined`。**本该被捕获的解析异常，反而升级成 500**，用户无法再打开配置页修复这份坏配置——**故障自我锁定**。
- **证据**：pyflakes `undefined name 'logger'`；文件头未 import 任何 logger。

**修复**：

```python
from butler.logging_setup import get_logger
logger = get_logger(__name__)   # 文件头补齐
```

> 这正是"错误处理路径自身有 bug"的典型：异常分支的代码从未被执行过，因此从未被发现。

---

### 🔴 NEW-2 `ha.py:311-331` — 丢失 `def` 行，整个 HA notify 兜底方法变成不可达死代码

- **位置**：`butler/integrations/ha.py:311-331`
- **现象**：`intelligent_speaker` 在 L308 已 `return`，其后 L313 起却挂着一段带 docstring 的完整方法体（"通过 HA notify 平台让小爱音箱播报文本"）——**`def` 声明行丢失**，代码被并入上一个函数体的 `return` 之后。
- **双重后果**：
  1. 该方法**永远不可达**（vulture 100% 置信：`unreachable code after 'try'`）；
  2. 函数体引用的 `message` / `entity_id` 在该作用域**均未定义**（pyflakes `undefined name 'message'`）——一旦被触碰即 `NameError`。
- **功能影响**：`_notify_message_inner` 目前只走 `intelligent_speaker` 一条路，**HTTP notify 兜底通道缺失**；小爱播报在 `intelligent_speaker` 失败时无降级。

**修复**（补回方法签名，并让它真正成为兜底）：

```python
    async def notify_message_http(self, message: str, entity_id: str | None = None) -> str:
        """通过 HA notify 平台让小爱音箱播报文本（intelligent_speaker 的兜底通道）。"""
        if not self.s.ha_token:
            return "HA token 未配置"
        data = {"message": message}
        ...
```
并在 `_notify_message_inner` 中接入：

```python
if not (res and isinstance(res, str) and res.startswith("ok")):
    return await self.notify_message_http(message, entity_id)   # 失败再兜底
return "ok"
```

---

### 🟠 NEW-3 `docker_tools.py:108` — `if False` 死代码，`restart()` 实际不等待

- **位置**：`butler/integrations/docker_tools.py:100-115`（`async def restart`）
- **现象**：`time.sleep(2) if False else None` —— 条件恒假（vulture 100% 置信 `unsatisfiable 'ternary'`），且 `time` 未 import。后果是**容器重启后没有任何等待**就返回。
- **功能影响**：调用方在 `restart()` 返回后立即 `ps`/`logs`/健康检查，会读到**旧状态或过渡态**，产生"重启失败/容器不存在"的误判。属真实的功能性竞态。
- **风险等级**：中（`time` 未定义是潜在 `NameError`，但因短路不会触发）

**修复**（改为正确的异步等待 + 状态轮询）：

```python
import asyncio
# 重启后等容器真正进入 running，最多等 15s
for _ in range(30):
    await asyncio.sleep(0.5)
    st = await self.inspect(container_id)
    if st.get("State", {}).get("Running"):
        return True
return False
```

---

### 🟠 NEW-4 多处 SQL 字符串拼接（`B608` × 9）— 注入面与健壮性风险

- **位置**：`store/repo.py:265,272,363,554`、`store/command_store.py:229`、`store/task_store.py:155`、`tools/schedule.py:199`、`triggers/audit.py:136,168`
- **说明**：bandit 标记 string-based query construction。**是否已构成可利用注入取决于字段来源**，需逐个确认；但至少是**脆弱写法**——含特殊字符（如成员名带引号）时会直接 `sqlite3.OperationalError`，表现为功能报错。
- **修复**：统一改参数化绑定。

```python
# 反例
c.execute(f"SELECT * FROM dialog_turns WHERE member='{member}'")
# 正例
c.execute("SELECT * FROM dialog_turns WHERE member=?", (member,))
```

> 建议优先排查 `audit.py:136/168` 与 `schedule.py:199`（这两处常带用户可控的筛选参数）。

---

### 🟡 NEW-5 `repo.py:145` — `sqlite3` 未导入（当前不炸，但属隐患）

- **位置**：`butler/store/repo.py:145` `_row_to_dict(r: sqlite3.Row)`
- **判定**：因文件有 `from __future__ import annotations`，注解为字符串**不会运行时求值**，故**当前不会抛 NameError**。但任何 `typing.get_type_hints()` / pydantic / 新版 SQLAlchemy 反射 / `mypy` 严格模式都会炸。
- **修复**：`import sqlite3` 补上（零风险，一行修复）。

---

### 🟡 NEW-6 重复导入与变量遮蔽（易引发后续误改）

| 位置 | 问题 | 影响 |
|---|---|---|
| `app.py:19` + `:51` | `audiobook_routes` **重复 import**（F811） | 无害但掩盖意图，易在合并时出错 |
| `api/skill_routes.py:979` | `skill_mock_test` 重复定义（F811），后者覆盖前者 | **同名函数被静默覆盖**，先定义的逻辑失效 |
| `core/scene_infer.py:65` | 循环变量 `re` 遮蔽顶层 `import re` | 该函数内后续 `re.xxx` 全部 `AttributeError` |

**修复**：删除重复导入；`skill_mock_test` 重命名其中一个；`scene_infer.py` 循环变量改为 `rule`/`item`。

---

## 二、对第一轮结论的工具链验证结果

| 第一轮结论 | 工具验证 | 判定 |
|---|---|---|
| P1-3 `create_task` 返回值被丢弃（16 处） | **ruff `RUF006` 命中 17 处 asyncio-dangling-task** | ✅ 证实，且数量略多于人工统计 |
| P2-9 异常吞没（30+ 处） | **ruff `S110` try-except-pass 47 处 + `S112` 12 处 + `SIM105` 28 处** | ✅ 证实，实际规模更大 |
| P1-2 同步阻塞调用污染事件循环 | **ruff `ASYNC230` 4 处（open/Path）+ `ASYNC240` 7 处** | ✅ 证实，且**不止 SQLite**——文件 IO 同样在阻塞事件循环 |
| P0-1 `role` 未绑定 | pyflakes 未报（因其为条件性运行时错误，非静态未定义） | ✅ 仍需按第一轮方案修 |
| P0-2 调度器作用域 | 静态工具不报（逻辑缺陷） | ✅ 仍需按第一轮方案修 |
| P0-3 SQLite 连接竞态 | 静态工具不报（并发缺陷） | ✅ 仍需按第一轮方案修 |

> **新增认知**：`ASYNC230/240` 说明阻塞面比第一轮判断的更广——除 SQLite 外，`open()`、`Path.read_text()`、`Path.exists()` 等文件操作也在 async 函数中同步执行（典型如 TTS 音频读写、配置落盘）。修复 P1-2 时应一并纳入 `asyncio.to_thread`。

---

## 三、其余扫描数据（供排期参考）

| 工具 / 规则 | 命中 | 说明 |
|---|---|---|
| `ruff` 总计（含风格） | ~7,500 | 绝大多数为 `RUF001-003` 中文全角字符（本项目为中文界面，属**预期噪声**，勿改） |
| `E501` 行过长 | 94 | 风格问题 |
| `F401` 未使用导入 | 96 | 清理项 |
| `S324` MD5/SHA1 | 11 | **均为缓存 key / 去重指纹，非密码学用途**，可加 `usedforsecurity=False` 消除告警 |
| `B104` 绑定 0.0.0.0 | 2 (`config.py:74,209`) | 容器内监听属预期，但暴露端口需确认未对公网开放 |
| `S105/B413` | bark.py 硬编码密码 + pyCrypto | 安全项，与本轮功能性主题无关，建议单独排期 |
| `W292/W293` 换行/空白 | 66 | 风格 |
| **vulture 死代码** | **8** | 其中 `ha.py:313`、`docker_tools.py:108` 已上升为本轮 NEW-2 / NEW-3 |

---

## 四、本轮审计局限（需诚实说明）

1. **`pylint` 与部分 `ruff` 细分规则执行超时**，未能取得完整 `E` 级错误清单；`RUF006` / `ASYNC230/240` 只拿到**统计数**，未逐条定位——上表中"17 处""4 处""7 处"为 ruff `--statistics` 输出，具体行号需补跑一次完整扫描。
2. `B608` 的 9 处 **未逐条确认是否为真正可利用的注入**（需追溯参数来源），目前仅按"脆弱写法"定级。
3. `skill_routes.py:979` 的重复定义，`scene_infer.py:65` 的变量遮蔽，仅依据 pyflakes，**未读取上下文确认实际运行路径**。
4. 未运行单元测试与集成验证（沙盒缺 MQTT / Home Assistant / doubao2api 依赖）。
5. 本轮**未重复审第一轮已覆盖的模块**，两轮报告需合并阅读。

---

## 五、合并后的修复优先级（两轮汇总）

| 优先级 | 项 | 来源 |
|---|---|---|
| **立即** | P0-1 `role` 未绑定、P0-2 调度器作用域、P0-3 SQLite 连接竞态、P0-4 TTS 阻塞 160s | 一轮 |
| **立即** | **NEW-1 `config_routes.logger`**（异常路径必 500，且锁死自我修复能力） | 二轮 |
| **本周** | NEW-2 HA notify 兜底方法丢失、NEW-3 `restart()` 不等待、P1-1 pending 穿透、P1-6~P1-11 | 一轮+二轮 |
| **本周** | **NEW-4 SQL 参数化**（先排查 audit/schedule 两处用户可控参数） | 二轮 |
| **排期** | NEW-5 `import sqlite3`、NEW-6 重复定义/遮蔽、P1-2 DB+文件 IO 异步化（含 ASYNC230/240） | 二轮 |
| **排期** | P2 全量、47 处异常吞没补日志、安全项（B413/S105） | 一轮+二轮 |
