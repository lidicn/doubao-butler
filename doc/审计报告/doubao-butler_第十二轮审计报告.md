# doubao-butler 审计报告 · 第十二轮

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-012`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-012/`
- **报告日期**：2026-10-07

---

## 一句话结论

**AFS-01 收敛完成：66 条命中 → 3 条 high，全部是真缺陷且全部对应已确证项（D1、D18×2），medium 归零，10 条假阳性进抑制清单。收敛靠的不是调参，而是给规则补上三类语义判据：数据流（W73）、falsy 早退（W74）、否定式使用（W74b）。**

---

## 一、AFS-01 的收敛曲线

| 阶段 | high | medium | low | 说明 |
|---|---|---|---|---|
| 第十轮末 | 6 | — | 60 | **D1 沉在 low 里** |
| 第十一轮（W72 caller 追查） | 21 | — | 45 | 抓到 D1/D18，但带进约 8 条噪声 |
| W73 数据流判据 | 5 | 16 | 45 | 必须"存进变量再写这个变量"才算 |
| W74 falsy 早退 | 4 | 15 | 46 | caller `if not X: return` ⇒ 空值被拦 |
| W74b 否定式使用 | 3 | 11 | 52 | `if not f(): return False` ⇒ True 是保守值 |
| W75b 排除 dumps | 3 | 11 | 52 | `json.dumps` 不是写盘 |
| **W75c 移除 `replace`** | **3** | **6** | **57** | `dt.replace` 是内存操作 |
| **应用抑制** | **3** | **0** | 53 | 10 条人工确证假阳性 |

**最终 3 条**：

| 位置 | 对应 |
|---|---|
| `api/config_routes.py:27` `_load_cfg` | **D1** |
| `memory/feeder.py:50` `_read_role_state` | **D18** |
| `memory/feeder.py:54` `_read_role_state` | **D18** |

---

## 二、三次修正，每次补的是语义而不是参数

### W73：数据流判据

"函数内有写盘调用"太粗。真正的破坏性覆盖形态是：

```python
cfg = _load_cfg()        # ← 空值存进变量
cfg["persona"] = ...
_save_cfg(cfg)           # ← 把这个变量写盘
```

**必须是"同一个变量"读出又写入。** 只满足"caller 里也有写盘调用"的宽松情况降为 medium（诚实标记为"不确定"），精确命中才给 high。

### W74：falsy 早退

`extractor.py:89`：

```python
facts_raw = self._parse_llm_json(text)
if not facts_raw:
    return {...}          # ← 空值被拦在这里，不进下游
```

caller 对返回值做 falsy 检查就早退 ⇒ 空值根本到不了写盘 ⇒ 假阳性。

这条把 `_parse_llm_json` 从 medium 降到 low。

### W74b：否定式使用

`conflict.py:118`：

```python
if not self._time_ranges_overlap(tr1, tr2):
    return False
```

**返回值被"否定式"使用**：解析失败返回 True ⇒ 走"判定为冲突"分支 ⇒ 这是**保守值**（宁可多报冲突，不会漏报）。

这是第十一轮规则判 high、第四轮人工判假阳性的矛盾点。W74b 让规则与人工结论一致——**规则反过来验证了人工判断，而不是推翻它**。

---

## 三、W75c：一个关键字引发的一整类误判

`_parse_reminder_at` 被判"自身写盘"，查下来命中的是：

```python
now.replace(...)      # datetime
dt.replace(hour=...)  # datetime
```

**`replace` 在 Python 里主要是内存操作**（str/datetime/dict），文件替换是 `os.replace` —— 而后者已在精确列表里。

宽松匹配加 `replace` 的收益为零，代价是让一整类 datetime 操作被误判。已从 `_LOOSE_WRITE` 移除。

这条和 W75b（`json.dumps` 被 `dump` 子串命中）是同一个教训的两面：**宽松匹配扩大召回的同时，把"名字里有这个词"误当成"是这类操作"**。修法是两条腿——精确列表覆盖已知 API，宽松匹配只用于兜底项目自定义函数名，且要维护排除集。

