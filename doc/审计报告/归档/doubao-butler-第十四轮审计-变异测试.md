# doubao-butler 第十四轮审计：变异测试（测试有效性）

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮方法：**变异测试** —— 不找新缺陷，回答「这 641 个 passed 里有多少真在测东西」
> 交付：新增 `mutation.py`（自写轻量变异器）；**测试盲区地图**；**3 个工具自身缺陷已修正**；全仓库 7 failed / 641 passed（无回归）

---

## 0. 一句话结论

**641 passed 有水分，但不是"空测试"，而是"覆盖不均"**：
- 最差的 `write_failures.py` **杀死率 31%**
- `triggers/engine.py` **36%**
- 最好的 `bus/inbox.py` **84%**

而且**变异器自己踩了三个坑**，其中两个直接产出过假结论（100%、0%）。这三个坑比盲区数据本身更有价值。

---

## 1. 为什么自写变异器而不是用 mutmut

mutmut 装上了，但**与本项目 `tests/` 结构不兼容**：它把 `tests/` 整份复制进 `mutants/` 再跑，而本项目测试用 `from butler.core import briefing` 这类绝对导入，路径一换就 `ImportError`（实测）。全量跑 41k 行也需数十分钟到数小时。

自写版本：只变异有相关测试的文件、只跑相关测试、有超时与上限、有进度。

---

## 2. 三个工具自身缺陷（本轮最有价值的产出）

### 坑一：把"模块加载失败"当成"测试抓住了变异"

第一版按 `returncode != 0` 判定 killed。但 pytest 在**模块加载失败**时也是非 0 —— 那不是测试抓住了变异，是变异把模块弄坏了。

后果：`triggers/engine.py` 25 个变异**全部被判 invalid**，而在修正前它们全被算成 killed → **虚报"杀死率 100%"**。

修正：输出里必须有真实的 `N failed` 才算 killed；只有 `error` 的算 invalid，且**排除出分母**。

### 坑二：测试文件名没带目录前缀

`_related_tests` 返回裸文件名 `test_dedup.py`，而 pytest 的 cwd 是 repo 根 → "file not found" → usage error(rc=4) → 被判 invalid。

后果：修复判定逻辑后 dedup / queue **全部变 invalid**，存活率失真。

### 坑三（最严重）：文件名匹配 ≠ 测的同一个东西

原按 stem（文件名）匹配：`butler/triggers/engine.py` 的 stem 是 `engine`，匹配到 `test_decision_engine.py`。

**但那个测试测的是 `butler/decision/engine.py`，根本不 import triggers。**

后果：误得「**杀死率 0%、`test_decision_engine.py` 是空测试**」。我差点把这个写进报告。手动核查才发现该测试有 9 个断言、测的是另一个 engine。

修正：改为**按真实 import 关系匹配**（扫描测试文件里的 `from X` / `import X`）。

修正后真实数据：**36%**（不是 0%）。

**教训**：工具报出的异常结论，先怀疑工具自己。这是纪律①（工具故障 ≠ 无问题）在变异层的第三次应验。

---

## 3. 测试盲区地图（修正后数据）

| 源文件 | 变异数 | 杀死率 | 评价 |
|---|---|---|---|
| `bus/inbox.py` | 25 | **84%** | 最好 |
| `core/dedup.py` | 5 | **60%** | 尚可 |
| `tts/queue.py` | 25 | **52%** | 中等 |
| `triggers/engine.py` | 25 | **36%** | 薄弱 |
| `store/write_failures.py` | 13 | **31%** | **最差** |

**值得注意`：write_failures.py` 是 9-30 事故后新增的运维模块，测试质量却最差（31%）。**

---

## 4. 三条有实质意义的存活项

### 4.1 V8 的冷却逻辑几乎无测试保护

`triggers/engine.py` 中冷却相关变异**全部存活**：

