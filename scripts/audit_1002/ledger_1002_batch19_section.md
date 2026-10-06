## §20 批19 · `get_conn` 的启动期竞态：判定挪进锁内＋先建表后发布＋建表失败关连接（表行 3／24 前半，P0-2 三现象拆判，2026-10-03）

### §20-0 本节怎么读（先给三句话）

一句活：本批改**一处生产码**——`butler/store/db.py` 的 `get_conn()`。旧形制是「`if _conn is None` 判定在锁外 ⇒ 并发首调各建一条、先建的那条被覆盖后没人关」＋「`_conn = c` 在 `_init(_conn)` **之前** ⇒ 建表／ALTER 期间别的线程已经能拿到一张『表还没建完』的连接」。
新形制：双检、建连、`_init`、发布**全收进 `_lock`**，`_init` 抛错则 `c.close()` 并保持 `_conn = None`（下次重跑建表），连接发布后走**无锁快路径**（热路径⛔ 排进全局锁——现网多模态 P95 已 6,259ms，这条是 §20-3 的护栏腿之一）。
状态＝**已落码-未生效**（与 §17-8／§18-0／§19-0 同一张窗单，唯一路径仍是那一次经授权的 `docker restart`；⛔ `up -d`，会丢可写层热修）。本节每个数都另有一条已跑命令的输出行，出处集中在 `workorders/readings/1003b19/`（该目录 `??` 未跟踪＝⛔ 无 git 托底，只在 §20 这一处按名字引用；小节号以本节为准，最后一段最新）。

### §20-1 报告坐标（E 盘原文现读；表行号 = `_缺陷汇总_供审阅.md` 该行行号 − 20）

来源＝第 12 份报告 `doc/审计报告/doubao-butler-稳定性功能审计报告.md`（E 盘现读 36,303 B／740 行／CR 0，见 §19-11），其 P0-2 在 `:78`，自述快照 `836df79` 在该件 `:3`。
**那条快照在权威树查无此号**（三腿各一次，读数件 `workorders/readings/1003b19/p02_snapshot_commit_probe_run2_labeled.txt`，618 B／13 行，`STAMP 2026-10-02T21:01:12Z epoch=1790974872`）：`git cat-file -t 836df79` → `fatal: Not a valid object name`（rc=128）、`git rev-list --all \| grep -c "^836df79"` → **0**、`git log --oneline -1 836df79` → `unknown revision`（rc=128）。⇒ 本批⛔ 按它的短号定位，一律按「文件＋符号＋上下文」。

| 汇总表行 | 汇总文件行号 | 原文报告号与位置 | 原文坐标 | 一句话 |
|---|---|---|---|---|
| 3 | :23 | P0-2 现象①②（`:78`） | `butler/store/db.py:39`（判定在锁外）／`:57-61` | 并发首调各建一条＋半初始化连接对外可见（`no such table: dialog_turns`） |
| 24 | :44 | P1-7 (RO)／M-11 (RB)；P0-2 现象②的「坏连接永久缓存」半 | `store/db.py:29-31` 等 9 处 | `_conn` 先缓存后 `_init` ⇒ `_init` 抛错则未建表的连接被永久缓存 |

两行与 P0-2 的三枚现象不是三件事：`p02_dedup_and_liveness_run1.txt` 的 Q1 现读——表行 3 与表行 24 在 `store/db.py` 上指**同一段代码**（旧 `:35-65`），9 处清单里其余 8 处是别的模块各自的全局单例、形状不同（本批只动 `db.py`，那 8 处逐枚判定见 §20-9）。

### §20-2 报告给的三味药，与已定根因不自洽（所以本批只落①②，③递 DCD）

报告现象③＝「并发 `execute`／`commit` 无保护」，它给的方子是 `threading.local` 连接＋`isolation_level=None`＋带重试的 `write()`。这三味与**我自己已定的根因**方向相反，根因判语引原文（`doc/写面复摆取证-20261001.md:144-149`，⛔ 我复述）：

