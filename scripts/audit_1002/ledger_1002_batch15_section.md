## §16 批15 · `_env_bool` 恒 False（表行 6）＋缓存 key 缺 `speed`（表行 14）同批落地 · 附两份下属交付的独立复核 ＋ 表行 21 的登录限流判「现网也不成立」（2026-10-03）

### §16-0 本节做的是什么，以及为什么这两枚必须一起做

| 表行 | 报告 ID（原文） | 声称的机制（合并表原文，报告原文如此） | 本批判定 |
|---|---|---|---|
| 6 | P0-2 (RO) | `_env_bool` 三元两分支均 False ⇒ 布尔配置永不为 True | 现码成立，已修（`butler/config.py:36-42`） |
| 14 | P1-7 (R1) 族 | TTS 缓存文件名只含 text/voice/engine，缺 `speed` ⇒ 改语速仍播旧音频 | 现码成立（旧公式在 4 个文件各抄一份），已修 |

耦合关系（这决定⛔ 分两批改）：`butler/config.py:278` 是 `_env_bool("TTS_CACHE_ENABLED", True)`——默认 **True**。
修好 `_env_bool` 的那一刻，TTS 缓存从「恒关」变成「默认开」，而 key 缺 `speed` 那枚潜伏缺陷立刻变成人人能看见的
「改了语速没反应」。⇒ 单修布尔＝点亮另一枚缺陷，所以本批把两枚一起做（合并表⛔ 标这层依赖，是 §5 口径之外我自己查出来的）。

### §16-1 验收先行：17 腿跑在**未修的码**上＝11 红 6 绿

`python3 -B -m unittest tests.test_audit_1002_batch15_env_bool`（基底＝权威树未修码，跑于落码前）
读数原文＝`workorders/readings/1003b15/red_batch15_final.txt`：

```
Ran 17 tests in 0.097s
FAILED (failures=10, errors=1)
```

11 红是真验收（跑在坏码上）。剩下 6 绿**⛔ 算「先红后绿」**，它们是本批自己声明的**笼头对照**：
公式形状钉住（`OLD3`/`NEW4` 两个 lambda）、缓存开关关掉时⛔ 命中、禁用态⛔ 落盘
——这 6 条的存在是为了证明那 11 条的绿不是「测试没跑到东西」，改前改后都该是绿。
落码后同一命令：`Ran 17 tests in 0.096s` / `OK`（`green_batch15.txt`）。

### §16-2 落码＝一笔 one-shot 施加器，6 个文件，重跑必 raise

`scripts/audit_1002/patch_b15_env_bool_1002.py`（一次性行切片，未动的那些行按原字节 copy，⛔ 整文件重排）。
`APPLIED` 行原文（`b15_apply.txt`，逐行照抄）：

```
APPLIED|butler/config.py|{md5 ac9ef6150a6a3897aa4185c03a199d07 -> 5034b71f6fa5eab4bf51cc3b9054fbf8, 395->395 行, 18231->18205 B, CR 0->0, funcs 11->11}
APPLIED|butler/tts/base.py|{662a8a60b3be365472f0b29c9b448ade -> 3419f28816b8057513aa2fb9206722da, 24->30, 663->975, CR 24->30, funcs 2->3}
APPLIED|butler/tts/manager.py|{7e5881537134c0b8715f011808bbc60b -> 69d9ffa64a371bead4dd5bfc8abf20da, 272->270, 11427->11396, CR 0->0, funcs 13->13}
APPLIED|butler/tts/edge_tts.py|{5e084bb0263ce2ca68b7f684f5987dad -> 3da21cf511f22019dceaaf7d6f6471b6, 86->84, 3108->3060, CR 1->1, funcs 5->5}
APPLIED|butler/tts/kokoro.py|{4eaee607828e3b2245a455c781bc9022 -> 48d6df118005a2f6bfdf0231e4282587, 59->57, 2029->1979, CR 59->57, funcs 3->3}
APPLIED|butler/tts/nowvoice_tts.py|{e7ef9472f83f7865f761e74b83ba076b -> a0ecf93f66507fe1785ef2a054ba1983, 96->94, 3652->3604, CR 0->0, funcs 5->5}
```

