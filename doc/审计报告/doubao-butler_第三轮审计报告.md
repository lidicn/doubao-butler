# doubao-butler 审计报告 · 第三轮

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-003`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-003/`
- **报告日期**：2026-10-07

---

## 一句话结论

**DO（破坏性覆盖）规则修了五处盲区后，命中从 4 条涨到 10 条、自动实测确认 5 处真实数据丢失，并新增确证 D7/D8/D9。核心教训是：本轮最贵的错误不在被测代码，而在测试基准——门禁报"正确写法样本上复现了 3 处数据丢失"，我反复怀疑规则太宽，最后发现是那个标着"正确写法"的样本自己复刻了缺陷写法。**

---

## 一、确证缺陷（本轮新增）

### D8【P0·high】cron 密钥库：三条凭据，一次写入全没

`butler/core/cron_task.py`：

```python
except Exception as e:
    logger.warning("failed to load secrets: %s", e)
    self._secrets = {}                      # ← 读侧清零

def _save_secrets(self):
    self._secrets_file.write_text(json.dumps(self._secrets, ...))   # ← 全量覆盖
```

**实测**：

| 步骤 | 结果 |
|---|---|
| 播种 `ha` / `bark` / `nasa` 三条凭据 | 盘上有 3 条 |
| 损坏 secrets.json | `JSONDecodeError` |
| 读侧 | 降级为 `{}`（仅 warning） |
| 写入一条新密钥 `new` | **盘上只剩 `{"new": "v"}`，原三条全无** |

**后果**：secrets.json 是定时任务用的 Secret Vault，存 HA / Bark / 第三方 API 凭据。损坏后任一写操作即抹掉全部凭据 ⇒ 所有定时任务集体失效，且必须逐个重新录入（凭据通常不可重新生成，只能翻原始配置找回来）。

### D9【P2·medium】cron 执行日志裁剪时全量重写

同一文件的第二条路径：读失败后 `_execution_log = []`，而 `_append_log` 在超过 `_max_log_size` 时会调 `_rewrite_logs()`——**用空内存快照覆盖整个 JSONL**。

**实测**：播种 6 条 `old-*` → 读侧失败 → 追加 6 条触发裁剪 → 盘上 `old-*` 计数 **0**。

触发条件比 D8 窄（需累计到上限），定 medium。但执行日志是排查定时任务失败的**唯一依据**。

### D7【P1·high】iLink 会话（上一轮末确证，本轮归档）

`ilink/client.py`：`_load_session` 失败清零 + `_save_session` 全量覆盖 ⇒ Bot 登录态丢失。额外问题：`_save_session` 用的是**就地 `write_text`**，而项目里其他地方已改用原子写——崩溃会留半截 JSON。

---

## 二、工作流迭代：DO 规则五处盲区

上一轮 DO 规则只有 4 条命中，而 D4（别名库清零，已人工实测确证）、D7、D8 全部 0 命中。逐个排查后发现五处独立盲区：

| 编号 | 盲区 | 后果 |
|---|---|---|
| **W54** | 写侧把落盘**委托给 helper**（`learn()` 调 `self._save()`，`dumps` 在 `_save` 里） | `_dumps_field(learn)` 为空 ⇒ 整条链跳过 |
| **W55** | `_mutated_fields` 只认下标赋值与增量赋值，不认**整体赋值** `self.aliases = {}` | 读侧清零字段这个主形态被漏掉 |
| **W56** | void 读侧只认 `except: return`，不认 `except: self.X = {}` | 新增 DO-05 子形态（44 条命中） |
| **W57** | `_dumps_field` 只在载荷里找 `values()/items()/list(/dict(/for`，而 `json.dumps(self.aliases)` **整个字段直接序列化**反而没这些痕迹 | 最直白的全量覆盖写法被判"不是全量覆盖" |
| **W58** | 同类候选去重时按行号排序，把 `_load()`（只播种一次）排到了 `match()`（每次命中都写）前面 | D6 真正的破坏路径被去重掉 |

**修后**：DO 命中 **4 → 10**（全量扫描口径），自动 PoC **5 处 data_lost**（别名库、快速路由、iLink 会话、角色状态 ×2）。

---

## 三、本轮最贵的一条教训：测试基准自己写错了

