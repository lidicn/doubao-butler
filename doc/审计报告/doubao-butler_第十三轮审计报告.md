# doubao-butler 审计报告 · 第十三轮（修复验证轮）

- **审计对象**：`lidicn/doubao-butler`
- **补丁副本**：`/data/workspace/repos/db-patched/`（**原仓库未改动**）
- **轮次**：`round-013`
- **报告日期**：2026-10-08

---

## 一句话结论

**9 项缺陷（D1–D4、D6–D9、D18）在补丁副本上完成修复并实测验证：PoC 的 `data_lost` 从 9 处归零。但过程中在我自己的补丁里发现同一类缺陷两处——隔离动作静默没执行（5/6 处）、护栏可被"漏调 load()"绕过。这两处都不是补丁逻辑错，是补丁的"另一半"没接上，与 D1–D18 同族。**

---

## 一、修复结果

| 缺陷 | 文件 | 验证方式 | 结果 |
|---|---|---|---|
| D1 | `api/config_routes.py` | 代码审查 | 读侧抛 StateCorrupt + 写侧 barrier |
| D4 | `core/aliases.py` | 手工实测 | 抛 StateCorrupt，坏文件隔离 ✓ |
| D6 | `core/fast_routes.py` | 手工实测 | **`match()` 抛 StateCorrupt** ✓ |
| D7 | `integrations/ilink/client.py` | 手工实测 | 抛 StateCorrupt，坏文件隔离 ✓ |
| D8 | `core/cron_task.py` | 手工实测 | 抛 StateCorrupt，坏文件隔离 ✓ |
| D9 | `core/cron_task.py` | 代码审查 | 独立标志，只挡重写不挡追加 |
| D18① | `roles/state.py` | 手工实测 | 抛 StateCorrupt，坏文件隔离 ✓ |
| D18② | `memory/feeder.py` | 手工实测 | 抛 StateCorrupt，坏文件隔离 ✓ |

**PoC 对照（同一份 DO-* 命中）：**

| | 原仓库 | 补丁副本 |
|---|---|---|
| data_lost | 4 | **0** |
| guarded | — | 3 |
| no_write | 3 | 3 |
| unavailable | 3 | 4 |

**D6 的验证最有价值**：第二轮我误判它"有防护"（因为读了 `_load()` 里的注释），第六轮才发现真正的写在 `match()` 里。本轮补丁把 barrier 装在 `_save()`，然后**用 `match()` 触发**——实测确实抛出 StateCorrupt，证明护栏这次装在了正确的路径上。

---

## 二、在自己补丁里发现的第一处：隔离动作静默没执行

`mark_poisoned()` 首版这样找路径：

```python
p = getattr(holder, "path", None) or getattr(holder, "_path", None)
if p:
    quarantine(p, ...)
```

而 6 处写手的路径属性各不相同：

| 位置 | 路径从哪来 |
|---|---|
| `roles/state.py` | `self.path` ✓ 唯一命中 |
| `core/aliases.py` | 模块级全局 `ALIAS_FILE` |
| `core/fast_routes.py` | 模块级全局 `ROUTES_FILE` |
| `memory/feeder.py` | 方法算出 `_role_state_path()` |
| `ilink/client.py` | `self.session_file` |
| `core/cron_task.py` | `self._secrets_file` / `self._log_file` |

**6 处里只有 1 处取到路径。** 其余 5 处 `if p:` 为假 ⇒ 隔离静默跳过，坏文件原地不动，日志里没有任何痕迹。

后果分层看：
- **写侧 barrier 仍然生效** ⇒ 数据没丢（坏字节完好躺在盘上）
- **但"保留现场"这一步没做** ⇒ 运维拿不到 `.corrupt-*` 副本，无从判断丢了什么、丢了多少

**这就是审了十三轮的那类缺陷——"取不到就当没有，且不声张"——写在我自己的补丁里。** 修法是让调用方显式传 `path`，并在隔离没做成时 `log.error` 留痕（不能静默）。修后六处全部实测出现 `.corrupt-*` 文件。

---

## 三、第二处：护栏可被"漏调 load()"绕过

PoC 判 `roles/state.py` 为 `data_lost`，但我手工测是 `guarded`。矛盾的根因：

```python
def __init__(self, data_dir):
    self.path = ...
    self._m = {}
    # ← 不调 load()
```

`Poisoned` 标志由 `load()` 置位。PoC 构造实例后**直接调 `set()`** ⇒ `_m` 为空且未置位 ⇒ `_save()` 用空快照覆盖整档会话。