- **换行血统逐文件保住**（部署树是混血）：`base.py` CRLF 24→30 行、`kokoro.py` 59→57、`edge_tts.py` 全文件 86 行里**只有 1 行带 CR**，改完仍 1（⛔ 被我抹平成整文件 LF）；`config.py`/`manager.py`/`nowvoice_tts.py` CR 0→0。
- 第二遍必 raise：`ALREADY_APPLIED|butler/config.py`（`b15_rerun_must_raise.txt`，rc=1）。
- 负控（证这把尺会咬）：把 `config.py` 换成 /tmp 上的篡改副本 ⇒ `BASELINE|...|md5` 不匹配 rc=1；
  锚点改成不存在的行 ⇒ `ANCHOR_MISS|x.py:2`。两份篡改副本在同一次命令调用里 `rm -f` 并 `ls` 反证。
- 爆炸半径：`_cache_hit` 全仓 2 个落点（定义＋调用），`cache_filename` 被引用 9 次，旧 3 字段公式残留 **0** 处（`grep -c` 的 pattern 原文＝`f"{text}|{voice}|{engine}"`，0 命中；这条我另跑了一遍带 pattern 打印，⛔ 把 0 当「没测」）。

### §16-3 三条口径决定（写下来，免得下一个人当手感）

1. **⛔ 只改 reader**：旧公式在 4 个文件里各抄一份。只把读缓存那侧改成 4 字段而写侧仍 3 字段 ⇒ 永久 miss，比原缺陷更坏。
   所以先落 `base.py:cache_filename` 作唯一真源（`base.py:28-30`），再把 4 处（`manager.py` / `edge_tts.py` / `kokoro.py` / `nowvoice_tts.py`）收敛到它。
2. **`_env_bool` 的 default 语义不是「空＝假」**：未设/空值现在落回 `default`。两个安全位
   （`config.py:215 DOUBAO_WEBHOOK_ALLOW_UNAUTHENTICATED`、`:216 BUTLER_ALLOW_NO_AUTH`）默认值仍 **False** ⇒ 本批⛔ 开任何 fail-open 口子；
   `:278 TTS_CACHE_ENABLED` 默认 True 是报告里写的既有意图，本批只是让它**真的能生效**。
3. **`speed` 进 key⛔ 等于「语速真的会变」**：本批保证 key 带这个入参；上游各引擎是否真把 `speed` 传到位（尤其 `nowvoice`）另算，见 §16-8。

### §16-4 变异探针：7 枚全咬 ＋ 2 枚对照必须活（本批我自己这把尺先红了一次）

第一版（`mutate_b15_1002.py` 首跑）出口 **rc=1**，红在两处、且两处都是**我的探针**⛔ 我的码：

```
ENVBOOL_TERNARY_BACK|SURVIVED(红)|rc=0|ran=17
CONTROL_NOOP_RENAME_LOCAL|CONTROL_BITTEN(红)|rc=1|ran=17|legs=test_disabled_cache_never_hits,test_same_speed_does_reuse_cache,test_same_text_different_speed_must_not_reuse_cache
CENSUS|mutants=7 bitten=6 control_ok=0 survivors=['ENVBOOL_TERNARY_BACK']|restore_bad=无
```

- 那枚「把恒假三元塞回去」的变异体是**等价变异**：它所在的分支只在 `v` 命中 `("0","false","no","off")` 时进入，
  此时 `v` 天生非空 ⇒ 三元两个分支返回同一个值 ⇒ 语义没变，测试⛔ 该咬。修法⛔ 是加一条腿，而是把变异体改成
  **整段复原 P0-2 原始形状**（假元组 + 三元一起回去）＝`ENVBOOL_ORIGINAL_BUG_BACK`。
- 我原本当「对照」的那条（改一个局部变量名）**⛔ 对照**：`cached` 下一行还在被读，改名是破坏性变异，
  它咬了恰恰证明测试在管这件事。对照换成两条可证明语义不变的字面重排（只塞三元、集合成员换序）。
- 第二版读数（`workorders/readings/1003b15/mutation_probe_b15.txt`，`PIPE_RC=0`）：

```
CENSUS|mutants=7 bitten=7 controls=2 survived=2 survivors=无|restore_bad=无
```

  7 枚各自被点名的腿咬住（点名清单在那份读数件里，逐行 `BITTEN|legs=...`）；跑完 6 个文件 md5 全部回到
  §16-2 的 `md5_new`（同批 `md5sum` 现读，`restore_bad=无`）。
- 探针自答「它测不到什么」：只跑批15 那一档（17 腿），跨档连带由全量回归管；变异体是等价类枚举、⛔ 全空间。
- ⚠ 副作用登记：探针的还原腿用 `open(...,"wb")` 重写过那 6 个文件 ⇒ **mtime 被刷到 17:21Z**（内容 md5 未变）。
  所以 §16-7 只拿 mtime 证「盘比进程新」，⛔ 拿它当落码时刻；落码时刻以 `b15_apply.txt` 的 APPLIED 行为准。

