## §17 批16 · 「先消费结果、后判成没成」的三枚同型缺陷（表行 119／143／46）：顺序错＋无条件收尾＋非原子写（2026-10-03）

### §17-0 本节做的是什么，以及为什么这三枚必须一起做

三枚缺陷是同一个句型的三种写法：**成功还没判定，结果已经被消费掉了**。

- 表行 119（B-10／S2，`integrations/bark.py`）：合并推送的加密腿 POST 到⛔ 带 `/推送加密` 的地址（打的是基址），
  且 `for` 循环之外无条件 `_merge_cache.clear()` ⇒ 一条没发出去、缓存照样清零＝静默丢消息。
- 表行 143（M-19／S3，`integrations/memory_agent.py`）：`recall()` 先 `[-limit:]` 切片、后跳过 revoked ⇒
  最新那 `limit` 条里每有一条已撤销就少召回一条，且⛔ 往前回填。
- 表行 46（P1-15／P1，`butler/core/aliases.py`）：`_save()` 就地 `write_text` 全量覆盖 ⇒ 崩溃留半个 JSON，
  下次 `_load()` 落进 except 分支把别名库静默清零，无备份、无告警。

一起做⛔ 不是「顺手」，是因为三腿的修法必须落在同一条不变量上（**成功才消费；消费要能整批回滚**）：
拆成两批就会出现两套「成功」的定义，第二批又得先把第一批的定义读一遍。

### §17-1 报告坐标 vs 现读坐标（三枚各量两面：`git show HEAD`→/tmp 与 工作树，两把尺同批）

| 表行 | 报告写的坐标 | 落码前现读（HEAD＝`fd7eb73`） | 落码后现读（工作树） |
|---|---|---|---|
| 119 | `bark.py:174,191` | 加密腿 POST 在 **:175**（:174 是 `try:`）；无条件 clear 在 **:192** | URL 公式 `:44`、两腿共用 `:181`/`:189`、按组清理 `:201-203` |
| 143 | `memory_agent.py:239-249` | `recall` 全体 **:302-318**，切片 `:309`、revoked 检查 `:311` | 先滤后切 **:312-315**（谓词只此一处） |
| 46 | `aliases.py:31-33` | `_save` **:31-33**（报告坐标**准确**） | `:32-37`（本节 +4 行） |

⇒ 只有表行 143 的报告坐标**整体过期约 63 行**：照它读码会读到 `list_memories` 的尾巴（:300 才是 `recall` 的前一行）。
⇒ 表行 119 的两个号各差 1 行（:174→:175、:191→:192），属「指对了腿、数错了行」，⛔ 据此判报告错。
读数件＝`workorders/readings/1003b16/ledger_evidence.txt`（A 落码后／B 落码前两块，含 `git show` 与 `print_lines` 双尺）
＋`ledger_evidence_prefix.txt`（HEAD 版窗口 118-133／165-196／300-320／28-36）＋`ledger_evidence_landed.txt`（落码后窗口）。

### §17-2 验收先行：14 腿跑在**未修的码**上＝8 红 6 绿

`tests/test_audit_1002_batch16_order_and_clear.py`（新建，322 行／15,491 B／LF／md5 `6d17003edc022b5b2f6c9fbeaba17aa8`）。
首跑 `Ran 14 tests in 0.031s / FAILED (failures=8)`、`unittest_rc=1`，基底 md5 三件均为**未改**值（`494327b43bf4`／`875c7e04a59e`／`b06068afd4df`）。

红 8 腿（＝缺陷本体）：`test_encrypt_leg_posts_to_encrypted_endpoint`／`test_http_error_keeps_cache_for_retry`／
`test_post_exception_keeps_cache_for_retry`／`test_success_clears_only_that_group`／`test_push_and_flush_share_one_encrypt_url`／
`test_recall_backfills_when_newest_slice_is_revoked`／`test_save_does_not_write_in_place_on_target`／`test_replace_crash_keeps_previous_content`。

绿 6 腿＝**笼头对照**（本就该绿，防我把修法写过火）：`test_plain_leg_posts_to_push_endpoint`（明文腿⛔ 被我顺手改成 POST 基址）、
`test_successful_flush_clears_and_counts`（成功仍要清、仍要计数）、`test_recall_never_returns_revoked_content`、
`test_recall_output_capped_at_limit`（修回填⛔ 变成放行超发）、`test_save_leaves_no_temp_residue`、
`test_saved_file_roundtrips_unchanged_format`。首跑读数里 failures=8／errors=0（＝8 红全是断言失败，⛔ 一条是 harness 崩）。

### §17-3 落码＝一笔 one-shot 施加器，3 个文件，逐文件保血统，重跑必 raise