```
L59  if not self._cooldown_file or not os.path.exists(self._cooldown_file):
     → `or` 改 `and` / 去掉 not  ⇒ 存活
L67  if isinstance(v, (int, float)) and now - v < 86400 * 7:
     → `and` 改 `or`            ⇒ 存活
```

L67 是「冷却条目是否过期」的判断。改成 `or` 后语义完全变了（本该要求"是数字**且**在 7 天内"），**没有任何测试发现**。

这与 **V8（冷却非原子写 + 静默归零 → 重启后触发风暴）** 直接相关：修复 V8 时无法靠现有测试验证。

### 4.2 P0-2 十三轮无法证实的原因找到了

`bus/inbox.py` L96：

```
c = sqlite3.connect(..., check_same_thread=False)
→ 改成 True ⇒ 存活（测试仍绿）
```

**说明现有测试从未做真正的跨线程写入。** 这正解释了为什么 P0-2（SQLite 跨线程）十三轮都只能靠代码推断、无法用测试证实——**测试根本没覆盖这个场景**。

要证实 P0-2，必须新写跨线程测试，而不是继续读代码。

### 4.3 dedup 的"取最大相似度"无保护

`core/dedup.py` L43：

```
best = max(best, jaccard(bg, fp))
→ 改成 min ⇒ 存活
```

防重复的核心语义（取最相似的那条）**没有测试断言**。改反了都发现不了。

---

## 5. 与属性测试的互补（正面结论）

`is_quiet` 的"起止相同 = 全天可播"分支（`tts/queue.py` L160）在 `test_tts_queue.py` 里**存活**（说明单元测试没覆盖）。

但第十二轮的属性测试 `inv_quiet_playable_span_has_exact_length` **用 400 组随机组合覆盖了它**。

**这印证了分层价值**：单元测试的盲区，属性测试补上了。两层不是重复投入。

---

## 6. 变异器的局限（必须说清）

- **只做了单 token 变异**（比较符/布尔/±1/max-min），不做语句级变异，因此存活率会**偏高**（真实 mutmut 算子更多）。
- **只跑相关测试**，不是全量。
- **只扫了 5 个文件**（共 93 个变异）。41k 行项目的整体杀死率仍是未知数。
- 变异期间会**临时改写源文件**，靠 finally 还原。已验证全仓库 641 passed 与改动前一致，**无残留**。

---

## 7. 工作流变更

- 新增 `scripts/mutation.py`（含上述三个坑的修正与注释）
- `pipeline.py` 新增 `--mutate <files...>` 阶段 S3m
- 存活项自动转为 `M-*` finding，severity P2

---

## 8. 下一步建议

1. **新写跨线程测试证实 P0-2** —— 现已明确：现有测试覆盖不到，必须新增。这是十三轮悬案的唯一解法。
2. **为 `write_failures.py` / `triggers/engine.py` 补测试** —— 31% / 36% 是全仓最弱两处，且前者是事故后新模块。
3. **修复 V8 前先补冷却测试** —— 否则无法验证修法。
4. 扩大变异扫描到更多文件（当前仅 5 个）。

---

## 9. 项目侧修复清单（累计，仍为零修复）

1. **V23**（P0，5 分钟）`_run_coro` 改同 loop await —— 唯一能挂死进程
2. **V18**（P0，5 分钟）`docker_tools` async 内 `time.sleep` + 缺 import
3. **V25**（P0，15 分钟）SQLite 发布顺序 + 写操作加锁
4. **V26**（P1，10 分钟）anomaly 补 `device_id`、删 `priority`
5. **V24**（P1，1 行）`await asyncio.to_thread(...)`
6. **V13**（P1，30 分钟）15 处 `.result(timeout=30)`
7. **V21 / V22 / V7 / V20 / P0-1 / P0-3**
8. **V27**（P3，10 分钟）Runtime 补注解或去 `@dataclass`

**前三条合计约 25 分钟。**