### §16-5 本节分母（三个数 ＋ 红名单**身份**核对，⛔ 用「全过」代替）

| 口径 | 数 | 来源 |
|---|---|---|
| AST 里的 test 定义总数 | 905＝类内 805＋模块级 100（＝§15-5 的 888＋本批 17，17 枚全在类内 ⇒ 模块级⛔ 变） | `python3 -B scripts/audit_1002/ast_test_census_1002.py`（`census.txt`，`FILES_SCANNED\|68`＝批14 的 67＋本批 1 个测试文件） |
| 本宿主机实跑 | 695＝678＋17 | `python3 -B -m unittest discover -s tests -t .`（`discover_full.txt`，56.316s） |
| 收进来了但没跑（差） | 210＝905−695（与批13／批14 同值 ⇒ 本批没新增未收集项） | 上两行相减 |
| 红灯 | `^FAIL:`=0；`^ERROR:`=10 | 构成为 2 × `No module named 'pytest'` ＋ 8 × `No module named 'starlette'`＝ENV_ONLY，⛔ 行为回归 |
| 红名单**身份** | 与批14 那份**逐行相同**：两份 `sort` 后的清单 md5 同为 `9490b2e3085174ec213d6b34d0e0d01d`（10 行） | pattern 原文＝`^(FAIL\|ERROR): `；比对用的两份 /tmp 清单在同一次命令调用里 `rm -f` 并 `ls` 反证 |

⚠ ⛔ 把上一行的 md5 与 §15-5 里的 `01e7596e233dfb0035038478c651417e` 混为一谈：两个值来自**不同的 grep pattern**
（那条只切 `^ERROR:`/`^FAIL:` 的行首标记，我这条带冒号后空格并含测试全名）。相同的是**同一 pattern 下批14 与批15 两份清单互相相等**这条判据，⛔ 「与台账历史值一致」。

### §16-6 文书腿：两份下属交付的独立复核（我自己的尺，⛔ 采信自报）

交付物＝`scripts/audit_1002/_roster_extra_1002.md`（4 份未并入报告的缺陷补登记）与
`scripts/audit_1002/_reconcile_c_1002.md`（C 桶 29 行语义对账）。复跑尺＝`scripts/audit_1002/verify_agents_1002.py`，
本轮重跑读数＝`workorders/readings/1003b15/verify_agents_rerun.txt`（97 行，rc=0）：

```
TSV_C|29|uniq=True      FILE_C|29|uniq=True      SET_EQ|True|only_tsv=[]|only_file=[]
VERDICTS|{'NOT_IN_LEDGER': 23, 'PARTIAL': 5, 'COVERED': 1}|sum=29
ANCHOR|ok=18|bad=0|re=`?:(\d+)`?\s*「([^」]+)」
X_ROWS|16|['201' … '216']（连续、与合并表正文 1..184 无撞号）
EOF|D3|307|OK / EOF|RT2|258|OK / EOF|R5|255|OK / EOF|R4|224|OK
```

- 那份对账文件的 18 处「台账原文＋行号」锚点我逐枚验过（原文在该行、行号对得上）；27 个「零命中」关键词我以**同一 pattern 原样复跑**，全 0。⇒ 29 条判定的盘上依据成立。
- **一处坐标错（下属混用了两套坐标系）**：文件里说 `_echo_cooldown` 那条在「合并表 `:53`」。现读：合并表 `:53` 是**表行 33**（全仓 except-pass 面），
  `_echo_cooldown`/`_role_mode` 那行在文件 **`:73`**＝**表行 53**（`workorders/readings/1003b15/merged_table_citations.txt`）。
  ⇒ 「表行号」≠「文件行号」，这条错只错坐标、⛔ 错机制（机制原文确实在 `:73`，与该下属的实质结论一致）。
- **我自己的第一把尺有两处坏，已修**：① 锚点正则起初写成 `` `:(\d+)`「 `` ⇒ `ANCHOR|ok=0|bad=0`——原文在 `:242` 与「」之间夹着反引号＋空格，
  零命中是**尺坏**⛔ 无锚点（现 pattern 见上面那行，另加「错位 7 行仍命中」的自测腿）；
  ② `XID|2xx|tag_in_merged_src=False` 那 16 行是**无效探针**：`D3/RT2/R5/R4` 这几个简称根本不在合并表的来源集里，
  按「ID＋报告简称」grep 判「无命中」什么也不说明。⇒ 新条目成立与否只能按**机制关键词**筛：
  16 枚里 `208/209/210/211/213/214` 六枚机制关键词在合并表 0 命中，其余命中若干**词面**（如「硬编码@67」）——
  词面只当线索、⛔ 覆盖判定（覆盖与否归 §16-9 那条「合并表来源缺口」＋后续逐批验码）。
