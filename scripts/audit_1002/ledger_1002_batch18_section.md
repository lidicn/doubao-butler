## §19 批18 · 「抛错就静默／收尾漏了就一直卡在 SPEAKING」一族：worker 强引用 + 发声段收敛 + 过期 pending 不再喂生成器（表行 7／8／70／71，2026-10-03）

### §19-0 本节怎么读（先给三句话）

一句活：本批改**两件生产码**——`butler/tts/singleton.py`（队列 worker 的 Task 现在存进强引用注册表、死亡日志从 warning 升到 error）与
`butler/core/dialog.py`（SPEAKING 之后的收尾从 8 个各写一遍的出口收敛成一处 `_speak_transition`，其 `finally` 里恢复 WAITING／派生 IDLE；
`_return_idle` 派生的 task 也进强引用注册表；技能草稿 pending 过期分支补了 `return`，普通闲聊不再被当技能描述喂进生成器）。
状态＝**已落码-未生效**（与 §17-8／§18-0 同一张窗单，唯一路径仍是那一次经授权的 `docker restart`；⛔ `up -d`，会丢可写层热修）。
本节的每个数都另有一条我已跑命令的输出行，出处集中在 `workorders/readings/1003b18/`（该目录 `??` 未跟踪＝⛔ 无 git 托底，只在这一处按名字引用）。

### §19-1 报告坐标（E 盘原文现读；表行号 = `_缺陷汇总_供审阅.md` 该行行号 − 20）

`doc/审计报告/` 只在 E 盘工作副本，**权威树没有这个目录** ⇒ 原文从 E 盘读、代码一律从权威树读（口径沿用 §16-10／§17-11／§18-1）。
本批四枚（第三、第四枚同指 `_return_idle`，所以合并为一次改动）：

| 汇总表行 | 汇总文件行号 | 原文报告号与位置 | 原文坐标 | 一句话 |
|---|---|---|---|---|
| 8 | :28 | P1-3 (R1)，R2 用 ruff `RUF006` 复核到 17 处 | `singleton.py` 等 16 处 | `create_task` 返回值丢弃 → Task 可被 GC，播报／任务静默丢失 |
| 7 | :27 | P1-1 (R1)/(RS)／T-02 (RB)；第六轮 P1-22（该轮 :148，并在 :208 明确「以第一轮描述为准」） | `core/dialog.py:328-359`／`358-377`／`323,353,367` | pending 技能描述过期分支无 `return` → 普通对话被误当技能描述喂生成器 |
| 71 | :91 | P1-24 (RO)；第六轮 P2-18（该轮 :190） | `core/dialog.py`（`on_wakeup` 函数级，`:411-415` 仅单点保护） | 无 `try/finally` → 中途抛异常状态永久停在 THINKING／SPEAKING |
| 70 | :90 | P1-23 (RO)／T-08 (RB) | `core/dialog.py:684-686`（RB 侧 681,684,231,422） | `_return_idle` 裸 `create_task` 无代际校验，引用丢失可致永久卡 WAITING |

⛔ 把「第六轮把 P2-18 定成 P2」读成「不用修」：那轮的降级理由是「状态仅用于 WebUI 展示、无门禁依赖」，而本仓的 WebUI 状态就是**用户唯一能看见管家在不在线**的窗口；
且同一条代码形状在表行 70／71 被两轮独立报为 P1 ⇒ 本批按 P1 处理。

### §19-2 三枚为什么同批（形状相同：控制流走到尽头≠成功）

三处的共同点是**「成功路径写了收尾，异常／过期路径没写」**：worker 的 Task 只在「起得来」的假设下活着，起不来时既有引用没留、日志还压成 warning；
SPEAKING 之后有 8 个出口（`speak()` 尾部、`tv_voice`、快速回复、`_return_idle` 等），每个出口各自抄一遍「set_state(WAITING) + 派 IDLE」，
只要某条中途抛错（MQTT publish、TTS、repo 写库）就永远停在 SPEAKING；pending 过期那段判完过期就直接往下走，缺一个 `return`。
同批的理由与 §18-2 一样：**「收尾只写一遍」这件事只需要一套腿**，逐点各写只会得到同一句断言的 N 次重复。