`scripts/audit_1002/patch_b16_order_clear_1002.py`（`PLAN|`→`APPLIED|`，六行逐文件）：

- `butler/core/aliases.py` 118→122 行、4,283→4,539 B、CR 0→0、funcs 9→9 → md5 `ba541ed3d69aa7479629e07592d28009`
- `butler/integrations/bark.py` 196→207 行、8,490→9,052 B、**CR 196→207（全 CRLF 血统）**、funcs 6→7 → md5 `33805cd879c124f904003638b13982d7`
- `butler/integrations/memory_agent.py` 508→512 行、23,493→23,716 B、**CR 508→512（全 CRLF）**、funcs 42→42 → md5 `22e90f1bd83ce6276085e5952ddc7a30`

施加 `apply_rc=0`；GREEN `Ran 14 / OK`。落码器门序本身是一枚坑：第一版把 `ALREADY_APPLIED` 排在 md5 校验**之后**，
于是重跑报的是 `BASELINE_DRIFT|butler/core/aliases.py|got=ba541...|want=b0606...`——门在但走不到（批14 同款）。
调序后 `--apply` 与干跑各重跑一次，两条读数都是 `ALREADY_APPLIED|butler/core/aliases.py`、`rc=1`（`rerun_rc=1`／`dryrun_rc=1`）。
两条 raise 读数都留档（`apply_and_green.txt`／`rerun_raise_and_mutation.txt`），⛔ 只留调序后那条好看的。

### §17-4 三条口径决定（写下来，免得下一个人当手感）

1. **M-19 定性为「召回条数不足」，⛔ 定性为「泄露已撤销内容」**。落码前那段是先切后滤，revoked 的正文仍被 `:311` 挡掉，
   所以报告里「可能取最旧」这半句我按「条数不足＋不回填」入账；方向由打出来的那一行说（`if m.get("state") == "revoked": continue` 在切片**之后**）。
2. **合并缓存「按成功的组保留」，⛔ 「整批清零」也⛔ 「单条尝试即弃」**。安全性来自两处既有事实：`push()` 侧缓存有 100 条上限并 `pop(0)`
   （`:90-92`），`app.py:653-655` 的定时任务每轮重投 ⇒ 保留＝下轮再试。这与该文件自己 `:90-91` 的「宁多勿漏」fail-open 立场同向。
3. **revoked 谓词只留一处**（`:312-313` 的列表推导），内层 `continue` 删掉。两处判同一件事＝下一批改一处漏一处。

### §17-5 变异探针：8 枚全咬 ＋ 2 枚对照必须活（本批我自己这把尺先红了一次）

`scripts/audit_1002/mutate_b16_1002.py`，`CENSUS|mutants=8 bitten=8 controls=2 survived=无|restore_bad=无`、`probe_rc=0`；
逐枚腿名与咬住的测试名同行打印（`BARK_CLEAR_BACK|BITTEN|legs=3|...`、`ALIAS_REPLACE_BEFORE_WRITE|BITTEN|legs=4|...`）。
两处自曝：**（a）** 首跑 `SyntaxError: closing parenthesis ')' does not match opening parenthesis '[' on line 36`（我多打一枚右括号），
rc=1 读数原样留档；SyntaxError 在编译期，**没写盘**，三件基线跑完后逐枚回到 GREEN 值。修好后同一轮加了 `py_compile_rc=0` 这条腿。
**（b）** 等价对照 `survived=无` 的打印口径：脚本要求 `control_survived == control_total > 0` 才算过，两枚对照各自 `rc=0|ran=14`，
⛔ 计入缺口（改告警文案／删注释型变异本就该活）。
**意外的发现**（这条是判据，不是花絮）：`MA_SLICE_BACK` 同时咬住 `test_recall_never_returns_revoked_content`，
说明「先滤后切」这条腿里**单点谓词**才是承重墙——只把切片挪回去，泄露腿也会红。⇒ §17-4-3 那条「只留一处」⛔ 洁癖。

### §17-6 本节分母（三个数 ＋ 红名单**身份**核对，⛔ 用「全过」代替）

`bash scripts/audit_1002/run_b16_regression_1002.sh`（跑前 BASELINE_OK 四件齐）：
`Ran 709 tests in 56.544s`／`FAILED (errors=10, skipped=9, expected failures=1)`／`^FAIL:`=0／`^ERROR:`=10／
判据正则原文 `^(FAIL|ERROR): ` 同批打＝10。红名单（排序后清单整体）md5 `9490b2e3085174ec213d6b34d0e0d01d`＝期望值，
**与批14／批15 同值** ⇒ 本批⛔ 引入新的行为回归，10 条红全是既有的装载／seam 类（逐条名字在读数件里）。
AST 三数：`FILES_SCANNED|69`、`AST_TOTAL=919`＝`IN_CLASS=819`＋`MODULE_LEVEL=100`，`census_rc=0`；
919−709＝210 未收集，与批15 同值（模块级 8 个文件的逐档数在 `census.txt`）。
临时件残留自证：`TMP_REMOVED|/tmp/b16_redlist_4175047.txt`（删除与取证同一次调用内）。