- **补登记表自己的口径是对的**：编号从 201 起跳正是为了与 1..184 永不撞号；4 份报告的条目数（D3 抽 10／RT2 9／R5 7／R4 6）
  与 EOF 现读一致，R4 判「6 条全已被合并」我按 ID＋机制双探复核成立。

### §16-7 生效面：已落码-未生效，与批12／批13／批14 同一张窗单

| 层 | 现读 |
|---|---|
| 容器 `Created` | `2026-09-30T01:54:28.459691892Z` |
| 容器 `State.StartedAt` | `2026-10-02T01:14:21.567841601Z` |
| 6 个文件 mtime | 全部 `2026-10-03 01:21:2x~3x +0800`＝`2026-10-02T17:21Z`（⚠ 被变异探针的还原腿刷过，见 §16-4；仍晚于 `StartedAt`） |
| 判定 | 盘比进程新 ⇒ **未生效**；⛔ 为这一批单独 `docker restart`，与批12／13／14 并到下一次授权窗（`up -d` 会丢可写层热修，窗单⛔ 写成 rebuild） |

窗单里本批那格的判据：`GET /api/tts/…` 同文案改 `speed` 后**返回体的 URL 变了**；
以及 `TTS_CACHE_ENABLED=false` 显式下发后缓存腿关闭。⛔ 写「缓存命中率上升」这类我⛔ 有基线的说法。

### §16-8 ⛔ 本批做的（登记，⛔ 混进本批提交）

1. **批16 改号（重要）**：§15-7 第 1 条当时写「**批15 单开**」，但批15 已被这两枚占满 ⇒ 该条（`daily` 型冷却在
   `status()` 里仍按 `cooldown_sec` 算剩余）现在编号**批16**。现读两枚坐标都在、⛔ 漂移：
   `butler/triggers/engine.py:253-259`（daily 分支＝今天火过就整天拦）与 `:530-532`（`status()` 只 `trig.get("cooldown_sec", 300)`，⛔ 看 `cd_type`）
   ⇒ 同族第二种假话。修法要先选口径（到本地午夜的秒数 vs `fired_today` 布尔）＝显示契约变更，⛔ 我抽一口，验收先行另起一批。
2. **缓存文件名⛔ 加盐**：`cache_filename` 现在是 `sha1(text|voice|engine|speed)`，与 WO-ME-227 那条「`/tts` 名字＝可外部重建的 sha1」是**同一枚面**，
   加盐（服务端混入只有服务端知道的串）是那一单的一行改动，本批⛔ 顺手做＝⛔ 把别人在途的单并进来。
3. **上游 `speed` 是否真传到位**（尤其 `nowvoice` 那条腿）⛔ 再验；本批只保证「key 带 `speed`」这一层。
4. **C 桶 23 行 `NOT_IN_LEDGER`**：本批只做了**对账**（它们⛔ 台账在册），逐行验码⛔ 开工。下一批从 `docker_tools.py` 恒假三元、
   `aliases._save` 非原子、`fast_routes` 三兄弟、`role_state.json`、`flush_merged`、`recall` 先切片后过滤 `revoked`、`adm_heartbeat` 里挑。
5. **B 桶 87 行**抽查仍欠（§15-7 第 3 条同样的账）。
6. **表行 21 的 SSE 半条**（订阅者队列满静默丢事件）本批⛔ 点开，只点了登录那半条（见 §16-9）。

### §16-9 表行 21「登录无速率限制」：现码有闸，且**现网**也有闸——三份报告的抵触裁定为「撤回方对」

背景（合并表 `:41`＝表行 21）：`P1-9 (RS)` 声称「`butler/api/*`（`_login`）、`butler/app.py`（SSE 广播）登录无速率限制（可爆破）」；
而 RT2 `:175-:185`／`:220` 与 D3 `:282` 两份都把它**撤回**（称已实现）。补登记表当时按规矩「两报告原文相互抵触，⛔ 裁定」只登记了冲突事实。本轮我从码侧结案：

```
275:def is_login_locked(client_ip: str) -> bool:          (butler/api/deps.py)
140:async def _login(request: Request):                   (butler/app.py)
146:    if is_login_locked(client_ip):                    (butler/app.py)
34:        return err("too many failed attempts", 429)    (butler/api/task_routes.py，:33 report_locked——任务回报侧另一把闸)
```

