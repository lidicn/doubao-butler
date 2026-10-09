# doubao-butler 审计报告 · 第八轮

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-008`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-008/`
- **报告日期**：2026-10-07

---

## 一句话结论

**D15 确证（SQLite 懒连接同型竞态，8 线程创建 8 个连接、7 个泄漏），扫描范围从 1339 收敛到 1033（high 73→48）。但本轮真正的教训是 W67b：给 bandit 加 `--exclude` 时把 `test,tests` 当 glob 传进去，结果 `selftest/` 整个目录被匹配掉，dirty 样本 0 命中——由门禁 drift 兜住，是第十九次「工具静默失效」。**

---

## 一、确证缺陷：D15【P2·medium】SQLite 懒连接同型竞态

`butler/core/decision_store.py:20`（`notification_store.py:20` 同形）：

```python
def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:                      # ← check
        c = sqlite3.connect(...)           # ← act
        _init(c)
        _conn = c
    return _conn                           # ← 读的是全局，不是自己建的
```

**实测**（monkeypatch `sqlite3.connect` 计数，8 线程 barrier 同发起）：

| 指标 | 结果 |
|---|---|
| `sqlite3.connect` 调用 | **8 次，8 个不同对象** |
| `_init(c)` 执行 | **8 次，8 个不同对象**（建表语句重复跑） |
| 返回句柄去重 | 1 |
| **泄漏连接** | **7 个**（创建后从未关闭） |

**后果与 D14 不同**：D14 是"多实例各自全量覆盖 → 数据丢"；D15 是"**多连接创建但只留一个 → 连接泄漏**"。每个泄漏连接持一个 fd 与一个 WAL 读锁。

为什么返回句柄去重是 1：`return _conn` 读的是**全局变量**而非自己创建的 `c`，所以所有线程最终都拿到最后被赋值的那个。这反而**避免了多写端覆盖**，故严重度低于 D14。

量级有限（启动期一次性 7 个 fd），但 WAL 下多余读锁会加剧写冲突，且 `notification_store` 同形。

---

## 二、扫描范围收口：1339 → 1033

W67（本轮）：把 22 个分析器的 `for p in sorted(root.rglob("*.py"))` 批量换成 `_common.py_files()`。

| 指标 | 收口前 | 收口后 |
|---|---|---|
| 原始命中 | 1339 | **1033** |
| high | 73 | **48** |
| doc/ 命中 | 82 | 1 |
| tests/ 命中 | — | 7 |

**残留的 8 条全部来自 `external_tool_defects`**——它是唯一不共享 AST 遍历逻辑的分析器，bandit 走自己的 `-r` 递归。这恰好印证了 W66 注释里的担心：**任何一处忘记跳过就前功尽弃，而最容易漏的就是这种旁路。**

### W67b：给 bandit 加 `--exclude`（以及我犯的错）

第一次写法：把 `SKIP_DIRS` 的名字原样传给 `--exclude`：

```
--exclude test,tests,doc,docs,vendor,...
```

**结果：dirty 样本 0 命中。** 因为 bandit 把它们当 glob 匹配路径，`test` 匹配到了 **`selftest/cases/dirty/...`** —— 整个样本目录被排除。门禁 drift 立刻报：

> `[-dirty] external_tool_defects: EXT-BANDIT-B310 消失 / B501 消失 —— 规则可能失效`

**第二次写法**：改成 `*/test/*,*/tests/*,*/doc/*,...` 形式，实测 dirty 样本恢复 10 条命中，doubao-butler 上 doc/tests 残留归零。

这是**第十九次**"工具静默失效"，失效方向照例是"命中变少"。这次能被发现，靠的是**门禁的 drift 检测**——它不假设"命中变少是好事"，而是把任何消失都报出来。这已经是它第 N 次救场。

---

## 三、审计结果

| 指标 | 数值 |
|---|---|
| 原始命中（后收口） | **1033**（high 48 / medium 506 / low 479） |
| 外部工具命中 | 153（bandit+secrets，doc/tests 残留 **0**） |
| 本轮新增确证 | **D15** |
| 台账累计 | **D1–D15** |
| 门禁 | ok=True，analyzers=33 |

---

## 四、按根因合并（更新）

| 根因 | 缺陷 | 计数 |
|---|---|---|
| 读路径的失败被当成「没有数据」 | D1 D2 D3 D4 D6 D7 D8 D9 | 8 |
| 失败路径没兜住 / 无超时 | D10 D12 D13 | 3 |
| 并发初始化竞态 | **D14 D15** | 2 |
| 配置面不一致 | D5 | 1 |
| 观测语义矛盾 | D11 | 1 |

D14/D15 是**同一个根因（check-then-act 未原子化）的两个面**：一个丢数据，一个漏连接。全项目 `global _` 26 处、`if _X is None:` 10 处，修复时应当**统一处理而不是逐条改**。

---

## 五、未覆盖 / 如实说明

- **`doc/` 残留 1 条**：`af_service.py:1668` EXT-BANDIT-B603，来自 bandit 对 doc 目录的扫描路径与 py_files 判定不一致（bandit 用 `*/doc/*`，该文件在 `doc/ask/src/` 下应该被排除，需进一步核对）——**未确认为零残留**
- **semgrep 不可用**（rc=127）
- **pip-audit 已 ok 但依赖 CVE 面仍未扫**（本轮未执行该阶段）
- **1033 条仅完成范围收口，未做内容分诊**
- **D15 的两处 `_init` 重复建表是否引发并发 DDL 错误，未实测**（SQLite WAL 下并发 CREATE TABLE IF NOT EXISTS 通常安全）

---

## 六、下一轮方向

1. **AFS-01 剩余约 12 条核验**（扫描范围收口后数量已下降，现在更容易做）
2. **把 26 处 `global _` 懒单例统一改造**（D14/D15 的根因层修复）
3. **给分诊层加「调用方如何处理失败返回值」信号**（四、五、七轮累计 9 条假阳性靠人工读出）
4. **跑依赖 CVE 面**（pip-audit 已就绪）
5. **考虑写补丁副本（db-patched）**，把 D1–D15 推进到"修复并验证"

---

## 七、本轮最该记住的一条

**统一入口的价值不在于它自己多正确，而在于不再有第二个地方能漏掉。**

W66/W67 把 22 个分析器的遍历逻辑收口到 `py_files()`，效果立竿见影（1339→1033）。但**残留的 8 条恰恰来自那个没被收口的旁路**（bandit 自己递归），而且我在给旁路打补丁时又制造了一次静默失效。

结论：**"统一入口"必须真的涵盖所有入口，包括外部工具的。做了一半的统一比不统一更危险**——因为你会以为已经统一了。门禁的 drift 检测是唯一可靠的兜底，它从不假设"命中变少是好事"。
