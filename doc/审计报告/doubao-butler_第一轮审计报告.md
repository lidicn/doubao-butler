# doubao-butler 审计报告 · 第一轮

- **审计对象**：`lidicn/doubao-butler`（NAS Docker 家庭 AI 管家决策大脑）
- **审计工具链**：`lidicn/ADM-auditkit`（经 AutoForge 二十轮演进的形态）
- **轮次**：`round-001`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-001/`
- **报告日期**：2026-10-07

---

## 一句话结论

**1305 条机器命中，人工确证 3 项，其中 2 项高危（D1/D2）且形态与 AutoForge 的 F8/F10 完全同族——「读侧静默返空 + 写侧全量覆盖」。真正有意思的是：同一个代码库里 `fast_routes.py` 把这件事做对了，源码注释里明确写着"⛔ 在这里 `_save()`：盖回去＝抹掉现场"。**

---

## 一、确证缺陷

### D1【P0·high】config.json：改一个字段，人格与成员全没了

`butler/api/config_routes.py`：

```python
def _load_cfg():
    try:    return json.loads(...)
    except Exception:
        logger.error(...)       # 有日志，但不阻断
        return {}               # ← 读侧静默返空

def _save_cfg(cfg):
    ...  # tmp + os.replace 全量覆盖
```

**实测复现**：

| 步骤 | 结果 |
|---|---|
| 播种 `{persona, members}` | 盘上有 persona + members |
| 损坏 config.json | — |
| `_load_cfg()` | `{}`（记 error 日志，不阻断） |
| 改 `greeting_template` → `_save_cfg` | **persona / members 永久丢失，只剩 greeting_template** |

config.json 存**人格、成员称呼、免打扰时段、TTS 设置**。用户视角是："我只改了一句招呼语，怎么人格和成员全没了。"

触发条件平常：磁盘满、断电、手工编辑都能造成文件损坏，不需要攻击。

### D2【P0·high】role_state.json：双写手 + 读失败返空，跨端续聊会话全丢

`butler/roles/state.py` 是**最标准的 DO-01 形态**：

```python
def load(self):
    try:    self._m = json.loads(...) or {}
    except Exception as e:
        logger.warning("role_state.json parse failed: %s", e)
        self._m = {}                              # ← 读侧返空

def _save(self):
    write_json_atomic(self.path, self._m)         # ← 全量覆盖
```

**实测复现**：播种 sess-A / sess-B 两条会话绑定 → 损坏 → `load()` 后 `self._m = {}` → `set("sess-C")` → 盘上**只剩 sess-C，conv-A / conv-B 均不在**。

后果：该文件存各角色的 `conversation_id`，用于**手机端跨端续聊**（README 的 R8 特性）。丢失 ⇒ 会话串味或重开。

**加剧因素**：这个文件有**两个写手**（`roles/state.py` 和 `memory/feeder.py`）。源码注释自己承认：

> 同一份 role_state.json 的第二个写手（第一个是 memory/feeder.py）：就地截断写会盖坏对方刚绑好的会话

**开发者防住了"截断写"，没防住"读失败"。** 这和 AutoForge 的 F10 是同一句话——那边的 BUG-20 注释说"防住了不重读，没防住重读失败"。

### D3【P3·low】devices.json：同族轻症

读失败退**出厂 seed**（非空兜底），且 `if not self.path.exists()` 才 save ⇒ **不自动覆盖**，坏文件仍在、可人工恢复。下次 WebUI 编辑设备时才把 seed 写回。

---

## 二、最有价值的发现：同一个库里，一个做对一个做错

`butler/core/fast_routes.py`：

```python
except Exception as e:
    # 表行 94 T-04：回退空列表＝快速路由永久静默失效 ⇒ 兜底成内置规则。
    # ⛔ 在这里 _save()：盖回去＝抹掉现场，人工无从判断丢了哪些用户自加规则。
    logger.error("fast_routes.json unreadable, fallback to builtin rules: %s", e)
    self.routes = self._builtin()