> 全仓 18 处 `sqlite3.connect`，**10 条指向同一个 `butler.db`**、同进程；`store/db` 单例自己是受害者……全仓 **0 处显式 `BEGIN`／`isolation_level`** ⇒ 只可能是 python 隐式事务被漏掉的 `commit()` 悬住。

于是两条相反论证，各自带一把尺：

- 「线程本地连接」把 1 条连接变 N 条＝**可能悬住的事务个数**变大。尺＝`sqlite3.connect_sites=19`（其中 1 处同形假阳：`butler/find_speaker.py:6` 的 `ha.connect` ⇒ 真 18，与取证文书同号；`connect_with_isolation_level=0`）。
- 「自动提交」把 33 个「多条写共用一次 commit」的函数从一个事务变成 N 个事务＝跨语句原子性没了。尺＝`batched_write_sites(同函数 w_calls>=2*commit)=33`，名单 33 行逐枚点名在读数件里（`store/db.py` 自己就占 5 枚）。
- `write()` 重试是把 `locked` 咽下去：`busy_timeout=5000` 已在做同一件事。

⇒ ③ 与既有写面停摆线同题（裁 C 自愈＝task #44 已在册），本批⛔ 按报告原样落地，改为递 DCD 裁收口路径（§20-7）。

### §20-3 验收：8 条腿，四遍先红（每遍各一份读数件，⛔ 覆盖）

件＝`tests/test_audit_1002_batch19_db_getconn_race.py`（现值 md5 `0ca1d6fd85b696b51f381b19f4d9b269`，357 行／16,432 B／CR 0；命名纪律＝全部 `unittest.TestCase` 方法，模块级 `test_*` 为 0）。八个名字（`grep` 自本件，⛔ 缩略）：
`test_init_runs_before_the_connection_is_published`、`test_concurrent_reader_never_gets_a_tableless_connection`、
`test_concurrent_first_call_builds_exactly_one_connection`、`test_init_failure_does_not_cache_a_broken_connection`、
`test_init_failure_closes_the_abandoned_connection`、`test_init_body_does_not_call_get_conn`、`test_no_lock_body_calls_get_conn`、`test_second_call_does_not_take_the_lock`。

四遍 RED 各有原因，⛔ 我把「重跑」写成「重复」：`red_b19_getconn_run1.txt`（5 腿，最早那遍）→ `…run2_sevenlegs.txt`（补派生腿后 7 腿）→ `…run3_sevenlegs_whyfix.txt`（runner 的失败原因段用 `sed -n '/^===* FAIL/,/^Ran /p'` 抓到 **0 块**：unittest 的 `=====` 与 `FAIL:` 不在同一行；改 Python 分块器后重跑，⛔ 引用旧遍）→ `…run4_eightlegs.txt`（`STAMP 2026-10-02T20:33:07Z`，**本节引用的那一遍**：`Ran 8 tests`／`FAILED (failures=5)`＝5 枚腿红，断言消息按前缀去重是 **4 类**（`8 != 1`、`<sqlite3.Connection object…`×2 枚腿、`True is not false`、`True is not False`），⛔ 我原本要写成「5 个不同消息」——现读只有 4 类，两枚腿的措辞只差一个大小写）。
GREEN＝`green_b19_getconn_run1_eightlegs.txt`（`STAMP 2026-10-02T20:35:00Z`，`Ran 8 tests in 1.144s`／`OK`）。

两枚⛔ 靠 sleep 赌时序：「建表前不对外」＝读者线程从 `_init` **内部**起、并另外记下 `get_conn` 返回那一刻建表是否还在跑（两个信号）；「只建一条」＝在 `connect` 里等满 THREADS 枚到齐（最多 0.6s），并发是被强制出来的。
三枚**护栏**（改前改后都绿，⛔ 指望它们先红）：守「把 `_init` 挪进 `_lock`」这一步不自死锁（`_lock` 是 `threading.Lock`＝非重入）、`_init` 体内⛔ 调 `get_conn`、热路径⛔ 因此加上抢锁。

### §20-4 落码与一次性补丁器（含一次我自己踩的静默 0）

