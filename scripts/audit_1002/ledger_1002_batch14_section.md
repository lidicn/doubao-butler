## §15 批14 · `status()` 的冷却键与 fire 侧同形制（多枚 member 键取最新）＋ §14-6 那句「面板在撒谎」置顶撤回（2026-10-02）

### §15-0 置顶撤回：§14-6 的「`/api/status` 恒显示没在冷却」是我把注释当成了接线

§14-6 原文（未改，留在 `:1041`）写的是：「凡带 `member` 的触发器在 **`/api/status` 里恒显示「没在冷却」**」。
现读尺＝`grep -rn "\.status()" butler/ --include=*.py`，三条命中全在别处：
`butler/api/audiobook_routes.py:57`、`butler/api/tts_routes.py:394`、`butler/api/tts_routes.py:413`；
`TriggerEngine.status()` **零生产调用方**，`butler/api/trigger_routes.py` 里 `cooldown_remaining` 亦 0 命中。
⇒ `/api/status` 从来没显示过这枚数，不存在一张被我谎绿的面板。

更要紧的是：同一坑批4 已经量过并且写进了文件——`tests/test_audit_1002_batch4.py:15`
原文「`TriggerEngine.status()` 全树**零调用方**」。我第一次登记时⛔ 读到那行就下了结论＝「引未验」。
**这条撤回⛔ 否定 bug 本体**：键形制打架是现读事实（`:252` 用 `cd_key`、`:525` 用裸 `trig["id"]`），
修仍然该修，只是严重度从「现网面板事故」降为「接上它的那一刻起会拿到假话」。

### §15-1 验收先行：7 枚腿跑在**未修的码**上＝4 红 3 绿

件＝`tests/test_audit_1002_batch14_status_key.py`（`360b3299e7f7`／179 行／8,363 B／CR 0，
两树 md5 同号）。RED 原件＝`workorders/readings/1002b14/red_batch14_first_run.txt`（41 行）：
`Ran 7 tests`／`FAILED (failures=4)`／rc=1，点名四枚＝
`test_member_fired_trigger_reports_cooldown_remaining`（真火路径带 member，status 报 0.0）、
`test_newest_key_wins_among_members`（实读 39.99971675872803，期望 280）、
`test_sibling_prefix_is_not_counted`（兄弟那条自己也算不出来）、
`test_status_and_the_gate_that_runs_in_the_same_direction`（`_match()` 返回 `[]` 正在拦、status 说没在冷却）。

诚实登记：另外 3 枚（裸 id 仍算得、空名不崩、过期报 0⛔ 负）跑在未修码上**也是绿的**。
它们的作用是给修法上笼头（⛔ 拿「谁都不算」换「不算别人的」、⛔ 把无 member 那条路改坏），
⛔ 冒充「先红后绿」。判它们有效的方式＝§15-4 的变异探针：`STATUS_MEMBER_ONLY` 咬在第 1 枚、
`STATUS_NO_ZERO_FALLBACK` 咬在第 2 枚、`STATUS_NO_CLAMP` 与 `STATUS_MONOTONIC_NOW` 咬在第 3 枚。

### §15-2 落码＝两笔 one-shot 施加器（各跑第二遍必 raise）

| 笔 | 施加器 | 基底 → 产物 | 行 | 字节 |
|---|---|---|---|---|
| 批14 本体 | `scripts/audit_1002/patch_b14_status_key_1002.py` | `307424a3555e` → `0da5478c51e1` | 533→539 | 26,960→27,503 |
| 批14b 措辞 | `scripts/audit_1002/patch_b14b_status_comment_1002.py` | `0da5478c51e1` → `866ee386117f` | 539→539 | 27,503→27,570 |

批14b 只改注释：把「面板绿、门红」这句（§15-0 已撤回的说法）换成现读结论。
读侧最终形状（`nl -ba butler/triggers/engine.py`，`:529`–`:531`）：

```
            tid = trig["id"]
            last = max([v for k, v in self._last_fired.items()
                        if k == tid or k.startswith(tid + ":")] or [0])
```

负控（证明基底闸会咬，⛔ 恒真闸）＝把 `engine.py` 复制到 `/tmp/b14gate/` 多写一行注释再跑施加器，
`BASELINE_MD5|01b347ff943a50d11e535e136308f793(期望307424a3555ecffb49379dfd0973b9b7)`、`negctl_rc=1`；
该沙箱当场 `rm -rf` 并 `ls -d` 反证（`No such file or directory`）。
二次施加：批14 重跑 raise、批14b 重跑 raise `ALREADY_APPLIED`（rc=1）。