```

| 文件 | 读失败行为 | 是否覆盖 | 结论 |
|---|---|---|---|
| `config.json` | 返 `{}` | **是**（下次改动） | ✗ D1 |
| `role_state.json` | 返 `{}` | **是**（下次 set） | ✗ D2 |
| `fast_routes.json` | 兜底内置规则 | **否（刻意不写回）** | ✓ 做对了 |
| `devices.json` | 兜底 seed | 否 | △ D3 轻症 |

**同一项目、同一模式、同一批开发者、四个文件四种处理。** 这说明问题不在能力，而在**缺少统一的"读失败语义"约定**——每个文件各自临场发挥。

给开发者的建议优先级也因此变了：**不要逐个修这三个文件，先定一条「读不出来 ≠ 没有」的约定**，再把四个文件统一过去。

---

## 三、工作流迭代

### W49：外部工具分析器的白名单是照 AutoForge 定制的

`external_tool_defects.py` 的 `KEEP_TESTS` 白名单不含 B608/B104/B107/B413/B603/B311，且 `conf != HIGH` 一律丢弃 ⇒ **bandit 手工跑出 57 条，经分析器只剩 0 条**。

修：扩充白名单、conf 门槛放宽到 MEDIUM+、LOW severity 不再一律丢弃。修后产出 **162 条**。

### W50：写盘 API 别名表换项目就失效（本轮最关键）

`destructive_overwrite_defects.py` 的 `WRITE_CALLS = {atomic_write_text, write_text, ...}` 全是 AutoForge 用过的 API。本项目用**自定义的 `write_json_atomic`** ⇒ **DO-01..04 全部 0 命中**，表现为"本项目没有破坏性覆盖"。

而 D2 就是最标准的 DO-01 形态，实测确证存在。

修：加入项目自定义 API + **启发式兜底**（以 write/save/dump/persist/store/flush 起头且非日志调用）。修后 **0 → 4 条命中**，其中 `feeder.py:66` 正是 D2 的同源读侧。

> **这是同一个坑换项目再踩**。第四轮是 `_atomic_write_text` 漏登记，那次让新写的 DO-02 规则"安静地什么都不报"。枚举式别名表永远追不上每个项目的自定义封装——这也是本次提交给上游的判据增补建议的由来。

---

## 四、审计结果

| 指标 | 数值 |
|---|---|
| 原始命中 | **1305**（high 71 / medium 835 / low 399） |
| 人工确证缺陷 | **3**（high 2 / low 1） |
| 外部工具（W49 修后） | 162 条 |
| DO 规则（W50 修后） | 0 → **4 条** |

Top 规则分布：`AF-AST-MIXED-RETURN` 125 · `IN-02-unsafe-cast` 119 · `RSC-03-unbounded-read` 95 · `AFS-01-silent-failure` 74 · `API-06-same-name-diff-sig` 73 · `IN-03-path-join` 71 · `ERRH-02-fake-success` 62 · `TIME-02-wall-timeout` 62

1305 条中绝大多数**尚未分诊**，本轮人力集中在确证 D1/D2/D3 与修复两个失效的分析器。

---

## 五、工具失效/不适用清单（如实说明）

| 分析器 | 状态 | 说明 |
|---|---|---|
| `destructive_overwrite_defects` | **已修（W50）** | 别名表不含 `write_json_atomic`；修后 4 条 |
| `external_tool_defects` | **已修（W49）** | 白名单照 AutoForge 定制；修后 162 条 |
| `outbound_guard_defects` | **失效** | 规则只认 `urllib.request.urlopen`，本项目用 **httpx** ⇒ 出站护栏面完全没扫 |
| `resource_defects` | **不适用** | 只关心裸 `open()`，不覆盖 MQTT 长连接 / SQLite 连接 / httpx.AsyncClient |
| `semgrep` | **不可用** | 安装失败（`OSError [Errno 27] File too large`），包可 import 无 CLI |

---

## 六、已判假阳性

- **B608 × 8（SQL 注入告警）**：逐处核对，`command_store.py:229`、`repo.py:298/305/396/587`、`task_store.py:155`、`tools/schedule.py:199` 拼进 SQL 的**列名全是代码内硬编码字面量**，值全部走 params 占位符 ⇒ 判假阳性。
  留一条风险提示：`tools/schedule.py` 的 sets 来自 args 字典键（当前键写死安全），**若将来允许外部传列名即变真注入**。
- **B324 × 116（不安全哈希）**：全部用于**指纹/去重/缓存键**（`bus/inbox.py:176/277`、`core/dedup.py:48`、`doubao.py:84`、`audiobook/manager.py:95`），非安全用途 ⇒ 判假阳性。

---

## 七、一条值得留意但未立项的观察

`butler/bus/inbox.py:277`：

```python
key = (channel, source, hashlib.sha1(text.encode("utf-8")).hexdigest()[:12])
```

事件去重键用 SHA1 **截断到 12 个十六进制字符（48 bit）**。10 万条文本量级下碰撞概率约 1.8%——碰撞会导致**不同的 MQTT 消息被误判为重复而丢弃**。量级不大，但这是"防重复"功能的核心键，建议在消息量上去后复核。

---

## 八、下一轮方向

1. **分诊 1305 条**（high 71 优先），当前绝大多数未处理
2. **补 httpx 出站护栏规则**——出站面完全没扫，而项目要连 HA / Bark / doubao2api / memory-agent
3. **按项目风险面定向核查**：MQTT 长连接重连、TTS 三级降级链路、SQLite WAL 并发、bigram 7 天窗口、免打扰时段时区、HA/Bark 无超时、人格热改竞态
4. **验证 README 的设计契约**：「全局异常兜底绝不崩溃退出」「`/api/health` 不因下游不可用而失败」——这两条是显式承诺，值得专门实测
5. **资源类规则扩展**：覆盖 MQTT / SQLite / httpx 连接池（当前不适用）

---

## 九、未覆盖

- **依赖 CVE 面未扫**（pip-audit 网络受限）
- **semgrep 不可用**
- `git clone` 返回 403，源码经 codeload tarball 获取，无 git 历史
