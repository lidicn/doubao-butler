# doubao-butler 第三轮审计报告 · 持久化层与并发共享状态

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮方向（自主选定）**：**持久化层 + 并发共享状态**
  - 前两轮覆盖：启动/lifespan 链路、TTS 栈、SQLite、对话状态机、MQTT、工具链静态扫描
  - 本轮补位：JSON 落盘的**原子性与崩溃安全**、有状态组件的**锁覆盖范围**、异步路径中的**阻塞 DB 调用**、单例与硬编码路径
- **方法**：AST 定向扫描（异步函数内未包裹的阻塞调用）+ 逐文件通读 6 个 store + 跨文件调用链追踪
- **结论**：**1 个 P0（功能完全失效）+ 4 个 P1 + 3 个 P2**。本轮最重的一条是**设备别名学习功能只写不读**，属于"代码在跑、功能从未生效"的静默失效。

---

## 一、P0 级缺陷

### P0-5 设备别名学习「只写不读」——功能从未生效

- **位置**：`butler/core/aliases.py:66-75`（`match()`）、`butler/tools/registry.py:1048-1059`（唯一写入方）
- **现象**：`AliasStore.match()`（docstring 声称"快速匹配：用户说的话 → 找到对应的设备"）**在全代码库中无任何调用点**。
- **验证过程**：
  - 全仓 grep `alias` 关键字，排除 `aliases.py` 自身后，相关调用仅 3 处：
    - `registry.py:1057` → `store.learn(...)`（**写**）
    - `agent_routes.py:148` → `store.list_aliases()`（UI 展示）
    - `agent_routes.py:159` → `store.delete_alias()`（删）
  - `match()` 零调用 → 学到的映射**从不参与设备解析**
- **功能影响**：模块 docstring 承诺"下次快速命中"完全不成立。用户每次说"打开客厅灯"，系统都会重新走完整解析链路，别名库只增不减地膨胀（还占磁盘、拖慢 `_save()`），却**一次都不会被用上**。同时 UI 上能看到别名列表，造成"功能正常"的假象。
- **附带缺陷**：`match()` 即使被调用也不安全——它在**不持锁**的情况下遍历 `self.aliases`，而 `learn()` 持锁写入，并发时触发 `RuntimeError: dictionary changed size during iteration`。

**修复方案**（二选一，取决于设计意图）：

```python
# 方案 A：确实要用 —— 在设备解析入口接入 + 补锁
def match(self, text: str) -> dict | None:
    with _lock:                                  # ← 补锁，避免遍历期间被写
        for alias, info in list(self.aliases.items()):
            if alias in text and "conflicts" not in info:
                return {"entity_id": info["entity_id"],
                        "domain": info["domain"], "alias": alias}
    return None
```
然后在 `tools/registry.py` 的设备解析处（entity 解析失败/未命中时）插入：
```python
hit = get_alias_store().match(user_text)
if hit: eid = hit["entity_id"]
```

```python
# 方案 B：确认废弃 —— 删除 learn() 调用与定时任务，避免无意义磁盘膨胀
```
> 另外：`learn()` 中 `existing.setdefault("conflicts", []).append(...)` 会让该关键词**永久带 conflicts**，而 `match()` 的 `"conflicts" not in info` 判据使其**永久不可匹配**——即一次歧义就废掉一条别名。若采用方案 A，需改为带 TTL 的冲突衰减或按 count 择优选一。

---

## 二、P1 级缺陷

### P1-12 技能/触发器落盘：锁释放后才写盘，且 tmp 文件名固定 → 并发覆盖与损坏

- **位置**：`butler/skills/store.py:146-165`（`save`）、`butler/triggers/store.py:75-92`（`save`）
- **现象**：两处均为「`with self._lock:` 只包住内存索引更新，**写完就放锁**，随后的 `tmp.write_text()` + `tmp.replace()` 在锁外执行」。且 tmp 名为固定的 `path.with_suffix(".json.tmp")`。
- **后果**：
  - 两次并发保存（不同请求/不同 trigger）会写**同一个 tmp 文件** → 内容交错 → `replace` 后磁盘上是**损坏的 JSON**
  - 下次启动 `load()` 解析失败 → 该技能/触发器**静默消失**（用户视角："我明明设了，重启就没了"）
  - 同一 id 的并发保存还会产生**丢失更新**（后读到的旧内容覆盖新内容）
- **触发条件**：WebUI 上同时保存两个技能/触发器，或 API 与定时任务同时写同一文件

**修复方案**（扩大锁范围 + 唯一 tmp 名 + fsync）：