- 现网腿（⛔ 只读，`docker exec grep`，rc=0）：容器内 `/app/butler/app.py` 第 **62** 行 import `is_login_locked`、第 **146** 行调用它 ⇒ 闸在**跑着的那份码里**。
- 结论：**「登录无速率限制」现网不成立**；RT2／D3 的撤回方向正确，合并表 `:41` 那半条应视为**已过时**（⛔ 我现在动合并表正文，只在台账登记）。
- 边界要说死：① `task_routes.py:34` 是**另一条路**（任务回报）的闸，⛔ 拿它当登录闸的证据；
  ② 表行 21 的**另一半**（SSE 订阅者队列满静默丢事件）本批⛔ 验；③ 「有闸」⛔ 等于「闸的阈值合适」，阈值那一格⛔ 现读（在 `deps.py:275` 往后，下一批连 B 桶一起点）。

### §16-10 复跑（命令原文，⛔ 凭手感重述）

```
cd /vol1/1000/docker/doubao-butler
python3 -B -m unittest tests.test_audit_1002_batch15_env_bool                    # 期望 Ran 17 / OK
python3 -B scripts/audit_1002/mutate_b15_1002.py                                  # 期望 CENSUS|mutants=7 bitten=7 controls=2 survived=2|restore_bad=无，rc=0
python3 -B scripts/audit_1002/patch_b15_env_bool_1002.py                          # 期望 raise ALREADY_APPLIED|butler/config.py
python3 -B scripts/audit_1002/ast_test_census_1002.py | head -4                    # 期望 AST_TOTAL=905 / 805 / 100
python3 -B scripts/audit_1002/verify_agents_1002.py | grep -E "SET_EQ|VERDICTS|ANCHOR\||X_ROWS"   # 期望 29/29、18 锚点、201..216——⚠ 只在 E 盘工作副本跑，见下
python3 -B scripts/audit_1002/print_lines_1002.py <file> <A[-B]>                   # 通用「打印行号」尺，台账里每个行号都由它出
```

⚠ 上表倒数第二条**跑在 E 盘工作副本**、⛔ 权威树：它要读 `doc/审计报告/` 里那 4 份报告的 EOF 与合并表正文，
而那个目录只在 E 存在（路径事实在下一段）。那三份输入件（`_roster_extra_1002.md`／`_reconcile_c_1002.md`／
`verify_agents_1002.py`）本轮随本节一并复制进权威树，好让 §16-6 那些数⛔ 只活在 E 的一台机上；
但件复制过去后在权威树**仍读空**（报告⛔ 在权威树）⇒ 权威树侧把它们当**存档文书**，复跑归 E。其余五条腿全部在权威树跑过。

读数件＝`workorders/readings/1003b15/`（`red_batch15_first_run.txt`／`red_batch15_final.txt`／`b15_dry.txt`／`b15_apply.txt`／
`b15_rerun_must_raise.txt`／`green_batch15.txt`／`mutation_probe_b15.txt`／`discover_full.txt`／`census.txt`／
`verify_agents_rerun.txt`／`code_citations.txt`／`merged_table_citations.txt`／`citations.txt`），
名单以该目录 `ls` 现读为准、**本节点名非全集**，最后一段最新；该目录 `??` 未跟踪＝⛔ 无 git 托底 ⇒ 本节每个数都另有一条我已跑命令的输出行，⛔ 依赖这些文件存活。
⚠ 另两条路径事实要记：`doc/审计报告/`（11 份报告＋1 份合并表，目录现读 12 件）**只存在于 E 盘工作副本，权威树里没有这个目录**（`ls -d`/`find` 双尺空）
⇒ 报告的机制原文只能从 E 盘读，**代码**一律从权威树读；台账本体只在权威树 `doc/审计报告分诊台账-20261002.md`。
落盘自量：施加＝append-only，脚本 assert「旧文本是新文本的前缀」；追加前基底＝
`7dbda739570d407cdc8f8523c3b85781`／1,235 行／142,000 B／CR 0／`^## ` 15 段＋`^### ` 52 段（追加前的定值，写死安全）；
追加后的字节数／段数⛔ 写进本节，只指两处＝本笔 commit 正文的 `APPLIED|` 行与 `workorders/readings/1003b15/ledger_after.txt`
（`wc -c`／`grep -c "^## "`／`grep -c "^### "`／二进制 CR 四把独立尺同批打）。最终 md5 与 `date -u` 原样戳只出现在本笔 commit 正文。
本仓⛔ 戳尺 ⇒ 这两腿判「人工-无尺」，与 §8–§15 同口径。