### §17-7 批6 冻结锚点被本节的 +4 行挪位：`delete_alias` 84→88，两把尺同值才重刷

`aliases.py` 加 4 行后，`tests/test_audit_1002_batch6_gate.py` 的锚点尺当场判
`MOVED core/aliases.py AliasStore.delete_alias 84->88`——这是我自己那道闸在正常工作，⛔ 绕过、⛔ 让锚点红着进下一批。
重刷前用两把独立尺对账（AST 取 `def` 行号、`print_lines_1002.py` 打行号），同值 88 才落
`scripts/audit_1002/patch_b16_anchor_resync_1002.py`：干跑 → `APPLIED|...|md5=b3cc29c1c56b8e56c5eb3e411789b50d|lines=192|bytes=9210|cr=0` → 重跑 `ALREADY_APPLIED` rc=1。
批6 锚点尺单跑 `Ran 8 / OK`（`anchor_resync_and_regression.txt`）。

### §17-8 生效面：盘上已是新码，进程侧只认 `StartedAt` ⇒ 与批12／13／14／15 同一张窗单

`docker inspect doubao-butler` 现读（`ledger_evidence.txt` 尾段）：
`{"Type":"bind","Source":"/vol1/1000/docker/doubao-butler/butler","Destination":"/app/butler","RW":true}`
⇒ 权威树的改动**已经在容器的可见盘上**（`CONTAINER_GREP|butler/integrations/bark.py|_encrypt_url|count=3|rc=0`、
`memory_agent.py|alive|count=2`、`aliases.py|os.replace|count=2`）。
但 `Created=2026-09-30T01:54:28.459691892Z`、`State.StartedAt=2026-10-02T01:14:21.567841601Z`、`RestartCount=0`、`docker diff`＝3 行 `C` ⇒
**活进程比批12…16 每一笔都老**：状态一律「已落码-未生效」，唯一路径是那一次经授权的 `docker restart`（⛔ `up -d`，会丢可写层热修）。
⚠ 判读规则要写死：`docker exec python3 -c "import ..."` 读的是**盘**，⛔ 它当「已生效」的证据；生效后验收⛔ 用这条腿。

### §17-9 ⛔ 本批做的（登记，⛔ 混进本批提交）＋ 我这把尺测不到什么

- **非原子写兄弟落点**（⇒ 批17）：正则原文 `\.write_text\(json\.` 在 `butler/**/*.py`（219 个 .py）现读 **17 行／16 文件**；
  其中 5 行是「写 tmp→rename」的正解本体（`aliases.py:36`→`:37 os.replace`、`config_routes.py:37`→`:38`、`deps.py:57`→`:59`、
  `triggers/store.py:91`→`:92 tmp.replace`、`skills/store.py:159`→`:160`），另 12 行＝就地截断写。
  逐行分类（哪些真有清零后果）归批17，本节⛔ 宣布「12 处待修」这种结论。扫描根＝`butler/`，⛔ 全集（`scripts/`、`tests/` 未扫）。
- **硬编码 `/app/data`**：正则 `/app/data`（occ）现读 **28 次／23 文件** ⇒ 批17 与上一条同批点，因为改原子写就要选目标路径。
- `docker_tools.py:108` 那条恒假三元：命中为真、**方向为刻意**（`in` 而非 `not in`），判「⛔ 改」。
- `get_merge_cache_size`：符号可达性现读 `all=7|butler=1|tests=3`，那 1 处就是 `:206` 的定义本身 ⇒
  **生产侧调用者 0** ⇒ 被保留的合并缓存⛔ 有面板读数，运维只看得见新加的那条 warning 日志。要不要接进 `/api/status`，等裁。
- 本节改动的**语义副作用**：`recall()` 现在会把较旧的存活条目回填进 `limit` 配额（展示口径变了），
  合并通知延后一轮再投的时间语义（过载期后可能一次补投多组）⛔ 实测过，只按代码口径陈述。
- `TriggerEngine.status()` 的冷却展示契约、B 桶（⛔ 现网不可复现类）、表行 21 的 SSE 半、合并表只覆盖 7 份来源报告
  ——四条继续按 §15／§16 的指针挂账，本节⛔ 重述其数（那些数会随下一批变）。
