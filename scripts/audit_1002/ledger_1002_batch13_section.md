## §14 DCD 跟办 2 落地 · 批13 触发冷却 JSON→SQLite（一次性快照迁移＋验过才退役）（2026-10-02）

### §14-1 裁定来历与范围（⛔ 再问「要不要做」）

| 层 | 原文位置 | 内容 |
|---|---|---|
| 裁定 | `E:\NAS\关键决策部\decisions\20260928-豆包管家15项决策.md:50` | 「triggers 冷却用 JSON 文件，其他用 SQLite。裁定：统一进 SQLite。」 |
| 判性 | `decisions\20261002-DB六件影子代码-裁定.md:107` | 「确认是**欠执行**⛔ 待裁，排进下一批」 |
| 取证口径 | 同上 `:108`（跟办 3） | 行为类判据要有**运行时腿**（真临时 sqlite／假播报器／假时钟），⛔ 只 grep |

本节就是那句「下一批」。范围钉死在跟办 2 一条：一枚新表 + 两枚 repo 函数 + 一截 engine 迁移腿。
⛔ 新增配置项、⛔ 新增 reason code、⛔ 顺手改 §14-6 那枚假绿（先登记再单独判）。

### §14-2 落码清单（形状＝现读，尺＝`md5sum`／`wc -l`／`wc -c`）

| 文件 | md5_12 | 行 | 字节 | 本批动作 |
|---|---|---|---|---|
| `butler/store/db.py` | `5754194f3548` | 593 | 17,558 | 新 `_TRIGGER_COOLDOWNS_DDL`（`cd_key TEXT PRIMARY KEY, last_fired REAL NOT NULL`）＋`_TRIGGER_COOLDOWNS_IDX`＋`_ensure_trigger_cooldowns()`；挂在 `_init` 里 `_ensure_trigger_evaluations(c)` 之后一行（`:488`），走独立受防 DDL ⛔ 挤进那条 `executescript`（同 §13 格2 的理由：老库上那条脚本全是 no-op，新表 CREATE 在现网库是真写） |
| `butler/store/repo.py` | `84013fcc7013` | 673 | 25,246 | `COOLDOWN_RETENTION_S = 86400*7`（旧 JSON 时代原值，⛔ 悄悄改宽改窄）＋`load_cooldowns()`＋`save_cooldowns()`（`DELETE … last_fired<?` 与 upsert 同批；`INSERT OR REPLACE` ⛔ 换 `ON CONFLICT DO UPDATE`＝后者要 SQLite>=3.24，现网镜像库版本我没现读过） |
| `butler/triggers/engine.py` | `307424a3555e` | 533 | 26,960 | `_load_cooldowns()` 改读库（失败＝loud＋进台账＋保住进程内状态）、`_import_legacy_snapshot()`（只读一次，验过才退役）、`_save_cooldowns()` 改写库；`set_runtime` 传 `data_dir/trigger_cooldowns.json` 只当迁移源 |
| `tests/test_audit_1002_batch13_cooldown.py` | `c9dfeb85b07f` | 398 | 20,772 | 验收 24 例（22＋§14-4 补的两枚） |
| `tests/test_write_failures.py` | `bef41b7155ba` | 231 | 11,242 | `SITES` 收进三枚新落点（`triggers/engine.load_cooldowns`／`.save_cooldowns`／`.import_legacy_snapshot`），类 docstring 改成⛔ 写死条数 |
| `tests/test_trigger_evaluation_ledger.py` | `ff93809e1f57` | 273 | 13,506 | 模块级 `guard_body()`：守护函数体改由 AST `get_source_segment` 切，⛔ 手写行号梯子；另加两枚对照（`_ensure_trigger_cooldowns` 也必须 loud；全文件 warning 数须大于该函数体内的） |
| `scripts/audit_1002/classify_unawaited_1002.py` | `cd5134409898` | 672 | 27,773 | 在册坐标 `triggers/engine.py` 两条：`215 add_trigger_evaluation`／`474 add_trigger_run` |
| `scripts/audit_1002/mutate_b13_cooldown_1002.py` | `7a3eb15285bc` | 261 | 11,226 | 变异探针（14 枚，内存原件＋finally 逐字节还原＋md5 复验，⛔ .bak） |