一处⛔ 顺手扩的地方：本批**没有**给 `dialog.py` 的 8 个出口之外的 `THINKING` 段补同样的收敛。
原因是变异腿 D1／D2 只覆盖「进入 SPEAKING 之后」这一段，把 THINKING 也纳进来会得到一把测不到新行为的尺（⇒ 留给下一批，见 §19-11）。

### §19-3 验收：9 条腿，三遍先红（每遍各一份读数件，⛔ 覆盖）

件＝`tests/test_audit_1002_batch18_silent_raise_paths.py`（现值 md5 `efa8b5f0933bf01c0858d35fa433f612`，350 行，CR 0）。
九个名字（`grep` 自本件，⛔ 缩略）：`test_worker_task_is_kept_in_a_strong_reference`、`test_worker_failure_is_logged_as_error`、
`test_transition_returns_value_and_restores_waiting`、`test_transition_restores_waiting_when_action_raises`、`test_transition_idle_task_keeps_strong_reference`、
`test_quick_reply_does_not_stick_at_speaking`、`test_speaking_transition_has_a_single_exit_site`、`test_expired_pending_does_not_feed_generator`、`test_fresh_pending_still_feeds_generator`。

| 遍次 | 读数件 | STAMP（`date -u` 原样行） | 结果行 | rc |
|---|---|---|---|---|
| 先红 1 | `red_batch18_run1.txt` | `2026-10-02T19:05:13Z` | `Ran 9 tests in 1.904s` / `FAILED (failures=3, errors=6)` | 1 |
| 先红 2（补 vendor 垫片后） | `red_batch18_run2_vendorshim.txt` | `2026-10-02T19:11:44Z` | `Ran 9 tests in 1.102s` / `FAILED (failures=4, errors=3)` | 1 |
| 先红 3（过期判定改成真值判断后） | `red_batch18_run3_truthy_expired_ts.txt` | `2026-10-02T19:12:10Z` | `Ran 9 tests in 0.947s` / `FAILED (failures=5, errors=3)` | 1 |
| GREEN | `green_b18_leg5_strongref_run3.txt` | `2026-10-02T19:26:49Z` | `Ran 9 tests in 1.810s` / `OK` | 0 |

三遍先红的**失败集合不一样**，这不是噪音：第 1 遍里有 5 行 `ModuleNotFoundError: No module named 'homesdk'` 的 traceback
（`red_batch18_run1.txt` 的 :94／:119／:144／:169／:197，宿主没装 homesdk 那一条腿），第 2 遍只剩行为缺口，
第 3 遍是「`expired_ts` 为 0 时的真值判断」这条腿换了形状。⛔ 把「先红」当成一次性动作登记——真正的先红是**每一遍都只剩我要修的那条**。

### §19-4 落码与一次性补丁器

一次性件＝`scripts/audit_1002/patch_b18_dialog_singleton_1002.py`，三遍 dry 的读数都在 `dryrun_b18_patcher*.txt`：
v1（`19:16:18Z`）红在 `ANCHOR|spoken 行不以 'spoken = await ' 开头：'self.state.set_state(DialogState.SPEAKING)'`，rc=1——
这是我把「下一句」当成锚点句的 off-by-one，**锚点门当场咬住**；v2（`19:16:45Z`）同类三处（D-353／D-341／D-226）修完，rc=1（仍是 dry 期望的不写盘）；
v3（`19:17:04Z`）rc=0。落码那遍 `apply_and_green_b18_run1.txt`（`2026-10-02T19:17:13Z`）：