补丁器＝`scripts/audit_1002/patch_b19_db_getconn_1002.py`（md5 `67c8005471517993487548053d9beae2`）。目标基线现读 `butler/store/db.py`＝`5754194f3548a795d04fdb89c04e042d`／593 行／17,558 B／CR 0；闸的顺序照批18：ALREADY_SCAN（标记 `# 双检：等锁期间可能已由别人发布` 已在＝直接 raise）→ CR==0 → ENDNL → 行数 → 字节 → md5 → 切片逐字节相等 → `compile()` → **结构闸**（AST：锁体内⛔ 调 `get_conn`、`_init` 行号 < 发布行号、锁体内恰好一条 `_conn =`、`c.close()` 在场、双检恰好一条、无锁快路径在 `with` 之前）→ 写 → WRITE_VERIFY（md5 变、CR 0、标记 ×1、`593-31+30==592`、再 parse）。
产物现值＝`7dcf301fed05450166716f4d47982dbf`／592 行／18,525 B／CR 0。
**一次性亲证**：先在 `/tmp/b19_dry` 的复制树上跑一遍（`rerun_rc=1 PATCH_FAIL|ALREADY_SCAN`）才敢碰现树；碰之前再取一次现树 md5＝`5754194f…`（⛔ 未验证就施加）。

过程缺陷一枚（差点造出假绿）：结构闸我第一版写成 `for n in ast.walk(body)`，而 `body` 是**列表**——`ast.walk` 吃列表时**一个节点都不产出** ⇒ 那些计数全为 0 ⇒ 每个「==0」的闸**空过**。改法＝`walk_block()` 生成器逐个 walk 元素，并把这条失败模式写进注释。这把尺的教训与「0 命中先怀疑 0」同源：**闸的 0 与判据的 0 一样，要先证它会咬。**

### §20-5 变异探针：6 枚真变异体全被咬，4 枚等价对照全存活

件＝`scripts/audit_1002/probe_b19_mutation_1002.py`（md5 `3e99e83df0d52b6bc530777347965384`），跑在现树、每次改回基线后校验 md5 复原。
`run1`（`mutation_b19_run1.txt`）**中途崩在 COVERAGE 行**：`KeyError: 'expect'`——等价对照的字典没带期望值；那遍仍打出 6 BITE／4 SURVIVED，我**留着它**当崩溃证据⛔ 当结论。
`run2`（`mutation_b19_run2_census.txt`，`STAMP 2026-10-02T20:38:37Z`，`rc=0`）才是引用那遍：`CENSUS|real=6 bitten=6 controls=4 survived=4 misbite=0 restore_bad=0`，`EXTRA_RED|无`。
真变异体（行切片注入）＝M1 去双检（47-48）、M2 先发布后建表（57-62）、M4 抛错不关连接（60）、M5 去无锁快路径（44-45）、M6 锁体内调 `get_conn`（56）、M7 `_init` 体内调 `get_conn`；等价对照＝C1 只改 docstring（42）、C2 PRAGMA 顺序（54-55）、C3 局部改名 `s`→`cfg`（49-51）、C4 日志文案（63）。⛔ 把等价对照计入缺口——它们**必须**存活。

### §20-6 普查尺的三遍对账（＋一把尺中途加了一格）

尺＝`workorders/tools/1003b19/count_b19_atomicity_sites_1002.py`。指纹血统：`98e253844e6ea9f0…`（run1／run4 用的那版）→ `bc3e704888ba18a6…`（现版，5,314 B／117 行／CR 0，只加了 `rollback_sites` 一格＋每条打 `recv=`／`src=`）。主读数件＝`atomicity_sites_run6_stamped.txt`（5,952 B／94 行，`STAMP 2026-10-02T20:50:39Z epoch=1790974239`）。
三遍对账（run1 改码前／run4 改码后／run6 改码后＋rollback 格）：`files=220`、`258/92`、`connect_sites=19`、`isolation=0`、`batched=33`、旁尺档 `24` **六格同号**；33 条 BATCH 名单里唯一变动＝`store/db.py` 那 5 枚行号各 **−1**（`87/114/132/505/524` → `86/113/131/504/523`），差值正等于本批那次替换的净行数（31 行换 30 行）＝读数跟着补丁走。
`run2/run3` 两遍**作废**（不是"没跑"，是取数姿势坏）：ssh 双引号里 `cut -d' '`／`awk $1` 被某一层吞掉，`db_md5=` 一格空、另一遍把 mawk 的 usage 打了进读数 ⇒ 改落成 `take_census_reading_1003.sh`（md5 `d120e03acba2b2022245d9bbcf8796da`，读数件名改成**必给参数**＝一次一名）。