我自己那把闸的红：批14 第一版把 `ALREADY_APPLIED` 排在 `BASELINE_MD5` **之后**，
而改过的文件 md5 必不等 ⇒ 那条守卫永远轮不到＝死支。已把顺序前移，二次施加才真报 `ALREADY_APPLIED`。
教训落形：**守卫也要测可达性**，光写「会拦」不证它排在哪一步。

### §15-3 三条口径决定（写下来，免得下一个人当手感）

1. **取最新＝最保守**。member 维冷却是逐位的，`status()` 只有触发器一级；多枚键里取 `max(last_fired)`，
   读数读作「该触发器名下最晚解除冷却的那位还要多久」。方向性理由＝旧读法恒 0＝「随时可再火」，
   而同一秒 `_match()` 真的在拦；新读法⛔ 谎报「可以火」，代价是可能把「bob 现在其实能火」说成「还在冷却」。
2. **前缀两形制**，`k == tid or k.startswith(tid + ":")`。naive `startswith(tid)` 会把兄弟触发器
   `trig_ext:alice` 算进 `trig` 名下（触发器 id 天然有前缀重合的，如 `morning` / `morning_lidicn`）。
3. **空名兜底 `or [0]`** 保留：没火过的触发器⛔ 因 `max([])` 抛值把整个 `/调试` 读数打挂。

### §15-4 变异探针：7 枚全咬＋1 枚控制腿必须活

件＝`scripts/audit_1002/mutate_b14_status_1002.py`，跑 `tests.test_audit_1002_batch14_status_key` ＋
`tests.test_audit_1002_batch4`（后者＝既有 `status()` 契约腿，用来抓「我把无 member 那条路改坏」）。
输出＝`workorders/readings/1002b14/mutation_probe_b14.txt`，`probe_rc=0`、`CENSUS|bitten=7 control_ok=True survivors=无`、
收尾 `md5sum` 回 `866ee386117f`（每次注入后还原并核 md5）。

| 变异体 | 判定 | 咬住它的腿（探针现读点名） |
|---|---|---|
| `STATUS_BARE_ID` | BITTEN | 4 枚：member 真火／取最新／兄弟／显示与门同向 |
| `STATUS_ANY_PREFIX` | BITTEN | `test_sibling_prefix_is_not_counted` |
| `STATUS_MEMBER_ONLY` | BITTEN | `test_bare_id_key_still_counts` ＋ 批4 `test_status_reports_durations_from_the_cooldown_store` |
| `STATUS_OLDEST_WINS` | BITTEN | `test_newest_key_wins_among_members` |
| `STATUS_NO_ZERO_FALLBACK` | BITTEN | `test_empty_cooldown_map_does_not_crash` ＋ `test_sibling_prefix_is_not_counted` |
| `STATUS_MONOTONIC_NOW` | BITTEN | 5 枚：过期／取最新／批4 两枚＋`test_status_does_not_read_the_monotonic_clock` |
| `STATUS_NO_CLAMP` | BITTEN | `test_expired_cooldown_reports_zero_not_negative` |
| `NOOP_RENAME_LOCAL`（控制） | CONTROL_SURVIVED | 只改局部名 ⇒ 必须全绿，证这把尺会报「活下来」 |

探针自答「它测不到什么」＝⛔ 碰库／碰网络／碰现网进程；只注入 `status()` 的文本形状错，
⛔ 注入运行期状态错（`_last_fired` 被别处写脏）；写侧 `:244`/`:358` 的键生成由批13 那把尺管。

### §15-5 本节分母（三个数＋红名单身份核对，⛔ 用「全过」代替）

| 口径 | 数 | 来源 |
|---|---|---|
| AST 里的 test 定义总数 | 888＝类内 788＋模块级 100（＝§14-7 的 881＋本批 7，全 7 枚在类内） | `python3 -B scripts/audit_1002/ast_test_census_1002.py`（`workorders/readings/1002b14/census.txt`，`FILES_SCANNED\|67`） |
| 本宿主机实跑 | 678（＝671＋7） | `python3 -B -m unittest discover -s tests -t .`（`discover_full.txt`，58.128s） |
| 收进来了但没跑（差） | 210＝888−678（与批13 同值⇒本批没新增未收集项） | 上两行相减 |
| 红灯 | `^FAIL:`=0；`^ERROR:`=10 | `discover_full.txt`：2 × `No module named 'pytest'` ＋ 8 × `No module named 'starlette'`＝ENV_ONLY，⛔ 行为回归 |
| 红名单**身份**（⛔ 只比条数） | 与批13 那份逐行相同 | 两份 `^ERROR:/^FAIL:` 清单 `sort` 后 `diff` 空、`md5sum` 同为 `01e7596e233dfb0035038478c651417e`（10 行）；比对用的两份临时清单当场 `rm -f` 并 `ls` 反证 |