```python
import os, tempfile

def save(self, trigger: dict) -> None:
    normalized, err = validate_trigger(trigger)
    if err: raise ValueError("trigger 校验失败: " + err)
    tid = normalized["id"]
    self.dir.mkdir(parents=True, exist_ok=True)
    path = self.dir / f"{tid}.json"
    payload = json.dumps(normalized, ensure_ascii=False, indent=2)
    with self._lock:                                    # ← 索引与落盘在同一临界区
        self._index[tid] = normalized
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".tmp")   # ← 唯一名
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            os.unlink(tmp); raise
```

---

### P1-13 角色 / 设备配置：无锁 + 非原子写，崩溃即静默丢失用户配置

- **位置**：`butler/roles/store.py:175-180`（`_write`）、`butler/devices.py:171-178`（`save`）
- **现象**：
  - 两者**完全没有锁**（对比 skills/triggers 至少有 `_lock`）
  - 均为裸 `write_text()`（**先截断再写**）——写一半崩溃/断电即留下**截断的 JSON**
  - 无 `fsync`
- **后果**：下次启动 `load()` 的 `except` 捕获解析失败 → `roles` 回退 `DEFAULT_ROLES`、`devices` 回退 `_seed()` → **用户自定义的角色人设、绑定的音箱、设备表被静默重置为出厂值**，且只在 warning 日志留痕。
- **并发面**：`roles.upsert()` / `roles.delete()` 由 `role_routes.py` 的 async 处理函数调用，多个 HTTP 请求可并发进入（虽同一事件循环，但 `await` 点之间可交错）。

**修复方案**：与 P1-12 相同——加 `threading.RLock()` 覆盖"改内存 + 落盘"全程，改用 `tempfile.mkstemp` + `os.replace` + `fsync`。

> 建议抽一个公共 `_atomic_write_json(path, obj)` 工具函数，供 roles / devices / aliases / config 复用，避免 5 处各写一遍。

---

### P1-14 技能执行热路径中的同步 SQLite 查询阻塞事件循环

- **位置**：`butler/skills/runner.py:180` `repo.count_skill_runs_today()`（在 `async def run` 内）
- **现象**：**每一次技能执行**都要先做一次同步 SQLite 查询（daily limit 检查），未包裹 `to_thread`。锁争用时按 `busy_timeout=5000` 最坏阻塞 5 秒——**播报、MQTT、HTTP 全部停顿**。
- **背景数据**：AST 扫描确认全仓 **13 处**在 async 函数内直接调用 `repo.*` 而未包裹：
  - `skills/runner.py:180`（**热路径，最严重**）
  - `memory/feeder.py:86,109,115,121,130,148,152,155,220`（8 处，记忆投喂链路）
  - `decision/engine.py:138`（决策心跳）
  - `api/memory_routes.py:433`、`self_evolution.py:225`
  - 另有 28 处**已正确**使用 `to_thread`（如 `dedup.py`、`dialog.py:416`），说明项目已有正确范式，只是未统一
- **注意（对第一轮 P1-2 的修正）**：第一轮称"全仓库同步 SQLite"，实际**约一半已用 `to_thread` 包裹**。本轮精确定位到未包裹的 13 处，**修复范围比第一轮估计的小**，优先修 `runner.py:180` 与 `feeder.py` 这 9 处。

**修复方案**：

```python
today = await asyncio.to_thread(repo.count_skill_runs_today, skill_id, ok_only=True)
```
或在 store 层统一提供 async 版本 `arepo`，逐个替换。

---

### P1-15 `AliasStore` 落盘非原子 + 损坏即全量丢失

- **位置**：`butler/core/aliases.py:31-33`（`_save`）
- **现象**：`_save()` 裸 `write_text()` 全量覆盖 `device_aliases.json`；`_load()` 的 `except` 分支把解析失败直接降级为 `self.aliases = {}`。
- **后果**：一次写崩 → 下次启动**全部已学别名归零**，且无备份、无告警（仅 `logger.warning`）。叠加 P0-5（本就无人读取），实际损失有限，但修复 P0-5 启用 match 后，此缺陷会变得严重。
- **修复**：同 P1-13 的原子写；`_load` 失败时**保留坏文件副本**（重命名为 `*.corrupt-<ts>`）再降级，便于人工恢复。

---

## 三、P2 级隐患

| # | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| P2-11 | 24 个文件硬编码 `/app/data`（`aliases.py:13`、`deps.py:34`、`skills/versions.py`、`triggers/audit.py`、`newapi.py` 等） | 与 `Settings.data_dir`（可由 `DATA_DIR` 环境变量覆盖）**脱钩**。当前 compose 挂载正好是 `/app/data` 所以没炸，但一旦改 `DATA_DIR`，这 24 处仍写旧路径 → **数据分裂**（会话/别名/技能版本在一个盘，对话库在另一个盘） | 统一改 `Path(get_settings().data_dir) / "..."`；或删除 `data_dir` 可配置项、全部固定 `/app/data` |
| P2-12 | `aliases.py:59-62` | 关键词一旦产生 `conflicts` 就**永久不可匹配**（`match` 的判据排除带 conflicts 项），且 conflicts 列表无上限增长 | 冲突带 TTL / 按 count 择优选一，而非永久拉黑 |
| P2-13 | `aliases.py:66` `match()` | 遍历共享 dict **不持锁**，与持锁的 `learn()` 构成读写竞态 | 见 P0-5 方案 A |

---

## 四、已复核并**排除**的疑似问题

1. **会话持久化"重启掉线"** —— 一度怀疑 `_sessions_load()` 未被调用（该函数无外部引用，只有定义）。核查确认：`deps.py:136` 在**模块 import 期**执行 `_sessions_load()`，会话确实跨重启恢复。**不是 bug**。
2. **`config_routes.py` / `deps.py` 的 tmp + `os.replace`** —— 这两处**已正确实现原子写**（`config_routes.py:31` 注释明确写了 R2-08 fix），是本项目的正确范式，应作为 P1-12/P1-13 的修复模板。
3. **多 worker 导致 MQTT client_id 互踢** —— Dockerfile CMD 明确 `--workers 1`，**不会发生**。（但若运维改用 `--workers N` 或 gunicorn 多进程，会立刻触发；建议在 README 标注限制。）
4. **`Runtime` 单例** —— 类属性均为不可变 `None` 初值，`sse_subscribers` 用 `field(default_factory=set)` 正确，**无可变默认值共享问题**。
5. **`dedup.py`** —— 已正确使用 `asyncio.to_thread` 包裹两处 repo 调用，是本项目的正面范例。

---

## 五、三轮累计修复优先级（合并视图）

| 优先级 | 项 | 轮次 |
|---|---|---|
| **立即** | P0-1 `role` 未绑定 / P0-2 调度器作用域 / P0-3 SQLite 连接竞态 / P0-4 TTS 阻塞 160s | 一 |
| **立即** | NEW-1 `config_routes.logger` 未定义（配置损坏即锁死自我修复） | 二 |
| **立即** | **P0-5 别名只写不读**（决定启用还是删除该功能） | 三 |
| **本周** | NEW-2 HA notify 兜底丢失 / NEW-3 restart 不等待 / P1-1 pending 穿透 / P1-6~P1-11 | 一+二 |
| **本周** | **P1-12 技能/触发器落盘竞态**、**P1-13 角色/设备非原子写**（数据丢失类，先修这两个） | 三 |
| **本周** | **P1-14 runner.py:180 + feeder.py 9 处阻塞 DB 调用** | 三 |
| **排期** | NEW-4 SQL 参数化、NEW-5 `import sqlite3`、NEW-6 重复定义/遮蔽 | 二 |
| **排期** | P1-15 别名原子写、P2-11 硬编码路径、P2-12/13 别名冲突与加锁 | 三 |
| **排期** | P2 全量、47 处异常吞没补日志、安全项（B413/S105） | 一+二 |

### 值得先做的一件事

**抽一个 `_atomic_write_json(path, obj)` 工具函数**，一次性解决 P1-12 / P1-13 / P1-15，并顺便替掉 24 处硬编码路径（P2-11）。这三条其实是一个根因：**缺少统一的持久化原语**。

```python
# butler/store/atomic.py
import json, os, tempfile
from pathlib import Path

def atomic_write_json(path: Path, obj, *, indent=2, fsync=True) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, indent=indent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload); f.flush()
            if fsync: os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
```

---

## 六、本轮审计局限

1. **仅覆盖 6 个 store 与相关调用链**，未逐行通读 `triggers/engine.py`、`proactive/engine.py`、`modes/*` 等模块的内部状态管理——这些模块可能仍有同类竞态。
2. P1-12 的并发损坏**未做实机复现**（需构造并发写），结论基于代码路径推断：`tmp` 固定名 + 锁在写盘前释放，两者叠加成立。
3. P0-5 "match 无调用"基于 grep 全仓确认，但**不排除**存在动态分发（如 `getattr(store, name)`）绕过静态搜索的可能——已核查 `registry.py` 与 `agent_routes.py` 未见此类写法，但无法 100% 排除。
4. 未运行测试与集成验证（沙盒缺 MQTT / Home Assistant / doubao2api 依赖）。
5. 本报告**不重复**第一、二轮已列条目，三轮需合并阅读。