```
PLAN|butler/core/dialog.py|772->776 lines|43854->44196 bytes|md5=9d805fbffb83->1159d960069c
PLAN|butler/tts/singleton.py|72->87 lines|3122->3883 bytes|md5=b6d5d646f665->eb0e6e305381
apply_rc=0
butler/core/dialog.py 777 lines; bytes=44196 CR=0 ENDNL=True md5=1159d960069c597c97eee1642a0dff9e
butler/tts/singleton.py 87 lines; bytes=3883 CR=0 ENDNL=True md5=eb0e6e3053814066fa6586d4b5a97e79
```

**PLAN 那行自己报错了行数**（772→776，盘上实为 777）：我把两行新代码塞进了同一个列表元素、元素里嵌换行符，字节一样但 `len(new)` 少 1。
发现方式是盘上行数与 PLAN 不一致，不是我「看出来了」。补丁器已改成两个元素（`# 724-731 收敛` 那段带注释说明⛔ 合成一个元素），
`fidelity_b18_patcher_run1.txt` 里同一件 PLAN 现在打 `772->777`。**教训口径**：PLAN 的行数只算「列表元素个数」，
⛔ 当盘上行数用；盘上行数只能来自 `wc -l` 那一类读数（§19-6 的逐字节对账才是终局）。

### §19-5 变异探针：run1 咬不住 D3 ⇒ 不是码错，是我那条腿写错

| 遍次 | 读数件 | STAMP | CENSUS 行 | rc |
|---|---|---|---|---|
| run1 | `mutation_probe_b18.txt` | `PROBE_STAMP 2026-10-02T19:19:25Z` | `real=8 bitten=7 controls=4 survived=4 misbite=1 restore_bad=0` | 1 |
| run2 | `mutation_probe_b18_run2.txt` | `STAMP 2026-10-02T19:27:29Z`（**同件内层 `PROBE_STAMP` 那行是空的**，见 §19-10） | `real=8 bitten=8 controls=4 survived=4 misbite=0 restore_bad=0` | 0 |

run1 的 `NOT_BITTEN|D3-idle-task-no-strong-ref|expect=test_transition_idle_task_keeps_strong_reference|actual_red=无|rc=0`——
把 `_idle_tasks.add(task)` 删掉，我那条腿照样绿。根因⛔ 在生产码，在腿：我原来只在**最后**断「注册表为空」，
而收尾任务跑完自己会摘掉引用，所以 add 与不 add 都能绿。改法（腿 5 现读）：`waiting_seconds` 给 30 秒让收尾保持 pending，
先断「此刻它必须在注册表里」，再取消、再断「收尾即摘」。run2 的 8 枚真变异逐枚点名（S1–S4 worker 四枚、D1–D4 dialog 四枚）都在 `BITE|` 行里，
**4 枚等价对照全部存活**（⛔ 计入缺口：它们是语义等价的改写，尺子不该咬）。

### §19-6 fidelity：用 HEAD 的旧码本体重放补丁器，产物逐字节等于盘上

`fidelity_b18_patcher_run1.txt`（`2026-10-02T19:28:57Z`，`HEAD=59bc449`）：旧码取自 `git show`（⛔ checkout 工作树、⛔ 手写复刻），
两份提取物 md5 先等于补丁器里的 BASE（`9d805fbffb838d5e89cadb9294ccab3c`／`b6d5d646f6657ac247c66d41c27160c4`），
对旧码跑修好的补丁器得 `fidelity_apply_rc=0`，产物与盘上现值同行同值（`1159d960069c…`／`eb0e6e305381…`），
逐字节 diff 段打 `DIALOG_IDENTICAL` / `SINGLETON_IDENTICAL`，临时目录当场 `ls` 反证 `TMP_GONE_CHECK|ls: cannot access '/tmp/b18_fid': No such file or directory`。
这一遍同时补证了 §19-4 那句「PLAN 行数会少报」不影响产物字节。

### §19-7 两枚连带重钉（我自己的行号漂移造成的红，⛔ 改判据去迁就代码）