在册坐标复核：批14 的插入在 `:525` 之后，`scripts/audit_1002/classify_unawaited_1002.py` 记的两枚
（`triggers/engine.py:215`／`:474`，均为 `write_failures.record`）现读 `sed -n "215p;474p"` 仍命中同一符号 ⇒ ⛔ 漂移，
本批⛔ 改那张名册。

### §15-6 生效面：已落码-未生效，且本批**没有现网观察对象**

| 层 | 现读 |
|---|---|
| 容器 Created | `2026-09-30T01:54:28.459691892Z` |
| 容器 StartedAt | `2026-10-02T01:14:21.567841601Z` |
| `butler/triggers/engine.py` mtime | `2026-10-03T00:36:46 +0800`＝`2026-10-02T16:36:46Z`（批14b 落盘＋探针还原） |
| 判定 | 盘比进程新 ⇒ **未生效**；下一次授权 `docker restart` 一并带上（与批12／批13 同一张窗单，⛔ 为这枚单独重启） |

⚠ 与批13 的差别要说死：`status()` 此刻零调用方 ⇒ 本批**没有可 curl 的现网格子**，
窗单里那格的判据只能是「盘上码＋单测」，⛔ 写「`/api/status` 冷却读数变了」。
（若将来要把它接进路由＝契约变更，接与不接归 DCD/出资人裁，⛔ 我在收尾批里顺手挂。）

### §15-7 ⛔ 本批做的（登记，⛔ 混进本批提交）

1. **`daily` 型冷却在 `status()` 里仍按 `cooldown_sec` 算剩余**：`_match()` 对 `cooldown_type == "daily"`
   走的是「今天火过就整天拦」（`:254`–`:259`），`status()` 完全⛔ 看 `cd_type`（`:532` 只取 `cooldown_sec`）
   ⇒ daily 触发器在凌晨火过一次后，面板类读数会说「还剩 0 秒」而门要拦到明天。这是**同族第二种假话**，
   修法要选口径（到午夜的秒数？还是 `fired_today` 布尔？）＝显示契约变更，本批⛔ 抽一口口径，批15 单开。
2. **`status()` 的接线**：见 §15-6。函数存在、docstring 自称「供 API/调试」、测试有契约腿，生产零调用方。
   按 DCD 判例「零调用方的符号要么接上要么删」，这枚有测试在守⇒⛔ 擅自删，归属待裁。
3. 现网 `trigger_cooldowns` 表／JSON 快照那一摊＝批13 已登，本批⛔ 重复采（无新腿可采）。

### §15-8 复跑（命令原文，⛔ 凭手感重述）

```
cd /vol1/1000/docker/doubao-butler
python3 -B -m unittest tests.test_audit_1002_batch14_status_key tests.test_audit_1002_batch4   # 期望 Ran 24 / OK
python3 -B scripts/audit_1002/mutate_b14_status_1002.py                                        # 期望 CENSUS|bitten=7 control_ok=True
python3 -B scripts/audit_1002/patch_b14_status_key_1002.py                                     # 期望 raise ALREADY_APPLIED
python3 -B scripts/audit_1002/ast_test_census_1002.py | head -4                                # 期望 AST_TOTAL=888 / 788 / 100
```

读数件＝`workorders/readings/1002b14/`（`red_batch14_first_run.txt`／`apply_b14.txt`／
`b14b_dry.txt`／`b14b_apply.txt`／`green_batch14_7legs.txt`／`batch4_regression.txt`／
`green_after_b14b.txt`／`mutation_probe_b14.txt`／`discover_full.txt`／`census.txt`），
名单以该目录 `ls` 现读为准、**本节点名非全集**，最后一段最新；该目录 `??` 未跟踪＝⛔ 无 git 托底，
所以本节每个数都另有一条我已跑命令的输出行，⛔ 依赖这些文件存活。
落盘自量：施加＝append-only，脚本 assert「旧文本是新文本的前缀」；施加前的基底＝
`6e73d742201054573a1c3d7b4020c654`／1,090 行／130,351 B／CR 0／`^## ` 14 段＋`^### ` 43 段
（这几格数是**追加前**的定值，写死安全）；追加后的字节数／段数⛔ 写进本节，
只指两处＝本笔 commit 正文的 `APPLIED|` 行，与 `workorders/readings/1002b14/ledger_after.txt`
（`wc -c`／`grep -c "^## "`／`grep -c "^### "`／二进制 CR 四把独立尺同批打）。
最终字节的 md5 与落盘戳只出现在本笔 commit 正文的 `date -u` 原样行（取于本文件最后一次写入之后）。
本仓⛔ 戳尺 ⇒ 这两腿判「人工-无尺」，与 §8–§14 同口径。