---

## 四、10 条抑制的逐条理由

| 位置 | verdict | 一句话理由 |
|---|---|---|
| `extractor.py:151` `_fetch_ma_schedules` | fp | 空 dict 只喂进 LLM prompt，不落盘 |
| `versions.py:87` `_load_version` | fp | caller 有 `if data:`；`snapshot` 里 falsy 就跳过去重，写的是新数据不是空值 |
| `engine.py:90/108/118` `_import_legacy_snapshot` | fp | 裸 `return`，读失败保持原样，不写不删 |
| `engine.py:228` `_trace_id` | fp | 空串只喂日志 |
| `memory_agent.py:423` `_butler_patch` | fp | caller `return res is not None` ⇒ **fail-closed** |
| `extractor.py:242` `_parse_llm_json` | fp | caller `if not facts_raw: return` 早退 |
| `schedule.py:48` `_parse_reminder_at` | fp | caller `if not run_date: return "无法解析时间"` 早退 |
| `conflict.py:147` `_time_ranges_overlap` | conservative | 否定式使用，True 是保守值 |

最后一条值得单说：**它同时是"真命中"和"假阳性"。** 规则报"返回 True 伪装成功"是对的，但那个 True 的下场是"判定为冲突"——保守方向。所以判 `conservative` 而非 `false_positive`，保留在报告里但不立缺陷。

---

## 五、SECRETS 消失的 drift 已结案

第十一轮门禁报 `[-dirty] EXT-SECRETS-Secret Keyword 消失（原 ×3）`。

**本轮实测：不是规则失效。** 直接跑 detect-secrets 在 dirty 样本上得到 1 条命中（`sample.py:308`），经分析器汇总后 dirty 共 3 条 SECRETS 命中（308、7、14）。

结论：那条 drift 是**第十一轮改 bandit 的 `--exclude` 参数期间跑门禁留下的错误基线**，不是规则问题。本轮不再出现。

这也再次说明：**"命中消失"要先怀疑基线/环境，再怀疑规则**——但永远要查，不能默认跳过。

---

## 六、审计结果

| 指标 | 数值 |
|---|---|
| AFS-01 | 66 条 → **high 3 / medium 0 / low 53** |
| 抑制生效 | **10 条**（8 fp + 1 conservative + 1 其他） |
| 本轮新增缺陷 | **无**（聚焦收敛已有命中） |
| 台账 | D1–D18 不变 |
| 门禁 | ok=True，analyzers=33，drift=4（常规项） |

---

## 七、下一轮方向

1. **核验剩余 low 53 条**（量大但应无 P0；可按文件聚类抽样）
2. **核验其他规则族**：DO-* 11 条、RSC-02、CONC-08、FO-01
3. **写补丁副本（db-patched）**——D1–D18 证据已足够，AFS-01 的 3 条 high 就是修复的靶子
4. **把 W73/W74/W74b 的语义判据外推到其他规则**："返回值被怎么用"这类判据不只 AFS-01 需要

---

## 八、本轮最该记住的一条

**收敛靠补判据，不靠调参数。**

这条曲线里我调过三次参数：别名表宽窄（无效）、跳数 2→1（丢 D18）、宽松匹配加不加 `write`（有效但只解决一半）。**真正让曲线降下来的是三个语义判据**——数据流、falsy 早退、否定式使用。它们回答的是同一个问题：**这个空值，最后到底被用来干什么。**

规则原本只问"函数里有没有写盘"，这是个**结构问题**；真问题是"空值的**归宿**"，这是个**语义问题**。从结构判据升级到语义判据，噪声自己就掉了——不是被参数筛掉的。

第十一轮我说"调参救不了判据缺口"，本轮是同一条结论的正面验证：补上语义后，21 → 3，而且 3 条全是真的。
