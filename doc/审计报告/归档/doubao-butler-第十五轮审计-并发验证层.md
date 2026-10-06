# doubao-butler 第十五轮审计：并发验证层

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮方法：**新增并发验证层** —— 专治"静态读代码证不实"的并发缺陷
> 交付：新增 `concurrency.py`（4 个实验）；**V28（P0-2 首次实证）**；**V29（本轮新发现 P1）**；套件 29 场景 / 58 条用例；全仓库 **7 failed / 645 passed**（新增 4 passed，无回归）；覆盖度 **16.4%**

---

## 0. 一句话

**十四轮悬案的 P0-2 首次被实证，而且顺带炸出一个没人预料到的新缺陷 V29。**

并发层第一次运行就产出了两条实证结论——这验证了"运行时验证"确实能补上静态分析的天花板。

---

## 1. 工作流新增：`concurrency.py`

四个实验，都是真起线程、真连 SQLite：

| ID | 实验 | 结果 |
|---|---|---|
| **C1** | SQLite 懒初始化竞态（放大窗口） | **HALF_INIT** ← P0-2 实证 |
| **C1\*** | 修法对照（双重检查 + 初始化完才发布） | **OK** ← 修法有效 |
| **C2** | 跨线程写同一连接 | **4 次 OperationalError** ← **新发现 V29** |
| **C3** | 模块级 dict read-modify-write | MATCH（未丢更新，但不能据此判安全） |

---

## 2. V28（P0-2 实证）：十四轮悬案终于证实

### 源码事实（逐行确认）

```python
with _lock:
    _conn = c          # L24，缩进 12 → 发布在锁内
_init(_conn)           # L26，缩进 8  → 初始化在锁外 ★
```

### 确定性实验（把 `_init` 换成慢版本，窗口从毫秒放大到 400ms）

```
DB 文件事先存在 = False
   0.002s  init:start
   0.102s  T2: _conn 已发布 = True
   0.102s  T2: 此刻表数 = 0        ← 拿到半初始化连接
    0.41s  init:done
VERDICT HALF_INIT
```

**反测**：应用修法（双重检查 + 初始化完成后再发布）后，T2 阻塞到 `0.407s` 才拿到连接，看到 **13 张表**。

> 这是 P0-2 第一次被**执行**证实。前十四轮只能靠读代码推断。
> 第十四轮变异测试已经解释了为什么证不了：**现有测试从未跨线程写入**。

### 一个必须记录的探测陷阱

探测半初始化窗口时，**必须查业务表**（`SELECT ... FROM dialog_turns`），**不能查 `sqlite_master`**——后者在表不存在时返回 0 行，看起来和"表已建好但未插入"一样，会得**假阴性**。我早期版本就踩了这个，得到过误导性的"表已存在"结论。

---

## 3. V29（P1，本轮新发现）：跨线程写同一连接直接报错

**这是 C2 实验的意外产出，前十五轮从未有人提过。**

8 线程各写 25 条（共 200 条）到 `get_conn()` 返回的同一个连接：

```
并发写异常数 = 4
    OperationalError: cannot start a transaction within a transaction
    OperationalError: cannot start a transaction within a transaction
    ...
```

**机制**：sqlite3 的 Python 封装为每个 DML 隐式开事务；两个线程交错执行 INSERT 时，后一个在前一个事务未结束时再开事务 → 直接抛异常。

**关键规模数据**：`store/repo.py` 中 `get_conn()` 调用 **38 处**，**加锁 0 处**。而本项目至少三条线程会碰 DB：
- paho 网络线程（MQTT 回调）
- APScheduler 线程（16 个定时任务）
- 主事件循环

**反测**：写操作包 `with guard` 后，同样 8×25 条并发写，**异常数 = 0**。

⚠ 更彻底的修法是每线程独立连接，避免共享 connection 的隐式事务耦合。

---

## 4. 工作流自身踩的三个坑（已记录进脚本注释）