批18 把 `dialog.py` 从 772 行改成 777 行，`self.tv.notify({` 从 :518 移到 :500，于是两处**按行号定位的尺**当场红：

1. `scripts/audit_1002/classify_unawaited_1002.py:64`（未-await 花名册 30 枚之一）。
   一次性件 `pin_b18_unawaited_anchor_1002.py`，读数 `pin_unawaited_anchor_b18.txt`（`2026-10-02T19:34:26Z`）：
   改前 `BASELINE bytes=27773 md5=cd5134409898bdfa96f953a2c92b5885`、`ANCHOR|第 64 行原样='    ("core/dialog.py", 518, "notify"),'`，
   **改前先现读新锚点**：`CONFIRM|dialog.py:500='self.tv.notify({'`、`CONFIRM|dialog.py:518(旧锚点现读)='"""小爱出声后…`（旧锚点现在指向一段 docstring ⇒ 这就是那把尺红的方式）；
   改后 `WRITTEN|lines=672|bytes=27823|md5=a1702d7f4a095d8526c8d11be99385ff`，批6 那道门重跑 `Ran 8 tests in 12.302s` / `OK`。
   ⛔ 动的是**锚点数据**，`20 SYNC_OK / 0 UNKNOWN` 的分布断言一字未改。
2. `tests/test_v25_pytest_shim.py` 的 ENV_ONLY 名单＋宿主环境。
   `pin_b18_envonly_roster_1002.py`（`2026-10-02T19:37:08Z`）：`a4d21da0581879f64fdbf1b954063508`(14,660 B/307 行) → `04d9be95f21ddb14bbad27f3d6614aef`(15,057 B/311 行)，
   把 `test_trace_chain.py` 的豁免摘掉；随后发现**摘法不对**——那 8 条腿能不能 import `homesdk` 变成取决于同场还跑了哪些档：
   差分证据在 `envonly_differential/`（leg1 只跑 shim＝红，`ModuleNotFoundError 未在册`；leg2 批18 在场＝另一种结果）。
   于是第三枚 `pin_b18_shim_vendor_1002.py`（`2026-10-02T19:38:36Z`）让替身档**自己**把仓内 `vendor/homesdk/src` 挂上 `sys.path`
   （用 `append` 不用 `insert(0)`：容器里镜像已装 homesdk，⛔ 让仓内副本盖住装好的那份），
   `04d9be95f21d` → `cf16a41ee784302cb1798e2b636f808c`(15,616 B/319 行)。两种跑法各一遍：`[A] 只跑 shim → Ran 20 tests / OK`、`[B] 批18 + shim → Ran 29 tests / OK`。
   三枚一次性件的重跑都必 raise（`ALREADY_APPLIED`，见各自读数 `rerun_rc=1`），dry 腿另存 `pin_dry_legs_b18_run1.txt`（`2026-10-02T19:42:19Z`，`A_rc=1` / `B_rc=1`）。

### §19-8 收口回归：红名单回到同一身份，skipped 的浮动只登记不糊

| 遍次 | 读数件 | STAMP | 结果 | 红名单身份 |
|---|---|---|---|---|
| run1（两枚重钉**之前**） | `run1_regression_redlistchanged.txt` | 段内 `stamp_utc=2026-10-02T19:29:40Z` | `Ran 738 tests in 63.413s` / `FAILED (failures=3, errors=10, skipped=3, expected failures=1)` | `REDLIST_CHANGED`（old_lines=10 → new_lines=13，added=3／removed=0，`run1_redlist_diff_b17_to_b18.txt`） |
| run2（重钉之后） | `run2_regression_after_repins.txt` | 段内 `stamp_utc=2026-10-02T19:42:50Z` | `Ran 738 tests in 61.605s` / `FAILED (errors=10, skipped=3, expected failures=1)` | `redlist_md5=9490b2e3085174ec213d6b34d0e0d01d` = want ⇒ `REDLIST_SAME` |
| run3（收口定版，runner 改开 `-v`） | `run3_regression_verbose_skips.txt` | 段内 `stamp_utc=2026-10-02T19:54:29Z` | `Ran 738 tests in 56.509s` / `FAILED (errors=10, skipped=3, expected failures=1)`，`skip_named=3` | 同一 `9490b2e3085174ec213d6b34d0e0d01d`，`REDLIST_SAME` |