一次性落码器（`scripts/audit_1002/`，名字即清单：`patch_b13_cooldown`／`_b13b_tests`／`_b13b_engine`／`_b13c_roster`／`_b13d_blanklines`／`_b13e_roster2`／`_b13f_missinglegs`），全部 DRY 先行、`--apply` 才写盘、重跑必 raise。⛔ 在这里写「共几枚」＝下一个补丁一进来那数就作废。

### §14-3 四条护栏各挂一条运行时腿（跟办 3 口径＝真临时 sqlite）

| # | 护栏 | 变异体 | 咬住它的腿 |
|---|---|---|---|
| 1 | 读库失败⛔ 折成「全员没冷却」：`repo.load_cooldowns` 直接抛，engine 接住＋进台账＋保住进程内状态 | `LOAD_SWALLOWS_TO_EMPTY`／`LOAD_FAIL_UNLEDGED` | `test_repo_load_cooldowns_raises_instead_of_folding_to_empty`／`test_every_registered_site_calls_record_inside_its_except` |
| 2 | 快照只读一次，且**验过才退役**（回读逐键对上才 `os.remove`；读不到／没写进去／有键没验到 ⇒ 留档，下次启动再搬，整段幂等） | `DELETE_WITHOUT_VERIFY`／`SNAPSHOT_NOT_MIGRATED`／`IMPORT_FAIL_UNLEDGED` | `test_unverified_import_keeps_the_file_and_the_in_memory_state`／`test_legacy_snapshot_entries_land_in_sqlite`／同上 |
| 3 | 同键取较新那个（更长的冷却＝⛔ 提前放行） | `STALE_SNAPSHOT_WINS` | `test_snapshot_never_overwrites_a_fresher_sqlite_entry` |
| 4 | 七天窗语义原样搬＋过期清理跟写同批（⛔ 只增账） | `LOAD_IGNORES_WINDOW`／`SAVE_NO_PRUNE` | `test_load_ignores_entries_older_than_the_retention_window`／`test_save_prunes_expired_rows_instead_of_leaving_them` |

另三枚与形状／旧形状残留对抗：`NO_TABLE_CREATED`／`EXTRA_COLUMN`（列集合＝裁定那两列，`cd_key` 必须主键）、`GUARD_IS_SILENT`（守护⛔ 静默）、`SAVE_NEVER_PERSISTS`、`SNAPSHOT_HANDLE_LEAK`。

### §14-4 变异探针两跑：12 咬 2 活 ⇒ 补两枚腿 ⇒ 14/14

第一跑（读数件 `workorders/readings/1002b13/mutation_probe.txt`）：`SUMMARY|run=14|bitten=12|survived=2`。两枚活下来的**不是产码缺陷，是验收缺口**，根因各一条：

1. `LOAD_SWALLOWS_TO_EMPTY` 活：原有那枚失败腿 `test_write_failure_is_loud_ledged_but_in_memory_survives` 是把 `repo.load_cooldowns` **换成会抛的假函数**再喂 engine——mock 掉的正是我要验的那层，所以「repo 本体到底抛不抛」零信息。补 `test_repo_load_cooldowns_raises_instead_of_folding_to_empty`＝把真表 `DROP` 掉让真 repo 撞真库，并断言抛的就是 `no such table: trigger_cooldowns`（⛔ 把「另一种错误」当证据）。
2. `STALE_SNAPSHOT_WINS` 活：原有快照腿只测「文件里的键进了库」＝正方向；反方向（库里更晚时旧快照⛔ 倒灌）没人测。补 `test_snapshot_never_overwrites_a_fresher_sqlite_entry`（进程内与库里两处各断一次）。