- **测不到什么**：真断电发生在 `write_text` 与 `replace` 之间（我只证了「同进程异常」与「不就地截断」）；fsync 级持久性⛔ 主张；
  错误 URL 那半⛔ 打到真 Bark 服务验状态码（我只证「两腿共用一条公式」）；MA 侧排序契约⛔ 由我这侧证；
  `aliases` 的 tmp 名是定值（`device_aliases.json.tmp`）⇒ 多进程同写会互踩，本仓单进程写，风险登记给批17。

### §17-10 我这把尺／脚本本轮自抓的缺陷（每条都改了，读数留档）

1. `ALREADY_APPLIED` 门排在 md5 校验后＝走不到的死门（§17-3 已述，批14 同坑第二次）。
2. 锚点重刷脚本把兄弟落点校验写成 `count+1`＝一把**恒红**的门（该判据是「行数不变」，⛔ 加一行）。
3. 我在重刷脚本里手填过一枚 md5（`2b8d97b9...`）——没有对应的已跑命令＝无中生有，换成 ssh 现读的 `3f2278b7d6c3b33c3775c54925eea4b8`，
   并在脚本注释里把这次违规写明（⛔ 事后抹平）。
4. 全量回归一度用内联 ssh 串跑，嵌套 `"` 被 bash 判 `unexpected EOF`，读数件根本没写出来 ⇒ 收口 steps 落成 `run_b16_regression_1002.sh`；
   同批自抓两处：`TMP_REMOVED|rc=$?`（拿到的是 echo 的 rc＝常量）、`census_rc=$?` 排在 `head` 之后（管道尾 rc）。
   `date -u 'stamp_utc=%Y...'` 少了 `+`＝`invalid date`，本轮第一次跑就红给我看了（读数件里的戳一律取 `date -u` 原样行）。

### §17-11 复跑（命令原文，⛔ 凭手感重述）

```
cd /vol1/1000/docker/doubao-butler
python3 -B -m unittest tests.test_audit_1002_batch16_order_and_clear          # 期望 Ran 14 / OK
python3 -B scripts/audit_1002/patch_b16_order_clear_1002.py --apply            # 期望 raise ALREADY_APPLIED|butler/core/aliases.py rc=1
python3 -B scripts/audit_1002/mutate_b16_1002.py                               # 期望 CENSUS|mutants=8 bitten=8 controls=2 survived=无|restore_bad=无，rc=0
bash scripts/audit_1002/run_b16_regression_1002.sh                             # 期望 Ran 709 / ^FAIL:=0 / ^ERROR:=10 / 红名单 md5 9490b2e3085174ec213d6b34d0e0d01d
python3 -B scripts/audit_1002/patch_b16_anchor_resync_1002.py                  # 期望 raise ALREADY_APPLIED|tests/test_audit_1002_batch6_gate.py
python3 -B scripts/audit_1002/probe_b16_ledger_1002.py                         # 本节每个行号／计数的出处
bash scripts/audit_1002/run_b16_prefix_windows_1002.sh                         # 落码前窗口（git show HEAD→/tmp，⛔ checkout 工作树）
```

读数件＝`workorders/readings/1003b16/`（`red_batch16.txt`／`red_batch16_head.txt`／`apply_and_green.txt`／
`rerun_raise_and_mutation.txt`／`green_batch16_full.txt`／`mutation_probe_b16.txt`／`discover_full.txt`／`census.txt`／
`regression_and_census.txt`／`anchor_resync_and_regression.txt`／`ledger_evidence.txt`／`ledger_evidence_prefix.txt`／`ledger_evidence_landed.txt`），
名单以该目录 `ls` 现读为准、**本节点名非全集**，最后一段最新；该目录 `??` 未跟踪＝⛔ 无 git 托底 ⇒ 本节每个数都另有一条我已跑命令的输出行。
⚠ 路径事实沿用 §16-10：`doc/审计报告/`（现读 12 件）只存在于 E 盘工作副本，**权威树里没有这个目录** ⇒ 表行原文（46／119／143 三条，
出自 `_缺陷汇总_供审阅.md` 的 :66／:139／:163）从 E 盘读，**代码**一律从权威树读；台账本体只在权威树。
落盘自量：施加＝append-only，脚本 assert「旧文本是新文本的前缀」；追加前基底＝
`72be96426fd356f1f228c2cef4a78011`／1,431 行／160,565 B／CR 0／`^## ` 16 段＋`^### ` 63 段（追加前的定值，写死安全）；
追加后的字节数／段数／最终 md5 与 `date -u` 原样戳⛔ 写进本节，只指两处＝本笔 commit 正文的 `APPLIED|` 行与
`workorders/readings/1003b16/ledger_after.txt`（`wc -c`／`grep -c "^## "`／`grep -c "^### "`／二进制 CR 四把独立尺同批打）。
本仓⛔ 戳尺 ⇒ 这两腿判「人工-无尺」，与 §8–§16 同口径。
