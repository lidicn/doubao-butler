## §12 DCD 裁定③ 落地 · 批11 TTL 过期分支＋「为什么没响」审计腿（2026-10-02）

本节口径**覆盖** §10-1 表里 ③ 那行的「⛔ 未开工」，该行自本节起作废；⛔ 改动上面各节已定稿的字。
追加器＝`scripts/audit_1002/patch_ledger_batch11_1002.py`，本节正文本体＝`scripts/audit_1002/ledger_1002_batch11_section.md`
（正文单独一个文件＝⛔ 拿 python 字面量装文书：字符串里的 `\r`/`\f` 会被静默吃掉，shell 双引号又把反引号当命令替换）。
裁定＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md:44-52`；提交＝`371276b`（三条腿）＋`0f8b8fb`（注释一枚错行号改口）＋`2075a77`（拆掉本批自造的第二枚空号）。

裁定原文（本节要销的那格，`:48`）：「**落地点**：消息进风控时带时间戳（`check_tts` 已有 `now`）+ 一条过期分支 + 审计表（让『为什么没响』可查）」。
起算点＝`:50`「从『消息进风控那一刻』起算……『播报失败重投』起算会让 TTL 在失败风暴下永不到期，失去意义」。

### §12-1 一处**落点**偏移（能力⛔ 缩；这处偏移裁定⑤ 自己已经指了方向，⛔ 另开一问）

裁定那句挂在「进风控」上，而风控那侧的 TTS 队列**不产生年龄**：`butler/api/tts_routes.py:265` 调 `pg.check_tts(...)`，
播完就在 `:280-281` 与 `:292-293` 成对 `pg.tts_pop(); pg.tts_unlock()` ⇒ `PushGuard._tts_queue`（`push_guard.py:87`）
是 1:1 排空的计数器，条目在这一层待不过一个请求，过期分支写在这里只会多一条没人走的路——正是裁定要拆的那类空号。
真让一句话排队等待的是 `butler/tts/queue.py`（`notify/router.py:281` `res = self.tts_queue.enqueue(`；尺＝
`grep -n "self.tts_queue.enqueue" butler/notify/router.py`）。

⇒ 三条腿全落队列侧：时间戳＝`queue.py:322` `item.created_at = now`（入队即盖，＝进风控那一刻）；
档位 TTL＝`:324`；过期分支＝`_sweep` `:535-550`；审计腿＝`_note_expired` `:552`。
**起算点这条裁定被硬钉住**：`created_at` 在队列文件内只有四行——`:52` 是注释（⛔ 算读者）、`:123` 字段默认、`:322` 入队赋值（**唯一赋值点**）、`:557` 算龄。
尺＝`grep -rn "created_at" butler --include=*.py` 现读：本文件之外唯一的生产读者是 `tts/singleton.py:29`（审计腿取年龄），
其余命中全是别家表的同名列（`store/task_store.py`、`core/notification_store.py`、`core/decision_store.py` 等），与队列时钟无关。
而 `grep -n "retry\|requeue\|attempt" butler/tts/queue.py`＝**0 命中** ⇒ 队列里根本没有「重投复位时钟」这条路，
失败风暴下 TTL 照常到期（＝`:50` 要的行为）。

这处偏移⛔ 需要新裁：裁定⑤-1 原文（`:77`）已经写了目标态——「**一套队列归一**：风控只出『放行／丢弃』，排队交给 `tts/queue.py` 一家」。
⇒ 我把 TTL 落在 `tts/queue.py` ＝**提前站到了⑤ 的归一落点上**；若坚持字面落点（风控层做 TTL），前提是把 `_tts_queue` 变成真队列，
那正是⑤ 本体，本节⛔ 做（见 §12-6-1）。

### §12-2 三枚生产文件的指纹（基线＝HEAD `9aa5862` → 工作树，全 LF／CR 0；`tts/queue.py` 末行无换行＝保持原样）

| 文件 | 改前 | 改后 | 装了什么 |
|---|---|---|---|
| `butler/tts/queue.py` | `27396bbe09f8` 570 行／23,338 B | `af4b1d0676e5` 635 行／26,958 B | 档位表 `PRIORITY_TTL_S`（`:54`：告警 0＝不过期／高优 1800／标准 300，数值照抄裁定表，⛔ 我改）＋`PRIORITY_LEVEL_NAME`（`:60`，档位→`critical/warning/info`，与 `push_audit` 既有词表同一份口径）＋`_band_key`（`:67`，4/5 归标准带，与 `_band()` 同一条分带）＋`level_name_for`（`:76`）；`TTSQueueConfig.ttl_by_priority`（`:158`＝档位表的**可改副本**）＋`ttl_for()`（`:187`）；`enqueue_item` 显式 `ttl_s` 优先于档位表（`:324`）；`_sweep` 收过期项→`_dropped["expired"]` 计数→逐条 `_note_expired`（`:535-557`）；钩子异常只 `logger.warning`，⛔ 打断播放队列 |
| `butler/guard/push_guard.py` | `38304cb64d36` 401 行／17,032 B | `cdcc2dd1825f` 419 行／18,623 B | **删**那张「全树引用只有定义行本身」的 `PRIORITY_TTL` 空号表；文件头 L4（`:7`）、`DECISION_DROP` 注释（`:64`）、两处 docstring（`:253` 与 `record_ttl_drop` 上方）四处措辞改口并指到 `butler/tts/queue.py`；新增 `record_ttl_drop()`（`:352`）往现成的 `push_audit` 写一行 `drop`，job/trace 折进 `reason` ⇒ **⛔ 动 schema**（不新开建表腿，面板与 `/api/guard/audit` 已经在读这张表） |
| `butler/tts/singleton.py` | `fdad587fda83` 42 行／1,589 B | `b6d5d646f665` 72 行／3,122 B | `init_queue(..., on_expired=None)`（`:43`）默认装 `_audit_expiry_to_guard`（`:17`、`:55`）；**晚绑**＝每次现取 `getattr(get_runtime(), "push_guard", None)`（尺＝`app.py:87` 引 `init_queue`、`app.py:450` 才 `rt.push_guard = PushGuard(...)` ⇒ 构造期注入拿到的必然是 None，与裁定② 那份同病同治）；无 guard＝fail-open 静默返回，⛔ 因审计腿把播报带下去 |

（`wc -l` 对 `tts/queue.py` 报 569／635 差 1＝该文件末行无换行；本节与三枚落码器一律按 `splitlines(True)` 计数，两个口径都写在 `scripts/audit_1002/patch_dcd3_1002.py` 文档串里。
`queue.py` 的改后值经过两次重钉：`33ec90dd140d`（`patch_dcd3_1002.py`）→ `37afd1302452`（`:283`→`:281` 注释改口，行/字节⛔ 变）→ `af4b1d0676e5`（拆 `ttl_for_priority`，−5 行／−157 B）。）

### §12-3 红绿两半（TDD：⛔ 先看它红，⛔ 落码）

- 验收件＝`tests/test_audit_1002_batch11_dcd3.py` `cf602dbc94ad` 322 行／15,861 B，20 条腿 5 档
  （BandTtl 档位表／ExpiryAudit 钩子／GuardAuditTable 审计表回读／NoSecondDefinition ⛔ 第二份定义／SingletonWiring 接线）。
  运行时腿按跟办 3（`:108`）＝假时钟＋假 speaker，审计库写 `tempfile.mkdtemp()` 前缀 `dbt_batch11_`，⛔ 碰活库。
- **红半（对 HEAD 真旧码跑终版验收件，⛔ 改工作树）**＝`git archive HEAD butler tests scripts | tar -x -C /tmp/b11red`
  → `Ran 18 tests / FAILED (failures=5, errors=11)`；异常种类现读＝`AssertionError` 5／`AttributeError` 7／`TypeError` 4。
  红因逐类：档位表那几个名字还不存在（`ttl_for_priority`/`level_name_for`/`ttl_by_priority`/`on_expired`）、
  `record_ttl_drop` 还没有、singleton 不接钩子、guard 那张空号表还在、文件头那行还写着「本层做 TTL」。
  **两条腿在旧码上就是绿的**（如实登记，⛔ 「全红」才好看）：`test_standard_band_lives_300s`（旧队列所有档统一 300s，标准档恰好对）、
  `test_explicit_ttl_still_wins_over_the_band_table`（显式 `ttl_s` 旧码本来就赢）⇒ 本批新行为由另外 16 条腿证明。
  读数件＝`workorders/readings/1002b11/red_vs_head.txt`（终版腿数）与 `red_before_apply.txt`（落码当场那次，同组数）。
- **绿半**＝活树 `python3 -m unittest tests.test_audit_1002_batch11_dcd3` → `Ran 20 tests in 0.333s / OK`；
  回归同批＝批10 `Ran 22 / OK`、模块级 shim 闸 `Ran 20 / OK`。
- **变异 8 枚全被咬住**＝`scripts/audit_1002/mutate_dcd3_ttl_1002.py` → `BASELINE|rc=0|reds=0`、
  `SUM|mutated=8|not_bitten=0`、`RESTORED|rc=0`（跑完三枚生产文件 md5 回到 §12-2 改后值，⛔ 留 `.bak`）。
  八枚＝M1 档位表被 `config.ttl_s` 顶掉／M2 钩子不接／M3 告警档也给 TTL／M4 词表退化成 `str(priority)`／
  M5 4-5 归高优带／M6 singleton ⛔ 装默认钩子／M7 审计不落库／M8 guard 那张空号表复活。
  ⚠ 基线第三次重钉后**八枚全跑**（⛔ 只跑「受影响的那几枚」）：`workorders/readings/1002b11/mutation_final.txt`。

### §12-4 判例回身咬到自己：本批造了第二枚空号（`ttl_for_priority`）

写 helper 时我照着手感加了一枚「便利取值器」。**删前**那枚符号的覆盖面可从历史现读（不是口头）：
`git show 0f8b8fb:butler/tts/queue.py | grep -n "ttl_for_priority"` ＝**只有 `:76` 定义行**（块体 `:76-78`，`:78` 是它自己的 `return`），
**定义文件之外 0 引用**（生产走的是 `TTSQueueConfig.ttl_for()` `:187`，`enqueue_item` `:324` 调它）——这正是裁定③ 在治的那一种病（「全树引用只有定义行本身」）。
**删后**现读：`grep -rn "ttl_for_priority" butler --include=*.py` ＝ **0 命中**（rc=1），注释与文档串里也没留名字。
处置＝**拆**（`scripts/audit_1002/patch_dcd3_del_zerocaller_1002.py`，一笔=一次可复跑：基线 md5＋锚点恰 1＋`ast.parse`＋CR 0＋末行换行不变＋行数 640−5）。
⛔ 选另一条出路「让 `ttl_for()` 回落改调它」＝把回落语义从「全局默认 `ttl_s`」改成「档位表」，那是产品语义变更，要裁才动。

- 先红：新腿 `test_this_batch_ships_no_new_zero_caller_symbol` 在未拆的树（`0f8b8fb`）上
  `Ran 20 / FAILED (failures=2)`，两枚红因各自点名（`[] is not true : ttl_for_priority 在 butler/tts/queue.py 之外零引用` ＋
  `True is not false : 符号还在＝我本批造的空号没拆`）；读数件＝`workorders/readings/1002b11/red_gone_leg_vs_head.txt`。
- 后绿：活树 `Ran 20 / OK`。
- 判据按判例给两条合法出路并都认：接上（定义文件之外 ≥1 引用）或拆掉（全树 0 引用）；
  闸里带分母自证一条 `assertGreater(len(src), 50)`＝扫描面塌了这把尺要红，⛔ 把「没测到」读成「通过」。

### §12-5 我这侧的两笔账（⛔ 让下一次再踩）

1. **探针测词不测语义**（第一版）：`assertNotIn("L4 TTL", pg_mod.__doc__)`——我把 L4 那行改成
   「执行在真正排队的 `butler/tts/queue.py`」之后，改口里仍带「L4 TTL」四个字，尺判红而码是对的。
   ⇒ 判据改成钉**那句原承诺**（「`L4 TTL 过期：按优先级设置不同 TTL`」＝本层在做，⛔ 出现）＋
   **改口必须指到真正执行的那侧**（`butler/tts/queue.py` 必须出现，⛔ 只删不指）。与「命中不等于方向」同族。
2. **§11 的落盘自量数字对不上，本节现读登记**（⛔ 回改 §11 已定稿的字）：§11 末尾写「施加后……80,042 字节」，
   而同一文件现读＝`bd8553169dc8`／663 行／80,035 B，`git show 9aa5862:` 同值 80,035 B、`git show f517305:` ＝ 69,761 B
   ＝§11 脚本里那枚 `BASE_MD5`（`7066f7d84756...`）对应的字节数 ⇒ 追加段实际 10,274 B，而 §11 那句自量对应 10,281 B，
   差 7 B 落在**追加段正文里**（文件本体在 9aa5862 之后无人再动）。最可信的解释＝那个数是 DRY 那一次打印的，
   之后正文又删过字，⛔ 重打自量。**处置＝本节⛔ 把「施加后」的字节数／段数写进正文当 claim**（写了就会被下一次编辑作废）：
   最终数只指两处——本笔 commit 正文的 `APPLIED|` 行，与施加后当场 `wc -c`／`grep -c "^## "`／`grep -c "^### "`／二进制 CR 四把独立尺的读数。

### §12-6 本轮分母（三个数，⛔ 用「全过」代替）

| 口径 | 数 | 来源 |
|---|---|---|
| AST 里的 test 定义总数 | 841＝类内 741＋模块级 100（＝§11-5 的 821＋本批 20） | `scripts/audit_1002/ast_test_census_1002.py`，`FILES_SCANNED` 64 档 |
| 本宿主机可收集＝实跑 | 631（＝611＋20） | `python3 -m unittest discover -s tests -t .` 的 `Ran=`（`workorders/readings/1002b11/discover_final.txt`） |
| 类内 − 实跑 | 741−631＝**110** | 与 §11-5 那格同号＝本批没新增「宿主导不进来的档」；模块级那 100 条另由 `tests/test_v25_pytest_shim.py`（`Ran 20 / OK`）跑 |
| 红 | `FAILED (errors=10, skipped=9, expected failures=1)`；`^FAIL:`＝0 | `ModuleNotFoundError` 恰 10 行＝starlette 8＋pytest 2（环境因，⛔ 记成代码缺陷）；容器侧有 starlette、无 pytest |
| 契约门／仓根 gates | 宿主 `rc=2`＝homesdk 未装（`workorders/readings/1002b11/gates.txt`） | 环境缺口，⛔ 记成本批缺陷；本批三枚文件全在 `butler/` 自家，⛔ 触及跨仓面 |

### §12-7 本节⛔ 含的（留给裁⑤／跟办 2／独立议题）

1. **两套（其实是三套）队列⛔ 归一**：`butler/tts/playback_queue.py` 里 `grep -n "ttl\|TTL\|expires"`＝**0 命中**
   （第二条队列完全没有 TTL），加上 `PushGuard._tts_queue` 这口计数器（`:74` 那句「实际是**计数器**、『入队等待』从不等待」）＝三家并存，而裁定⑤
   那三条（`:77-79`：一套归一／丢最低优但**保 ALERT/critical**且**当前这条不吞**／两处阈值与暂停时长常量并一处）本节一条都没做，只在**其中一家**装了过期分支。⇒ 批12＝⑤。
2. **档位数值本身**（0／1800／300 照抄裁定表）与「观影模式 `tts_emergency_only=False`、离家 `skills_allowed=none`」
   那类规则数值同属「改表＝产品语义变更」，§11-6 已递四问，⛔ 二次投递。
3. **`tts/helper.py:20` 的 `override_quiet=True` 是默认值**：队列那层夜间静默对经 helper 的调用方形同虚设，
   与⑤ 同域（§11-6 顺带登记过，⛔ 占裁定名额）。
4. **跟办 2**（`trigger_cooldowns.json` 进 SQLite）＝`:107` 明写「确认是**欠执行**⛔ 待裁，排进下一批」⇒ 本节没做，
   这笔账还挂着；**跟办 1**（`security_level`）＝`:106` 独立议题，已随 §11-6 问 4 递出。

### §12-8 复跑（命令原文，⛔ 凭手感重述）

```text
cd /vol1/1000/docker/doubao-butler        # 权威树＝NAS，⛔ E 盘镜像（生产码面已过期）
python3 -m unittest tests.test_audit_1002_batch11_dcd3     # 批11 验收 20 例
python3 -m unittest tests.test_audit_1002_batch10_dcd2     # 批10 回归 22 例
python3 -m unittest tests.test_v25_pytest_shim             # 模块级 shim 闸 20 例
python3 scripts/audit_1002/mutate_dcd3_ttl_1002.py         # 变异 8 枚，自动还原＋md5 复验
python3 scripts/audit_1002/ast_test_census_1002.py         # 分母那把尺
python3 -m unittest discover -s tests -t .                 # 宿主全量，三个数
# 三枚一次性落码器：锚点已施加过，重跑必 raise（raise＝它对，不是坏）
python3 scripts/audit_1002/patch_dcd3_1002.py
python3 scripts/audit_1002/patch_dcd3_linefix_1002.py
python3 scripts/audit_1002/patch_dcd3_del_zerocaller_1002.py
# 红半复现＝对 HEAD 真旧码跑终版验收件（⛔ checkout 工作树）
rm -rf /tmp/b11red && mkdir -p /tmp/b11red
git archive HEAD butler tests scripts | tar -x -C /tmp/b11red
cp tests/test_audit_1002_batch11_dcd3.py /tmp/b11red/tests/
cd /tmp/b11red && DATA_DIR=/tmp/b11red_data python3 -m unittest tests.test_audit_1002_batch11_dcd3
```

（本节⛔ 测到的：三枚文件的源码形状＋20 条腿＋8 枚变异腿＋分母三数。本节⛔ 测不到的，三格一律标「未量」，⛔ 写成绿：
① **现网未生效**＝三枚 .py 在容器挂载面上还没跑过一条真实播报（`butler -> /app/butler` 是 bind mount，`.py` 落盘＝已落码，
生效要一次授权 `docker restart`；`up -d` 会丢可写层热修，⛔），「夜里那条旧提醒不补播」这件事现在只有宿主假时钟证据；
② **`push_audit` 里真出现过一行 ttl drop**——只有现网跑过才有那行，我这边是对临时 sqlite 现读，⛔ 对现网；
③ 档位数值在真实播报节律下会不会把该播的过期掉（数值照抄裁定表，⛔ 我按现网调，要裁）。
读数件＝`workorders/readings/1002b11/`（施加后另加 `ledger_after.txt`；名单以该目录 `ls` 现读为准，**本节点名非全集**——
已点名：`red_before_apply.txt`／`red_vs_head.txt`／`red_gone_leg_vs_head.txt`／`red_zerocaller_leg.txt`／
`green_final.txt`／`green_after_apply.txt`／`mutation.txt`／`mutation_recheck.txt`／`mutation_after_linefix.txt`／`mutation_final.txt`／
`census.txt`／`census_final.txt`／`discover_host.txt`／`discover_final.txt`／`gates.txt`），该目录 `??` 未跟踪＝⛔ 无 git 托底，
所以本节正文每个数都另有一条我已跑命令的输出行，⛔ 依赖这些文件存活。
落盘自量：施加＝append-only，脚本 assert「旧文本是新文本的前缀」；施加前＝
`bd8553169dc88dea31651ae8d979d7aa`／663 行／80,035 B／CR 0／`^## ` 11 段＋`^### ` 19 段（这四格数⛔ 会随本节追加而变，
是**追加前**的定值，写死安全）；追加后的字节数／段数⛔ 写进本节（§12-5-2 那条 7 字节教训），只指两处＝
本笔 commit 正文的 `APPLIED|` 行与 `workorders/readings/1002b11/ledger_after.txt`（`wc -c`／`grep -c`／二进制 CR 三把独立尺同批打）。
最终字节的 md5 与落盘戳只出现在本笔 commit 正文的 `date -u` 原样行（取于本文件最后一次写入之后）。
本仓⛔ 戳尺 ⇒ 这两腿判「人工-无尺」，与 §8／§9／§10／§11 同口径。）