⛔ 这两枚冒充「先红后绿」的功能腿：它们盯的行为本批产码已实现，所以**红只能由变异体给**。第二跑（`mutation_probe_after_missinglegs.txt`）＝`bitten=14|survived=0`，且这两枚的 `legs=` 正是上面新加的两条名字；三枚产码文件 `RESTORED_OK` 回原 md5。

我这两把尺自己的两次红，都留了档：
- **恒真的分隔符尺**：补腿落码器第一版用 `\n(?!\n)    def test_` 数「def 前没空行」，那条先行否定看的是 def 前那个换行符后面是不是换行——后面永远是 4 个空格，于是在基线上直接数出 22＝全部＝一把永不咬人的尺。换成逐行看上一行；第二版又把 `class X:` 后面第一枚 def 误算成孤立（基线 3 枚，那是正常形制），再收窄到「上文必须是语句行」。出口前配自证：故意吃掉一枚空行 ⇒ `solo` 必须 0→1、`blank` 必须 −1，打印 `RULER_SELFTEST`。
- **管道末端 rc 当尺子的 rc**：第一跑探针我用 `… | tee 读数件; echo rc=$?`，取到的是 `tee` 的 0⛔ 脚本的（那时有 2 枚存活，脚本返回 1）。第二跑改重定向＋`echo probe_rc=$?`，`0` 这才对得上 `survived=0`。

### §14-5 现网面：已落码-未生效（四格现读，尺见附件）

| 格 | 读数 | 怎么读出来的 |
|---|---|---|
| 容器进程 | `doubao-butler` `StartedAt=2026-10-02T01:14:21Z`，Up 15 hours，uvicorn 单 worker（PID 3340384） | `docker ps --format` ＋ `docker inspect --format` ＋ `docker top` |
| 盘上产码 | `engine.py` mtime `2026-10-03 00:12:00 +0800`＝`16:12:00Z`，**晚于**进程启动 | `stat -c %y` ⇒ 本批 .py 落盘＝已落码，生效要一次授权 `docker restart`（`up -d` 会丢可写层热修，⛔） |
| 现网库 | `sqlite_master WHERE name='trigger_cooldowns'` ⇒ `[]`＝表此刻不在现网库里 | `docker exec … sqlite3 file:/app/data/butler.db?mode=ro`（只读 URI） |
| 旧快照 | `data/trigger_cooldowns.json` 390 B／9 键／值全 float；mtime `00:14:31.842568498 +0800` | `wc -c`＋`json.load` 只数键不抄值＋`stat` |

**那枚文件的写入者是活进程内存里的旧码，不是我的测试**：mtime 那一毫秒与容器日志 `trigger fired: decision_heartbeat`（`00:14:31.842`）对到毫秒级；而盘上产码已无该文件写点（全仓 grep `trigger_cooldowns.json`：只剩迁移读 `engine.py:134`、本批测试常量与文书/补丁正文）。我一开始怀疑是自己 16:14:32Z 跑的 `gates.sh` 写的——`gates.sh` 在 homesdk 缺失时于顶部就退出（rc=2，⛔ 跑到任何测试），且批13 验收把 `DATA_DIR` 钉在 tmp，排除。

⇒ **窗口动作次序（唯一路径）**：一次授权 `docker restart` ⇒ 新进程 `set_runtime` 读快照一次 ⇒ 写库 ⇒ 逐键回读验过 ⇒ `os.remove`。⛔ 在重启前手工删它：旧进程下一枚 fire 会整档写回，既白删一次又把迁移源弄丢。9 枚键⛔ 是我判的可用性——迁移腿按七天窗自己筛（`test_stale_snapshot_entries_are_not_imported` 钉的就是这条）。