改完 W54–W57 后门禁报：

> `[FAIL] 状态 PoC 门禁：clean 样本复现了 3 处数据丢失—— 假阳性`

**我第一反应是规则变宽了导致误报**，于是反复收紧判据（W59  attempts：委托写去重 → pass1 预计算 → `self_methods` 过滤），把 3 条压到 1 条，门禁仍报。

重新读样本源码才发现问题不在规则：

```python
# selftest/cases/clean/sample.py —— 标着"正确写法"的类
class CodeStore:
    def _load(self):
        if self.path.is_file():
            try:    self._codes = json.loads(...)
            except (OSError, ValueError):
                return          # ← 与 dirty 侧 D-52 缺陷写法逐字相同
```

**clean 样本里这个"正面对照"类，读失败处理方式和缺陷写法一模一样。** 规则修严之后正确地命中了它，PoC 也确实测出了丢失。所以不是规则误报，是**基准自己复刻了缺陷**。

把它改成 fail-closed（不吞异常）后，门禁恢复全绿。

**推论**：规则变严之后冒出来的新命中，先别急着怀疑规则——**有可能是旧的测试基准一直在包庇错误写法**。这个样本在此前十几轮里一直是绿的，因为它依赖的是规则的漏报。

---

## 四、环境缺口

`homesdk`（项目私有依赖，wheel 随仓库附带在 `vendor/`）要求 Python ≥3.11，沙箱是 3.10 ⇒ `pip install` 拒绝安装 ⇒ `butler/core/cron_task.py` 在 import 阶段就失败 ⇒ **D8/D9 一开始根本测不到**。

绕过方式：解包 wheel 到 `/tmp/hsdk`，手动 `sys.path.insert`。已作为环境缺口记录，不属缺陷。

这也是本项目反复出现的模式：**"测不到"和"没问题"在报表里长得一模一样**。本轮 6 个 `unavailable` 目标里至少有一部分就是这个成因，我没有把它们计入"已验证无缺陷"。

---

## 五、审计结果

| 指标 | 数值 |
|---|---|
| DO 全量扫描命中 | **10**（上一轮 4） |
| 自动状态 PoC | 10 目标 → **5 data_lost / 5 unavailable** |
| 人工补测确证 | **D8 / D9** |
| 台账累计 | **D1–D9**（high 7 / medium 1 / low 1） |
| 门禁 | ok=True，analyzers=33 |

---

## 六、按根因合并：只剩两族

| 根因 | 缺陷 |
|---|---|
| **读路径的失败被当成"没有数据"** | D1 D2 D3 D4 D6 D7 D8 D9（8 项） |
| 配置面不一致 | D5（双路径分叉） |

**8/9 项收敛到同一句话。** 建议的修法也因此不是"修这 9 个点"，而是**先定一条统一的"读不出来 ≠ 没有"约定**（poison + quarantine + barrier），再把九个文件统一过去——这正是 AutoForge 那边 `af_stateguard` 做的事。

---

## 七、未覆盖 / 如实说明

- **6 个 PoC 目标 `unavailable`**：`llm.py`、`newapi.py`、`memory/feeder.py`、`cron_task` ×3 的日志路径——探针构造不出实例或种不出状态，**不是"没问题"**
- **依赖 CVE 面未扫**：pip-audit 网络受限
- **semgrep 不可用**：安装失败（`OSError [Errno 27] File too large`）
- **1339 条通用命中仍未分诊**（high 73）
- **D5 未端到端实测**：环境缺 starlette，为静态取证结论

---

## 八、下一轮方向

1. **分诊 1339 条**（high 73），特别是 `AFS-01-silent-failure` 20 条与 `RSC-02-recursion-no-budget` 7 条
2. **补 homesdk 到环境**，解掉 cron_task 相关的 3 个 unavailable
3. **按 D1–D9 的根因写补丁副本**（`db-patched`）并跑回归，把"确证"推进到"修复并验证"
4. **验证 README 两条设计契约**：「绝不崩溃退出」「`/api/health` 不因下游不可用而失败」
5. **第一轮遗留的 7 个风险面**：MQTT 重连、TTS 三级降级、SQLite WAL 并发、bigram 7 天窗口、DND 时区、HA/Bark 超时、persona 热重载竞态