新加那格的第一次现读就把我文书里的一句话打回：`rollback_sites=2`——`api/skill_routes.py:305 recv=v_mgr`＝**技能版本回滚**，`git log -S` 只命中基线 `c987e5a`(2026-09-19)；`store/txn_census.py:193 recv=obj`＝**事务回滚**，是裁 C 自愈件 `8ceecd5`(2026-10-01) 带来的。
⇒ 合并表 表行 24 那句「全树 0 处 rollback」**字面⛔ 成立**（版本回滚一直在），按本意（事务回滚）只在 `8ceecd5` 之前成立；真欠的是那 33 处批写各自 `except` 分支里一处都没有。这条既已写进 §20-7 的决策申请，⛔ 再拿 0 当基线。

### §20-7 挂账件已投递（DCD inbox），本批⛔ 替 DCD 裁

申请件＝`doc/决策申请/20261002-DB-SQLite并发写面收口路径待裁-决策申请.md`，**现值** md5 `459bc301fb1a76016d99f9852dbf583d`／10,001 B／58 行／CR 0，戳 `2026-10-02T21:01:35Z`（epoch `1790974895`）。
它有一版**被我自己作废的前身**：`ef598bc15c49…`／9,628 B／戳 `20:56:44Z`——那版投递后我发现 `836df79` 那条断言只在对话里跑过命令、**没落过读数件**（文书里的一条数字指不到盘上输出行），补 §20-1 那三腿后重投。看到旧指纹的人请按现值对账，两版差＝那一格引用＋§二 的取数时间窗＋戳行，⛔ 判定内容变动。
三处同号（`md5sum` 现跑）：E 盘工作副本、`E:\NAS\关键决策部\inbox\`（DCD 收件）、权威树 `doc/决策申请/`；戳尺 `workorders/tools/receipt_stamp_check.py`（md5 `fc97a2dde86d…`）现跑 `VERDICT=PASS|fatal=0|advisory=0`，「差 -9s」＝戳早于落盘 9 秒＝方向对。
件里四选（A 动事务边界★★★★★／B 照报告原样★／C 只加重试★★／D 挂账等 `txn_census` 点名持锁连接★★★★），我的读法写的是「A 与 D 不互斥，若只选一个请先 D 再 A」。**在 DCD 落字之前，本批⛔ 动那 33 处**，也⛔ 把「我推荐 A」当成「已裁 A」。

### §20-8 收口回归：红名单回到同一身份

runner＝`scripts/audit_1002/run_b19_regression_1002.sh`（md5 `be0c544c91d7ea12f632b44a7255f511`，双侧同号），读数＝`regression_and_census.txt`（戳 `2026-10-02T20:39:47Z`）。
现读原样：`Ran 746 tests in 63.478s`＋`FAILED (errors=10, skipped=3, expected failures=1)`＋`skip_named=3 summary=skipped=3`＋`REDLIST_SAME`（红名单身份 md5 `9490b2e3085174ec213d6b34d0e0d01d`，⛔ 只比行数）。
批18 那遍是 `Ran 738`，差＝本批 8 枚；`errors=10`／`skipped=3`／`expected failures=1` **三数与批18 同号**（skipped 那三枚仍是指名的两条 `config_routes`＋一条 homesdk vendor 门，⛔ 新浮动）。
AST 分母三数对账：FILES `71→72`、AST_TOTAL `948→956`、IN_CLASS `848→856`、MODULE_LEVEL `100` **不变**（命名纪律守住＝我没往回归门里塞模块级函数）。
环境钉：4 把启动硬门键名从 `butler/config.py:300-326` 现场 grep＋`count==4` 闸（⛔ 手抄），值一律 `DUMMY_NOT_REAL`；`DATA_DIR=/tmp/b19_qa_data`；现网库 `data/butler.db` 跑前跑后 `1790972072 19378176` **同号**＝本批所有读数只写 `/tmp`。

### §20-9 本节在册的判定（销账只写到腿，⛔ 宣布整份报告清完）

| 对象 | 判定 | 凭据 |
|---|---|---|
| 表行 3（P0-2 现象①②） | **已修-落码未生效** | `db.py` 现值 `7dcf301f…`；GREEN `Ran 8/OK`；变异 6/6 咬 |
| 表行 24 前半（`_conn` 先缓存后 `_init`＝坏连接永久缓存） | **已修-落码未生效**（仅 `store/db.py` 那一枚；同清单另 8 处属别模块的全局单例，形状不同，⛔ 随本行一并销） | 派生腿 `test_init_failure_does_not_cache_a_broken_connection`／`test_init_failure_closes_the_abandoned_connection` |
| 表行 24 后半＋P0-2 现象③（并发 execute/commit） | **挂账-已递 DCD**（§20-7），⛔ 按报告原样落地 | 普查尺 33/19/0/92/258；根因引 `doc/写面复摆取证-20261001.md:144-149` |
| P0-3（`config_routes` 里 `logger` 未定义） | **已修-见 `639c17e`**（2026-10-02 15:05:55 +0800，`butler/api/config_routes.py | 3 +`） | `git show --stat 639c17e` |
| P1-1（该报告侧） | ≡ 表行 71，已在批18 落地（§19-11 已登记，本节只接指针） | §19 |
| 该报告余下 10 枚 P1／10 枚 P2 | **未分诊**（本批只点 P0-2／P0-3；整份 12 号报告的 P1/P2 走 task #66 那条分诊线） | §19-11 |

「⛔ 现网不可复现类」那只桶（台账 `:1548` 起的 B 桶）里，本批新增一格：P0-2 现象①②是**启动期竞态**，现网要复现得掐开机首秒的并发首调＝现网证据拿不到 ⇒ 我这侧只用构造树证明形状，⛔ 写成"现网已复验"。

### §20-10 我本批的过程缺陷（一次一名，写给下一批的我自己）

- **文书里承诺了一把还不存在的尺**：决策申请初稿写「`rollback_sites` 从 0 上升」，而那时尺⛔ 打这个键。修法＝先把键加进尺＋夹具亲证它会咬（合成树里 `conn.rollback()` 数出 1、`mgr.rollback()` 也数出 1 ⇒ 证明我新加的分支会走），再重取读数，最后把文书那句从「承诺」改成「引用」。副产：现读值 2⛔ 0，那句话本身也是错的。
- **粗粒度计数当判据**：同一格总数把「版本回滚」和「事务回滚」一起吃进来 ⇒ 一旦只报「rollback 落点变多了」就是假绿通道。改法＝尺对每条打 `recv=`／`src=`，判据改成「新增落点逐枚点名 `文件:行号`」。
- **否定式断言只在对话里跑过、没落读数件**：初稿那句「`git cat-file -t 836df79` = Not a valid object name」我确实跑过，但输出行⛔ 在任何文件里 ⇒ 文书里这条数字指不到盘上证据。补了三腿探针并落 `p02_snapshot_commit_probe_run1.txt` 后，那件又把 **grep 的 rc 标成了计数**（`prefix_hits=1` 其实是「rc=1＝无匹配」，字面读起来像"命中 1 次"＝方向完全相反）⇒ 作废重取为 `p02_snapshot_commit_probe_run2_labeled.txt`（13 行，每行标签写清是 rc 还是计数），run1 **留档不删**当这一格的证据。规矩：rc 与计数⛔ 共用一个键名；`grep -c` 与 `$?` 分别落两行。
- **戳位口径**：这类文书的戳原本落文末，而 `receipt_stamp_check.py` 只解析**首 12 行** ⇒ 它判 `NO_STAMP`（`checked=2 violations=2`，含已投递的 模式闸门 那份）。本件已改到文首并跑成 PASS；**已投递那份⛔ 回改**（收件方可能已按旧指纹对账，重写＝补丁与产物脱钩），只在 §20-10 登记这一格。⇒ 新规矩：投递前我自己先用那把尺跑一遍，"打了戳"必须有 `VERDICT=PASS` 那行。
- **ssh 双引号吞参数两次**（run2 空值／run3 把 mawk usage 打进读数），两遍都公开作废；同批还有一条 `git status --no-optional-locks` 我把旗标放错位置（应在 `git` 后）。⇒ 复杂远程逻辑一律落成脚本再 scp，⛔ 内联。
- **变异探针 run1 崩在出口那行**（`KeyError`）：出口打印的键与注入字典不同构。留档当证据、重跑取结论，⛔ 用崩溃那遍的读数写台账。
- 一条 `python -c` 里我自己写了 `bchr`（手误）⇒ 探测失败一次；这类只读测量优先用编辑工具落脚本。

### §20-11 下一批接什么（本节⛔ 宣布的任何"完成"）

- 等 §20-7 那件落字。裁 A＝33 处批写逐枚显式事务（一函数一腿、各自先红后绿）；裁 D＝`txn_census` 在部署窗点名持锁连接（＝task #43 那格，本件的 D 选项）。两件都要**一枚一枚开**，⛔ 一把梭。
- 12 号报告的 10 枚 P1／10 枚 P2 分诊（并入 task #66 那条线：第五轮／第六轮／动态实验与链路追踪／运行时验证与测试实证 四份也还没分诊完）。
- 表行 24 同清单另外 8 处全局单例的逐枚判定（形状与 `db.py` 不同，⛔ 套本批的腿）。
- §19-11 那几条继续挂着：`dialog.py` THINKING 段收尾收敛、P1-21 `_echo_until`、P2-16 `XiaomiEar._echo_cooldown` 跨模块写、P2-17 `spoken` 两形、P2-19 `mark_absent` 触发条件、P2-20 时区依赖、P1-23 那 16 处静默 `except: continue`。
- 生效面：仍是那一次经授权的 `docker restart`（§17-8／§18／§19 同一条腿）；⛔ push、⛔ 自主重启。

复跑一遍（四把尺，全在权威树，⛔ 写盘只写 `/tmp`）：

```
bash workorders/tools/1003b19/take_census_reading_1003.sh run7_recheck.txt     # 普查尺（一次一名）
bash scripts/audit_1002/run_b19_acceptance_1002.sh                             # 期望 Ran 8 tests / OK
python3 -B scripts/audit_1002/probe_b19_mutation_1002.py                       # 期望 CENSUS|real=6 bitten=6 controls=4 survived=4 misbite=0 restore_bad=0，rc=0
bash scripts/audit_1002/run_b19_regression_1002.sh                             # 期望 Ran 746 / errors=10 skipped=3 xfail=1 / REDLIST_SAME
python3 -B scripts/audit_1002/patch_b19_db_getconn_1002.py                     # 期望 raise ALREADY_SCAN（一次性件重跑必崩）
```

**落盘自量**：施加＝append-only，脚本 assert「旧文本是新文本的前缀」，只在 EOF 加一段。追加前基底现读＝
`e645894774d28097f591ba046da2d430`／1,969 行／214,675 B／CR 0／`^## ` 19 段＋`^### ` 99 段（追加前的定值，写死安全）；
本节文件的 md5／行数／字节数⛔ 在这里自量（写完即变＝自指），它们钉在 `scripts/audit_1002/patch_ledger_batch19_1002.py` 的 `SECTION_MD5`／`SECTION_LINES`／`SECTION_BYTES` 三枚闸里，跑一次即反证。
追加后的字节数／段数／最终 md5 与 `date -u` 原样戳⛔ 写进本节，只指两处＝本笔 commit 正文的 `APPLIED|` 行与
`workorders/readings/1003b19/ledger_after.txt`（`wc -c`／`grep -c "^## "`／`grep -c "^### "`／二进制 CR 四把独立尺同批打）。
本仓⛔ 戳尺 ⇒ 这两腿判「人工-无尺」，与 §8–§19 同口径。