那 3 条新增红**没有当噪音处理**，逐枚点名（`run1_redlist_diff_b17_to_b18.txt` 的「只在新表」段）：
`test_distribution_equals_todays_readings` 与 `test_no_async_unawaited_and_no_unknown`（都在 `test_audit_1002_batch6_gate.ProductionRosterTest`＝未-await 花名册的锚点漂移），
`test_trace_chain_examples_actually_run`（`test_v25_pytest_shim.RunTheRealFilesTest`＝替身档名单自核对）。
前两枚由 §19-7 第 1 件重钉、第三枚由第 2/3 件改成确定性环境，之后 run2 的红名单与批14/15/16/17 同一身份。10 枚 ERROR 的原文逐条在 run3 读数里，⛔ 抄进本节。

AST 三数（`census.txt`，`census_rc=0`）：`FILES_SCANNED|71`、`AST_TOTAL=948`、`IN_CLASS=848`、`MODULE_LEVEL=100`，
且 8 个 `MODULE_LEVEL_FILE` 行的计数相加＝100（＝与 `AST_TOTAL − IN_CLASS` 独立对账通过）。分母：`Ran 738` 与 848 之差由替身档与 loader ERROR 承担（口径沿用 §17 末段，⛔ 在本节重述成一个新数字）。

`skipped` 从批17 那遍的 9 变成本批的 3，我**没有**随手写成「环境差异」，跑了两次判别：
`skip_env_attribution_run1.txt`（`2026-10-02T19:47:59Z`）同一棵树只差那四把启动硬门键 ⇒ `T1_skipped=3 / T2_skipped=3`，env 假设**否掉**；
`skip_oldshim_attribution_run2.txt`（`2026-10-02T19:51:39Z`）只换 `git show HEAD:` 那份替身档 ⇒ `skipped=3`（HEAD 版另多 1 条 failure＝名单自核对，符合预期），shim 假设也**否掉**。
⇒ 台账记为**浮动未归因**：批17 那遍没开 `-v`，那 6 枚点不出名字（`skipped_diff_run1.txt`：两份 discover 的 skip 文本行**逐条相同**，各 7 行，差集为空）。
本批起 `run_b18_regression_1002.sh` 固定 `discover -v` 并打印 `skip_named`＋每条理由，从此 skipped 只能带名字登记。
当前 3 枚的名字与理由已在 run3 读数点名（两枚是 `config_routes` 需 `starlette`＝只在容器判，一枚是 homesdk vendor 门需 `HOMESDK_SRC`）——
⚠ 这 3 枚都是**容器侧格**，与 §19-11 的现网腿同一张窗单。

### §19-9 `butler/config.py` 启动硬门行号现读（我项目记忆里的旧数已过期）

`run_b18_acceptance_1002.sh` 与 `run_b18_regression_1002.sh` 都⛔ 手抄键名，改为从 `sed -n '300,326p' butler/config.py` 现场 grep，并有 `count==4` 的守卫
（跑偏就 `GATE_KEYS_UNEXPECTED count=<n> want=4` 直接退出）。本批现读：定义处 `:213 BUTLER_WEB_PASSWORD`、`:226 DESKPILO_API_TOKEN`、`:227 TASK_REPORT_TOKEN`、`:232 DOUBAO_API_KEY`，
抛错处在 `:306`／`:310-311`／`:320`。**我 project 记忆里写的 :272 / :278-279 / :286 是旧行号**，下一次引用前先按这把尺重读。
四把键的**值**在本批所有读数里都是 `DUMMY_NOT_REAL`（读数件只留键名，⛔ 真值）。