| 坑 | 后果 |
|---|---|
| **环境变量名是 `DATA_DIR`**，不是 `BUTLER_DATA_DIR` | 用错会写进 `/app/data/butler.db` 残留库，得**假阴性** |
| **探测要查业务表**，不能查 `sqlite_master` | 表不存在也返回 0 行 → 假阴性 |
| **四密钥启动期硬校验** | 缺 `DOUBAO_API_KEY`/`DESKPILOT_API_TOKEN`/`TASK_REPORT_TOKEN`/`BUTLER_WEB_PASSWORD` 任一即 `raise RuntimeError`，实验全部跑不起来 |

---

## 5. 审计了未读 god 节点：三条负面结论

图谱层指出的未读枢纽，本轮审了 `config.py`(39 入度)、`core/agent.py`、`core/tools.py`、`bus/topics.py`、`skills/runner_types.py`。**一条新缺陷都没有**，但有三点值得记：

### 5.1 `config.py` 的 fail-fast 做得比我预期好

`Settings.load()` 末尾三处硬校验（`WO-BUT-017/018/022`）：缺 `DOUBAO_API_KEY`、`DESKPILOT_API_TOKEN`/`TASK_REPORT_TOKEN`、空 `BUTLER_WEB_PASSWORD` → **直接 raise，不静默回退到泄露口令**。注释明写"禁止静默回退"。

这与本项目普遍的"兜底掩盖故障"倾向**相反**——密钥这块是例外，做得对。

### 5.2 `agent.py` 已用 contextvar 防并发串话

```python
# P1-1：用 contextvar 记录当前请求用户输入，避免并发串话
_current_user_text.set(text)
```

并发安全已经被考虑过了。

### 5.3 两处轻微问题（不值得单独立项）

- `get_settings()` 懒单例无锁——与 V28 同型，但 `Settings.load()` 是幂等的，双初始化最多产生两个等价对象，影响很小，定 **P3**
- `_env_bool` 里的 `return False if v == "" else False` 是冗余三元（两支都返回 False），死代码但无害
- `host: str = "0.0.0.0"`（ruff S104）—— Docker 内的常规默认值，且鉴权默认拒绝，可接受

---

## 6. 十五轮元观察

| 轮次 | 层 | 新缺陷 |
|---|---|---|
| 12 | 属性测试 | 0 |
| 13 | 代码图谱 | 1 (P3) |
| 14 | 变异测试 | 0（测的是**测试本身**） |
| **15** | **并发验证** | **2（1 个 P0 实证 + 1 个新 P1）** |

**静态分析确实到顶了，运行时验证一开就出活。** 但也要如实说：本轮的产出集中在 DB 并发这一处，不代表全仓。

---

## 7. 诚实边界

1. **C3 一次 MATCH 不能判安全**：CPython GIL 下单字节码是原子的，但 `get + 1 + set` 三步不是；本次没丢更新只是调度没撞上。
2. **C2 的 4 次异常是实验环境构造的**（`threading.Barrier` 强制对齐）。真实触发频率取决于实际调度，**未量化**。
3. 覆盖度 **16.4%（36/220）**，本轮只审了 5 个文件。
4. **完整 lifespan 仍未跑起来**——真实 MQTT、16 个定时任务、presence 轮询，十五轮零进展。
5. 全仓库仍有 7 failed（`test_v25_pytest_shim.py` 与 deskpilot/tvpilot），与本项目改动无关。

---

## 8. 修复优先级（累计 5 个 P0 + 12 个 P1，仍为零修复）

1. **V23**（P0，5 分钟）`_run_coro` 改同 loop await —— 唯一能挂死进程
2. **V18**（P0，5 分钟）`docker_tools` async 内 `time.sleep` + 缺 import
3. **V25 / V28**（P0，15 分钟）SQLite 发布顺序 —— **本轮已实证并验证修法**
4. **V29**（P1，20 分钟）写操作加锁或每线程独立连接 —— **本轮新发现**
5. **V26**（P1，10 分钟）anomaly 补 `device_id`、删 `priority`
6. **V24**（P1，1 行）`await asyncio.to_thread(...)`
7. **V13**（P1，30 分钟）15 处 `.result(timeout=30)`
8. **V21 / V22 / V7 / V20 / P0-1 / P0-3**

**前三条合计约 25 分钟。**