写压对照（现读，⛔ 我推断）：`trigger_evaluations` 1h＝2 行、24h＝32 行；fire 路径本来就每火写一行 `trigger_runs`（`engine.py:465`），本批把冷却持久化挂在同一处（`:460`）⇒ 每次火最多多一枚事务，**没新增一类写压**。该处注意：隔离态跳过那支（`_only_terminal`）不碰 `_last_fired`，但仍会走到 `:460`——这与旧 JSON 每火整档重写同形，本批⛔ 顺手改成「脏了才写」（那是新语义，要判）。

### §14-6 顺带发现一枚假绿（本批登记，⛔ 混进本批提交）

`engine.py:525` 的 `status()` 读 `self._last_fired.get(trig["id"], 0)`，而 fire 侧写的是 `cd_key = trig["id"] + (":" + member if member else "")`（`:244`、`:358`）
⇒ **凡带 member 的触发器在 `/api/status` 里恒显示「没在冷却」**。尺＝同一份文件里两枚键形制并排（`grep -n "_last_fired.get\|cd_key"` 现读，行号 244/252/358/525）。
方向判定（⛔ 只报命中）：`:252` 用的是 `cd_key`（对），`:525` 用的是 `trig["id"]`（错），同一名词两种键形制。
最小真话修法＝该触发器名下所有键里取**最新的** `last_fired`（前缀匹配 `id` 与 `id:`），口径读作「最早可再火的时刻」；旧读法给出的是相反方向（恒 0＝随时可火）。这是显示形制选择，但不是「不能自主决定」那一类：现状恒 0 已经是假话，取较新一枚是⛔ 不撒谎的最小改动。批14 单开（验收先行：member 维触发器必须显示 remaining>0）。

### §14-7 本节分母（三个数，⛔ 用「全过」代替）

| 口径 | 数 | 来源 |
|---|---|---|
| AST 里的 test 定义总数 | 881＝类内 781＋模块级 100（＝§13-6 的 857＋本批 24） | `python3 scripts/audit_1002/ast_test_census_1002.py`（`workorders/readings/1002b13/census_after_missinglegs.txt`，`FILES_SCANNED|66`） |
| 本宿主机可收集＝实跑 | 671（＝647＋24） | `python3 -B -m unittest discover -s tests -t .` 的 `Ran=`（`discover_after_missinglegs.txt`，54.286s） |
| 类内 − 实跑 | 781−671＝**110** | 与 §11-5／§12-6／§13-6 那格**同号**＝本批没新增「宿主导不进来的档」；模块级那 100 条另由 `tests/test_v25_pytest_shim.py`（`Ran 20 / OK`）跑 |
| 红 | `FAILED (errors=10, skipped=9, expected failures=1)`；`^FAIL:`＝0；`ResourceWarning`＝0 | `ModuleNotFoundError` 计数＝starlette 8＋pytest 2（环境因，⛔ 记成代码缺陷）；本批跑完复采同数 |
| 契约门／仓根 gates | `gates_rc=2`＝homesdk 未装（`workorders/readings/1002b13/gates.txt`） | 环境缺口，⛔ 记成本批缺陷；本批改动全在 `butler/` 与 `tests/` 自家面，⛔ 触及跨仓面（该门在缺 homesdk 时于顶部即退出，⛔ 跑到任何测试——§14-5 排除它写那枚 JSON 时用的就是这条） |

### §14-8 复跑（命令原文，⛔ 凭手感重述）