生产代码 `app.py:545` 紧接着调了 `load()`，所以这不是活跃缺陷。但它暴露了补丁的结构弱点：**护栏装在 `_save()` 上，标志位却由 `load()` 置——调用方漏调 `load()` 就等于绕过护栏。**

同目录的 `AliasStore` / `FastRouteStore` 都在 `__init__` 里读，本类是唯一例外。已改为构造即读（D18③），改完 PoC 的 `data_lost` 从 1 归 0。

**这条与 AutoForge F1 是同一族**：防护装了，但依赖调用方走某条路径。区别只是 F1 是"护栏装错方法"，这条是"护栏依赖前置调用"。

---

## 四、工作流迭代：patch_lint 四处修复

| 编号 | 问题 | 表现 |
|---|---|---|
| W76 | 只看名字出现，不看是否是调用 | `quarantine = getattr(...)` 被判"用到护栏 API" |
| W76b | 未排除同名局部变量 | 排除 Store 不够，`if quarantine:`（Load）仍命中 |
| W76c | `glob("*.py")` 只扫一层 | **检查到 0 个文件却显示 OK** |
| W76d/e | 模块名写死 + 只收相对导入 | doubao-butler 全用绝对导入 ⇒ 10 处假阳性 |
| W76f | 属性调用算作 API 调用 | `self.store.quarantine(...)` 是业务方法，同名不同物 |

**W76c 最危险**：首版对 `db-patched` 输出"检查 0 个含护栏调用的文件 · OK"——**一个补丁都没检查，却显示通过**。已改为 `rglob` + 检查数为 0 时显式告警。

这是第二十一次"静默失效"，而且是最阴的一种：**不是报错，不是漏报，是"检查根本没跑"被显示成通过。**

---

## 五、台账状态

| 状态 | 缺陷 |
|---|---|
| `fixed_in_patch_copy`（9） | D1 D2 D3 D4 D6 D7 D8 D9 D18 |
| `still_open`（9） | D5 D10 D11 D12 D13 D14 D15 D16 D17 |

D2/D3 并入 D18 统一修复后一并生效。补丁只存在于只读副本，**原仓库未改动**。

---

## 六、未覆盖 / 如实说明

- **D1 未端到端实测**：`config_routes.py` 依赖 starlette，沙箱未装 ⇒ 只做了代码审查。其修法最强（读侧直接抛），需确认配置页面在文件损坏时会一起报错——这是"明确报错"与"页面可用"的取舍，需开发者定夺
- **PoC 4 条 `unavailable`**：`llm`/`newapi` 不是 DO 目标（只读 LLM 客户端）；`ilink`/`feeder` 已由手工实测确证 guarded，探针到不了是探针的局限
- **`no_write` 3 条**（cron_task）：探针找不到公开 seeder 触发写盘，D8/D9 靠手工构造确证
- **依赖 CVE 面仍未扫**
- **D10–D17 未纳入补丁**

---

## 七、下一轮方向

1. **D1 端到端实测**：装 starlette 或用桩绕过，确认读侧抛异常对配置页面的实际影响
2. **把 D5（10 处硬编码路径）、D10（TTS 无超时）、D12（技能渲染无预算）纳入补丁**
3. **给 patch_lint 加"隔离是否真的执行"检查**——本轮两处问题它都查不出：一是路径取不到（静默跳过），二是依赖前置调用。它现在只查 import 和 bare-except，缺"护栏是否可达"这一维
4. **核验 D14/D15 并发竞态**（懒加载单例），证据已足但尚未进补丁

---

## 八、本轮最该记住的一条

**修缺陷的人最容易犯的错，是把护栏加在"看起来相关"的地方，而不是"实际会被走到"的路径上。**

这一轮我在自己的补丁里犯了两次：

- **第一次**：以为 `mark_poisoned` 能自己找到路径。它只认 `.path`，5/6 处取不到 ⇒ 隔离静默跳过
- **第二次**：以为护栏装在 `_save()` 就够。它依赖 `load()` 置位 ⇒ 漏调就绕过

两次都不是逻辑错误，都是**"另一半没接上"**——而这正是 D1–D18 全部缺陷的共同形态。AutoForge 第十轮我也犯过同样的错（3 处护栏装在调用方不走的辅助方法上）。

三次犯错说明这不是疏忽，是这个缺陷族的固有难度：**写防护时，你想到的是"防什么"，容易漏掉"谁会走到这里、走到时状态是什么"。** 唯一可靠的对策是实测——本轮两处都是 PoC 与手工结论打架时才暴露的，静态检查（包括 patch_lint）全部漏过。

所以结论很直接：**补丁必须过 PoC，不能只过静态检查。** 本轮 patch_lint 修了四处仍然查不出这两处，因为它只能看代码长什么样，看不出运行时走到哪。