### §19-10 我本批的过程缺陷（一次一名，写给下一批的我自己）

1. 四把键名我手抄错了三次（同一枚，拼写每次不同）。⛔ 在这份文书里抄出错形态——它自己会变成下一次 grep 的命中。修法⛔ 是「更小心」，而是 §19-9 那把现场 grep 的尺。
2. 一把宽掩码的 `sed` 通配把我自己要打印的 `ENV_PIN` 那行一起抹掉了 ⇒ 修成「只掩值、⛔ 掩指纹」。
3. 收口脚本里 `grep … "$LOG"` 写在 `{ … } >> "$LOG"` 块内 ⇒ `input file is also the output`，那一腿静默空读数。修法：先把摘要读进变量再 printf。
4. 第一遍判别探针把 `-v` 放在 `discover` **之前**，CLI 直接报 `unrecognized arguments: -s tests -t .`、rc=2，两遍都在 1 秒内"跑完"，
   `skip_named=0`。⛔ 那次读数如果没加 `ran_lines`／`usage_error_lines` 两列，就会被我当成「没有跳过的测试」写进台账——
   「没测」和「0 命中」再次同形。现在两把探针都带分母闸：任一遍无 `Ran ` 行即 `ABORT_DENOM` 作废（该作废版本留档 `skip_oldshim_attribution_run1.txt`，没擦）。
5. `mutation_probe_b18_run2.txt` 内层 `PROBE_STAMP` 那行是**空的**（外层 `STAMP` 有值）＝那一腿的 echo 被 bash 吃了。时间只能靠外层那行；⛔ 把空字段读成「有戳」。
6. 三枚 `pin_*` 件把 `sys.argv[1]` 当仓库根 ⇒ 传 `--dry-run` 时路径变成 `…/--dry-run/tests/…`，dry 腿 `FileNotFoundError`。
   已改成「只取不以 `-` 开头的第一个参数当根」并给 unawaited 锚点那枚补了 dry 分支，两枚的 dry 腿现都真跑到 `ALREADY_APPLIED`（`pin_dry_legs_b18_run1.txt`）。

### §19-11 下一批接什么（本节⛔ 宣布的任何"完成"）

- 生产面：`dialog.py` 的 THINKING 段与 `agent` 调用点同形状的收尾收敛（§19-2 明说本批⛔ 覆盖）；P1-21 全局 `_echo_until`；P2-16 `XiaomiEar._echo_cooldown` 跨模块写；
  P2-17 `spoken` 的 bool／dict 两形；P2-19 `mark_absent` 只在电视离线时触发；P2-20 时区依赖（`docker-compose.yml:25`）；P1-23 那 16 处静默 `except Exception: continue`。
- 文书面：新到的第 12 份来源报告（`doc/审计报告/doubao-butler-稳定性功能审计报告.md`，E 盘现读 36,303 B／740 行／CR 0，报告自述快照 `836df79` 在该件 :3；4 枚 P0／10 枚 P1／10 枚 P2）——
  其 P0-1 我**已核过＝本仓早前 T-01 已修**（权威树 `role` 的绑定早于分支，代码里带着那条注释）；P1-1 即本节的 §19 表行 71 那条，已在批18 落地；
  其 P0-4／P1-2／P1-6 与 DCD 裁⑤（task #59）同题，P2-9 与表行 70 同题；**剩下的 P0-2（SQLite 全局单连接跨线程）／P0-3（`config_routes` 里 `logger` 未定义）待逐枚点开**，
  按 §5-5 那条纪律先跑 `git log` 再判「已修／未修／不修」。
- 生效面：仍是那一次经授权的 `docker restart`（窗单里 §17-8／§18 的同一条腿）；⛔ push、⛔ 自主重启。