```text
cd /vol1/1000/docker/doubao-butler        # 权威树＝NAS，⛔ E 盘镜像（生产码面已过期）
python3 -B -m unittest tests.test_audit_1002_batch13_cooldown      # 批13 验收 24 例
python3 -B -m unittest tests.test_write_failures tests.test_trigger_evaluation_ledger
python3 -B -m unittest tests.test_audit_1002_batch6_gate           # 在册坐标门（215/474）
python3 -B -m unittest tests.test_v25_pytest_shim                  # 模块级 shim 闸 20 例
python3 -B scripts/audit_1002/mutate_b13_cooldown_1002.py          # 变异 14 枚，自动还原＋md5 复验
python3 -B scripts/audit_1002/ast_test_census_1002.py              # 分母那把尺
python3 -B -m unittest discover -s tests -t .                      # 宿主全量，三个数
bash gates.sh                                                      # 仓根门：宿主 rc=2＝homesdk 未装
# 一次性落码器：锚点已施加过，重跑必 raise（raise＝它对，不是坏）
python3 -B scripts/audit_1002/patch_b13_cooldown_1002.py
python3 -B scripts/audit_1002/patch_b13f_missinglegs_1002.py
# 红半复现＝对 HEAD 真旧码跑终版验收件（⛔ checkout 工作树）；
# 注意：本节那半取证是在**代码提交之前**跑的，提交后 HEAD 已含新码 ⇒ 复现要换成 `HEAD^`
rm -rf /tmp/b13red /tmp/b13red_data && mkdir -p /tmp/b13red
git archive HEAD^ butler tests scripts | tar -x -C /tmp/b13red
cp tests/test_audit_1002_batch13_cooldown.py /tmp/b13red/tests/
cd /tmp/b13red && DATA_DIR=/tmp/b13red_data python3 -B -m unittest tests.test_audit_1002_batch13_cooldown
```

（本节⛔ 测到的：三枚产码文件的形状＋24 条腿＋14 枚变异腿＋分母三数＋现网那四格读数。本节⛔ 测不到的，四格一律标「未量」，⛔ 写成绿：
① **现网未生效**＝`.py` 落盘在进程启动之后，`trigger_cooldowns` 表在现网库里现读为不存在；旧行为今天还在现网跑着（每火整档重写 JSON）；
② **重启那一刻快照到底退没退**＝`os.remove` 那条腿只在临时库里跑过，真库＋真快照上没跑过（要一次授权 restart）；
③ **9 枚键搬进库后有没有触发器提前再响**＝未量（真机节律证据⛔ 等于临时库证据）；
④ **`status()` 那枚假绿**（§14-6）＝本节只登记＋定方向，没改码、没写腿。
读数件＝`workorders/readings/1002b13/`（`red_vs_head.txt`／`leak_leg_red.txt`／`green_batch13_22legs.txt`／`record_sites_ast.txt`／`mutation_probe.txt`／`mutation_probe_after_missinglegs.txt`／`discover_after_missinglegs.txt`／`census_after_missinglegs.txt`／`shim.txt`／`gates.txt`），名单以该目录 `ls` 现读为准、**本节点名非全集**，最后一段最新；该目录 `??` 未跟踪＝⛔ 无 git 托底，所以本节正文每个数都另有一条我已跑命令的输出行，⛔ 依赖这些文件存活。
落盘自量：施加＝append-only，脚本 assert「旧文本是新文本的前缀」；施加前＝
`305ec8958530ec36ecdadaa2388b23be`／970 行／114,500 B／CR 0／`^## ` 13 段＋`^### ` 35 段（这几格数⛔ 会随本节追加而变，
是**追加前**的定值，写死安全）；追加后的字节数／段数⛔ 写进本节（§12-5-2 那条 7 字节教训），只指两处＝
本笔 commit 正文的 `APPLIED|` 行，与 `workorders/readings/1002b13/ledger_after.txt`（`wc -c`／`grep -c "^## "`／
`grep -c "^### "`／二进制 CR 四把独立尺同批打）。最终字节的 md5 与落盘戳只出现在本笔 commit 正文的 `date -u` 原样行
（取于本文件最后一次写入之后）。本仓⛔ 戳尺 ⇒ 这两腿判「人工-无尺」，与 §8／§9／§10／§11／§12／§13 同口径。）
