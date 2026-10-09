#!/usr/bin/env bash
# 批85 收口回归：全库 A 遍 + 剔跳过家族 B 遍 + 本批件单跑 S 遍 + skip 名册闭合 + 红名单身份 + AST 三数。
# 本件由 scripts/audit_1002/b85_regression_maker_1009.py 从批36 那份机械派生（整行常量重写＋短串逐对替换，
# 每对命中数必须＝声明值），⛔ 手抄。
# 命名口径：本件与读数目录用 `_1009`/`1009_b85`＝本次收窗的**北京时间日**（现读 UTC=2026-10-08T19:35Z
# 对应 CST 2026-10-09T03:35 ⇒ 跨钟比一律 @epoch，⛔ 拿日号当时序）；被测件文件名里的 `1009` 是它的落盘日，
# 与本次收窗同日（CST），无跨日错配。批36 那份头里写的是「UTC 日」，本批沿用其句式即错＝登记为本批自造错。
#
# ── 本批差异（批85＝路线图 v2.4:156「Bark 乱推送根因（内容只有 "push"）」现网重测）──
#   1) 症状本体未复现（六把尺全带会咬的负控，读数 workorders/readings/1009_b85/）：
#      push_audit 全表 717 行／跨度 09-10→10-08（28.06 天），
#      TRIM(LOWER(body))='push' 恰 0 行、title 恰 0 行、body LIKE '%push%' 恰 0 行、空 body 恰 0 行；
#      正对照＝同一把尺在同表现量到 71 行中文正文（NEGCTRL_ZH_ROWS|71）⇒ 这四个 0 是真 0，⛔ 尺哑。
#      ⇒ ⛔ 按症状名给这一格打勾。
#   2) 真机制（同一句验收语下面实际存在的东西）：持续存在的异常**每小时重推一次**。
#      ANOMALY_GAP_BUCKETS|2m_1h=17|1h_2h=84|2h_6h=1|gt6h=2（近 7 天 105 行异常推送，84 落在 1–2 小时桶）；
#      成因两条腿：`AnomalyDetector._is_duplicate` 的 `DEDUP_WINDOW=3600` 正好一小时，
#      而 dedup 键 `mode_anomaly_{user}_{YYYYMMDDHH}` 是**小时粒度**（跨钟即换新键）＋正文里嵌
#      一个每分钟都在变的「已过 N 分钟」计数 ⇒ 每一轮都是「新内容」。
#      PushGuard（butler/guard/push_guard.py）只有爆发闸（L2 5/60s、L3 15/60s），**没有重复内容这条腿**
#      ⇒ 28 天里对 645 行 bark 零拦截零合并（DIST_ACTION|pass=645|queue=72），与判据同源。
#   3) 本批落的半截＝自主可决的那半：`_mark_pushed` 里当场淘汰过期键。
#      `_dedup_cache` 是**进程级无界 dict**（只写不清，小时粒度键每小时换新）⇒ 长跑只涨不落；
#      修法＝写键前把 `now - seen >= DEDUP_WINDOW` 的键 pop 掉，⛔ 动去重语义（窗口内照旧压）。
#      重推**节奏**本身（1 小时该不该、要不要同一异常只报一次、要不要并进 PushGuard 重复腿）
#      ＝产品决策 ⇒ 归 DCD，卡片随批85 呈报，本门 ⛔ 预判裁哪一档。
#   4) 被测件 tests/test_audit_1009_batch85_anomaly_dedup_prune.py 共 4 格，分母三把尺同号：
#      AST（类内方法 4 ＋ 模块级 0 ＝ walk 总 4）／`def test_` 行计数 4 ／ S 遍 `Ran 4`。
#      模块级 0 ⇒ 批34 那类「不 import pytest 的模块级 test_* 被 discover 静默收下」的盲区本批不存在。
#      宿主 S 遍 skipped＝0（件里只有 time/unittest＋`butler.timeseries.anomaly` 一条 import 链，
#      ⛔ starlette、⛔ 网络、⛔ DB）⇒ **本批不新增 skip 声明**；S 遍那一面走同一条闭合腿（unmatched 须＝0）。
#   5) 三数对账换号（下面的数是先写死的**预测值**，由本次真跑判生死；
#      跑红就登记「预测错」⛔ 改判据迁就读数）：
#        RAN_A_WANT=922        批36 的 918 ＋ 本批 4 格
#        RAN_B_WANT=901        批36 的 897 ＋ 本批 4 格（本批件在这一遍里，⛔ 当在册基数读）
#        RAN_OWN_WANT=4        S 遍单跑本批件＝AST 总 4 同号
#        RAN_INBOOK_WANT=897   在册基数 = B 遍 901 − 本批 4 ＝ 批36 的 B 遍现量
#        RAN_DELTA_WANT=21     = 922 − 901（跳过家族四枚的贡献，⛔ 随本批变）
#        skip 判据（批86 腿②）＝名册闭合四腿：尺子自校准／A 遍每行有来路／两把尺同号／S 遍 unmatched＝0。
#        ⛔ 恢复成「SKIP_OTHER_WANT=19」这类行数钉值——那个数是这一面的依赖状态决定的，换一面就漂。
#        REDLIST_WANT / PREDECESSOR_WANT / REDLIST_LINES_WANT 全部与批36 同号（11 枚）
#      ⚠ 预测风险先写在这儿：现树此刻有 20 枚他座在途件（`git status` 里 M/D，含 butler/app.py），
#      A 遍吃的是**工作树**不是 HEAD ⇒ 红名单／基线点名可能因他座字节而变。那属「非本批」，
#      逐枚点名登记（分类探针另出一枚读数件），⛔ 把他座未提交的指纹钉进本门，⛔ 放宽期望值。
#   6) 基线名单 27 枚 → 27 枚（枚数不变，一枚重钉）：
#        butler/timeseries/anomaly.py  712982b834a41dbacf77e308ed6396db（改前）
#                                     → 6a90bd132874272a0a9c617a4207f00d（改后＝本批落盘那份字节）
#      改前后现读：377 → 381 行，CR 0 → 0（整文件 LF，⛔ 混血），15,402 → 15,708 B；
#      落码器 workorders/tmp_local/b85/b85_apply_prune_1009.py（pin 25c27b6028d8）自带三条腿：
#      OLD 块 hits==1、CRLF==0、ROUNDTRIP_EQ==YES，任一红即 ABORT|NO_WRITE。
#      ⛔ 动他座在途件：本批生产码只碰 anomaly.py 一枚，测试件一枚新增。
#   7) 负控＝RED-first 那一遍（⛔「把测试改成迎合代码」当过关）：
#      落码**前**同一件同一跑法：test_stale_keys_are_pruned_on_mark 报 `AssertionError: 51 != 1`、
#      test_prune_keeps_keys_inside_window 报 `'out_0' unexpectedly found`，FAILED (failures=2)；
#      落码后 Ran 4 OK（读数 workorders/readings/1009_b85/b85_green_1009.txt）。
#      两枚配对守护格（窗口内⛔ 误压／窗口外必须再报）保证「一把清光」这种作弊形状过不了。
#   8) 落盘≠生效：anomaly.py 在启动期被 import ⇒ 现网那一遍要等授权的那次 `docker restart doubao-butler`
#      （#77）之后才算数；容器**文件面**因 bind mount 应已是新字节——本批单出一枚现证探针
#      （容器内 md5 vs 部署树 md5 同值），⛔ 把文件面读成生效面。
#   9) 本批自己造的错（留档，⛔ 藏）：
#      a) probe1 的时间窗腿写坏：`datetime(<epoch 数>)` 在 SQLite 里按**儒略日**解释 ⇒ 恒 NULL，
#         读出 CUT_TS|None／ROWS_LAST_7D|0，差点把「近 7 天没推送」当成事实。修法＝cutoff 在
#         Python 侧由 MAX(ts) 算（probe2），probe1 那份读数原样留档不重写。
#      b) probe2 的日桶腿差点同样本坏：REAL 列上 `substr(ts,1,10)` 出的是科学计数法文本 ⇒
#         改 Python 侧分桶＋TS_UNPARSEABLE 计数，⛔ 让 SQL 的字符串函数替我数日子。
#      c) 一次 shell cwd 漂到 doc/00-路线图 ⇒ 相对 md5sum 全红、一条 mkdir&&scp&&echo 链死于引号，
#         之后每条命令显式 cd 回仓根。
#  10) 临时物：本脚本自己的只有 /tmp/b85_qa_data（跑后 rm 并当场反证）与 /tmp/b85_redlist_tmp_$$。
#      本批测试件⛔ mkdtemp（纯内存夹具），无 fixture 残留腿。
#  11) 在册尺行锚重钉（本批 run1 亲证的第一红，⛔ 当生产回归读）：
#      启动硬门名册腿那枚 sed 行段（老值 300,326）读出 3 把键 ⇒ GATE_KEYS_UNEXPECTED count=3
#      整门 exit 8（跑前就死，一格测试都没执行）。现读 config.py（md5 a76607160e75e523ff0bd8e068ae6218，
#      最后一次改动＝提交 94295b7 @2026-10-08 19:02+0800，⛔ 本批所改）：四把键在
#      :317 DOUBAO_API_KEY／:321-322 DESKPILOT_API_TOKEN+TASK_REPORT_TOKEN／:331 BUTLER_WEB_PASSWORD
#      ⇒ 老窗口 300..326 恰好漏掉 :331 那一把。本批把窗口重钉为 315,331（pair 命中数必须＝1）。
#      两条对照同批现读（读数件 workorders/readings/1009_b85/b85_config_window2_1009.txt）：
#        旧窗口 300,326 ⇒ n=3（＝这条腿会咬的证据，⛔ 恒 4 的死尺）
#        新窗口 315,331 ⇒ n=4，且⛔ 混进第五把（同名的四枚**声明**在 :222/:235/:236/:241，全在窗口下界之外）
#      ⚠ 同一枚 config.py 在基线名册里仍钉 d893fb30baf8（批36 那次现读）⇒ 本批 base_check 会报
#      BASELINE_BAD｜butler/config.py｜非本批。⛔ 由本批替别人重钉（那是改动方的税），只在读数里点名，
#      分类探针另出一枚件。行号当锚点＝每改一次交一次税，与在册「绊线绑推导⛔ 绑常量」正面对撞 ⇒ 另开单。
#  12) run2 现量（本门第一次真跑完）⇒ **本批 own 腿全绿，全库腿红，且那堆红⛔ 由本批造成**：
#      own 腿：S 遍 Ran 4／OK、OWN_SKIP_SAME(0)、anomaly.py 跑前跑后都 BASELINE_OK、LIVE_DB_SAME=YES、
#              DATA_DIR 反证 ABSENT、AST 单枚尺 4 ＝ `def test_` 4 ＝ Ran 4（三把同号）。
#      全库腿：Ran 1189（本门预测 922）、红名单 87 枚（本门钉 11）、B 遍 2 秒即死（ran 值为空⇒在册基数
#              读成 −4）、skip other=63（在册 19）、基线 14 枚漂。C 遍复跑 md5 与 A 遍同号
#              （REDLIST_CHANGED_BOTH_PASSES）⇒ 87 枚是**确定性红**，⛔ 读成间歇红。
#      归因读数件 workorders/readings/1009_b85/b85_drift_classify_1009.txt（探针 pin 81001ef9cf29，两面同值）：
#        ① 14 枚漂移拆开＝已提交 9 枚／他座**未提交在途** 5 枚 ⇒ 后者⛔ 钉进本门当期望值。
#        ② 87 枚红落在 18 枚件里；根因分布 AssertionError=24／ModuleNotFoundError=14（宿主缺 starlette
#           一类＝跑法面，非生产面）／PermissionError·IndexError·TypeError 各 1。逐枚定性才算收窗。
#        ③ 本批被测面在这些件里 0 命中（mine_absent），同一把尺换必然存在的锚点正对照命中 7 枚
#           （positive_control_bites）⇒ 那个 0 是真 0，⛔ 尺哑。
#        ④ B 遍死因＝import 期 raise SkipTest 的地雷数从 4 涨到 16（粗筛上界，含批42 一枚先把具名路线
#           打断）⇒ 剔除名单随每批变＝「绑常量」的形状；本批⛔ 擅自把名单扩到 16 枚（那是全库收窗的活）。
#        ⑤ 独立第二尺：AST 总 1562 vs discover 实收 1189 ⇒ 缺口 373 枚（只由 pytest／shim 跑的那批件），
#           与本批 own 件无关（本件三把尺同号 4）。
#      ⇒ 结论：**收口回归门自批36 之后断线**（现树 scripts/audit_1002/ 无任何批37…批84 的门件，本批是
#         48 批以来第一次跑它），本门的号只能对到批36。全库那一堆红＝48 批未收窗的账，⛔ 并进本批销，
#         ⛔ 为让门变绿而动期望值 ⇒ VERDICT 如实留 RED，另开「全库收窗」单（台账 §100 登记）。
# ── 继承批29/批32 的机制约束（⛔ 重犯）─────────────────────────────────────────
#   · 名册只吃 discover/ls 的 tests/test_*.py 顶层件；tests/contract/ 无 __init__.py 收不到，
#     那几枚另走 scripts/contract_gate.sh（一档一进程），两边口径不同⇒比树按同一边界。
#   · 减法对账两头必须同一个机制（批25 run3 亲证：find 名册 835 vs discover 809＝两把尺不同源）。
#   · 红名单函数 stdout 只留 printf 那一行（批28 run3 亲证：游离的 md5sum 会把「枚数」格子读空）。
#   · 密钥腿：只从 butler/config.py 现读**键名**并灌 DUMMY_NOT_REAL，⛔ 打印任何真实值、⛔ 落盘。
#   · DATA_DIR 钉 /tmp 假库；现网库跑前跑后同值反证；临时物与 ls 反证写在同一次调用里。
#   · 只读 git（--no-optional-locks）；⛔ 任何 checkout/reset。
# 用法：bash scripts/audit_1002/run_b85_regression_1009.sh [run_tag]
set -u
cd "$(git --no-optional-locks rev-parse --show-toplevel 2>/dev/null || echo .)" || exit 9
OUT=workorders/readings/1009_b85
TAG="${1:-run1}"
mkdir -p "$OUT"
LOG="$OUT/regression_and_census_${TAG}.txt"
DISC_A="$OUT/discover_full_${TAG}.txt"
DISC_B="$OUT/discover_minus_skipfam_${TAG}.txt"
DISC_C="$OUT/discover_full_${TAG}_pass2.txt"
DISC_S="$OUT/run_own_batch_${TAG}.txt"
CEN="$OUT/census_${TAG}.txt"

TEST_FILE=tests/test_audit_1009_batch85_anomaly_dedup_prune.py
TEST_MD5_WANT=84edbd47f4021ce22be04b380c6d6269   # 本批验收件（部署树＋E 镜像两面同值；容器无 tests/ 目录，⛔ 第三面）
TEST_MOD=$(printf '%s' "$TEST_FILE" | sed 's|/|.|g; s|\.py$||')
# 批86 腿②：跳过族⛔ 再钉「行数／文案键」（八把 *_SKIP_KEY ＋ 四枚 *_SKIP_WANT 已删）——
#   那个数是**这一面的依赖状态**决定的，换一面就漂；判据换成「名册闭合」：
#   运行面每一行跳过都必须在**当前源码**里有来路（尺＝scripts/audit_1002/b86_skip_family_derive_1009.py）。
#   --module-prefix tests ⛔ 省：省了⇒qid 那条腿整条哑（10-09 实犯 by_qid=0、unmatched=72＝假红），
#   跑面是 `-s tests -t .`，名册前缀必须与它同号；尺子自己有一格 CAL_PREFIX 盯着这个形状。
SKIP_RULER=scripts/audit_1002/b86_skip_family_derive_1009.py
REDLIST_WANT=e3fc107733ed2cc5a7078f45ddc8896a
PREDECESSOR_WANT=9490b2e3085174ec213d6b34d0e0d01d
RED_RE='^(FAIL|ERROR): '
RAN_A_WANT=922   # 批36 的 918 ＋ 本批 4 格（算式见文件头第 5 段）
RAN_B_WANT=901   # 批36 的 897 ＋ 本批 4 格（本批件在这一遍里，⛔ 当在册基数读）
RAN_OWN_WANT=4   # S 遍单跑本批件＝AST 总 4 同号（三把尺对账见文件头第 4 段）
RAN_INBOOK_WANT=897   # 在册基数 = B 遍 901 − 本批 4 ＝ 批36 的 B 遍现量 897
RAN_DELTA_WANT=21   # 922 − 901 ＝ 跳过家族四枚的贡献（批29 的 14 ＋ 批32 的 7），与本批无关
REDLIST_LINES_WANT=11
BASE_WANT="6b8908a85a97fccd81b5cdcd602c2e87 butler/logging_setup.py
19ae1116a8991d9fb268712029ece1de butler/core/cron_task.py
a5505389dd9599ed1e88c91fdc98b6ba butler/core/event_stream.py
3fe7f5b02da82900e7b50092b8a5bd17 butler/core/failure_learn.py
0d3828bab41e1f0a43c28486b6d8e370 butler/core/self_evolve.py
7c8660a9ece65c0891ecf0871e236a5c butler/core/wakeup.py
a2e9d51f95bdc4589ccd771dc6447185 butler/core/xiaomi_ear.py
9e0c78ed5d1a564c48927be2c3beac0a butler/integrations/ha.py
6e42e11939f8b362360aa70a5c530258 butler/proactive/engine.py
6a90bd132874272a0a9c617a4207f00d butler/timeseries/anomaly.py
660ff5924bf9f371e5b7198ef72b183d butler/api/doubao_webhook.py
67775eb0f7e908ab6960a253bbda174e butler/mcp/server.py
4166981fde29938333fca54c6d9965b8 butler/api/cron_task_routes.py
9fd6e54ad3c0105488171c699aeed091 butler/app.py
d893fb30baf8cae4d7aa177b7f0d5c81 butler/config.py
06556652b02de33983d2ced436dcb752 butler/triggers/engine.py
28561880dbde01986f97a61aa373b1cb butler/runtime.py
4b888706c32f9c4100e5d5d5d8635fe9 butler/api/system_routes.py
b5ca70223896496d8d24e9ea4b835732 scripts/audit_1002/classify_unawaited_1002.py
2f0b9828bbb941396f911e6edbfd42af tests/test_audit_1002_batch6_gate.py
cdb931278297d9cef2c8fe5ba4ac7755 butler/morning/routine.py
5eba1b7d137dc898e5cba7b5e1c88a4b butler/notifier/router.py
64b8df046b2554a130e85536ec6bfcfc butler/tts/manager.py
ab69260d128d60f47fcb65e9d24a581b butler/tts/base.py
5ba99019cdb2ecc3f23d43eb99fddc1c butler/tts/edge_tts.py
71b9f628a8fa9ef5a743e9bb27839ea5 butler/tts/nowvoice_tts.py
95ffe5a6513bbcc46613b4e52d311791 butler/tts/playback_queue.py"
N_BASE=$(printf '%s\n' "$BASE_WANT" | grep -c .)   # 基线枚数现读＝本门自己的被测面大小，⛔ 在判绿散文里写死

KEYS=$(sed -n '315,331p' butler/config.py \
       | grep -oE '[A-Z][A-Z0-9_]*_(API_)?(KEY|TOKEN)|BUTLER_WEB_PASSWORD' | sort -u)
nk=$(printf '%s\n' "$KEYS" | grep -c .)
if [ "$nk" != "4" ]; then echo "GATE_KEYS_UNEXPECTED count=$nk want=4"; exit 8; fi
for k in $KEYS; do export "$k=DUMMY_NOT_REAL"; done
export DATA_DIR=/tmp/b85_qa_data
POINTER_ENVS=$(grep -rhoE 'environ\.get\("B[0-9]+_[A-Z_]+_PY"' tests/ | grep -oE 'B[0-9]+_[A-Z_]+_PY' | sort -u)
export B22_STREAM_PY="$PWD/butler/api/stream_routes.py"
export B22_APP_PY="$PWD/butler/app.py"
export B21_CRON_PY="$PWD/butler/core/cron_task.py"
export B20_APP_PY="$PWD/butler/app.py"
export B26_SRC_ROOT="$PWD"   # 本件源码腿的根（宿主路线不给也＝对，给了才看得见）
export B33_REPO_ROOT="$PWD"   # 本批件的 REPO 落点：件内有 dirname(__file__)/parent 兜底，这里显式钉住，
export B36_REPO_ROOT="$PWD"   # 本批件的 REPO 落点：件内有 parents[1] 兜底，这里显式钉住，
                              # 让结构腿读的那三枚与被执行的那份是同一棵树的同一字节
                              # 让 t8/t9 读的那枚 app.py 与被执行的那枚是同一棵树的同一份字节
ENV_MISS=""
for k in $POINTER_ENVS; do
  eval "v=\${$k:-}"
  [ -n "$v" ] || ENV_MISS="$ENV_MISS $k"
done
if [ -n "$ENV_MISS" ]; then echo "POINTER_ENV_MISSING|[$ENV_MISS]"; exit 8; fi
mkdir -p "$DATA_DIR"

LIVE_DB=/vol1/1000/docker/doubao-butler/data/butler.db
BEFORE=$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null || echo NO_LIVE_DB)
WAL_BEFORE=$(stat -c '%Y %s' "${LIVE_DB}-wal" 2>/dev/null || echo NO_WAL)

# 模块名册现读：⛔ 拿 find 当名册（批25 run3 亲证：find 名册那遍 835、discover 那遍 809，两把尺根本不同源
#   ⇒ 本批的 B 遍同样只吃 discover 名册，⛔ 拿 find 数出来的数当分母）。
#   真因：tests/contract/ 无 __init__.py ⇒ discover 收不到它，而 find 会收；那 4 枚契约件由
#   scripts/contract_gate.sh「一档一个进程」单跑（卡6③的门，⛔ 并 discover 名单，两边口径不同）。
#   ⇒ 减法对账两头必须同一个机制：B 遍只吃顶层 tests/test_*.py，才配和在册基数比（本批的 897 由
#     S 遍从 B 遍里减出：897 = 901 − 4；⛔ 直接把 B 遍的 901 当在册基数读，它含本批 4 格）。
ALL_MODS=$(ls tests/test_*.py | sed 's|/|.|g; s|\.py$||' | sort)
B27_MOD=$(printf '%s\n' "$ALL_MODS" | grep -F 'batch27_xiaomi_ubus_gate')
B28_MOD=$(printf '%s\n' "$ALL_MODS" | grep -F 'batch28_silent_except')
B29_MOD=$(printf '%s\n' "$ALL_MODS" | grep -F 'batch29_webhook_dedup_release')
B32_MOD=$(printf '%s\n' "$ALL_MODS" | grep -F 'batch32_new_loop_await')
N_MOD=$(printf '%s\n' "$ALL_MODS" | grep -c .)
MODS_NO_SKIPFAM=$(printf '%s\n' "$ALL_MODS" | grep -Fv "$B27_MOD" | grep -Fv "$B28_MOD" | grep -Fv "$B29_MOD" | grep -Fv "$B32_MOD")
if [ "$(printf '%s\n' "$B27_MOD" | grep -c .)" != "1" ] \
   || [ "$(printf '%s\n' "$B28_MOD" | grep -c .)" != "1" ] \
   || [ "$(printf '%s\n' "$B29_MOD" | grep -c .)" != "1" ] \
   || [ "$(printf '%s\n' "$B32_MOD" | grep -c .)" != "1" ]; then
  echo "EXCLUDED_MODULE_ODD b27=$(printf '%s\n' "$B27_MOD" | grep -c .) b28=$(printf '%s\n' "$B28_MOD" | grep -c .) b29=$(printf '%s\n' "$B29_MOD" | grep -c .) b32=$(printf '%s\n' "$B32_MOD" | grep -c .)"; exit 8
fi
if [ "$(printf '%s\n' "$MODS_NO_SKIPFAM" | grep -c .)" != "$((N_MOD - 4))" ]; then
  echo "MODULE_ROSTER_ODD total=$N_MOD"; exit 8
fi
# 本批件⛔ 在跳过家族里（它没有模块级 SkipTest）⇒ 它必须出现在 B 遍名册中，否则 S 遍与三数对账全错位。
#   批35 这条腿吃的是上一批的模块名（batch34_bypass_roster）＝对本批零信息，本批改吃 $TEST_MOD（R-a）。
if ! printf '%s\n' "$MODS_NO_SKIPFAM" | grep -qxF "$TEST_MOD"; then
  echo "OWN_MODULE_NOT_IN_B_PASS"; exit 8
fi
# 分母边界自报（⛔ 把「discover 没收到」读成「没有这些测试」）：
CONTRACT_FILES=$(ls tests/contract/test_*.py 2>/dev/null | wc -l | tr -d ' ')
CONTRACT_GATE=$([ -f scripts/contract_gate.sh ] && echo PRESENT || echo MISSING)

base_check() {
  bad=0
  while IFS= read -r want; do
    f=$(printf '%s' "$want" | cut -d' ' -f2)
    w=$(printf '%s' "$want" | cut -d' ' -f1)
    got=$(md5sum "$f" | cut -d' ' -f1)
    if [ "$got" = "$w" ]; then echo "BASELINE_OK|$f|$got"; else
      echo "BASELINE_BAD|$f|got=$got|want=$w"; bad=$((bad + 1)); fi
  done <<EOF
$BASE_WANT
EOF
  echo "baseline_bad=$bad"
}

redlist_of() {
  f=$1
  t=/tmp/b85_redlist_tmp_$$.txt
  grep -a -E "$RED_RE" "$f" | sort >"$t"
  # run3 亲证的缺陷：这里多一句游离的 `md5sum "$t"`，它把 md5 当函数 stdout 的第一行印出去，
  # 于是调用侧 `read RED_A RED_A_LINES` 只吃到那一行 ⇒ 枚数格子是空的（两条都是同一个 md5，
  # 侥幸没把判据判坏，但「行数」这一腿当时是零信息）。函数 stdout 只留 printf 那一行。
  lines=$(wc -l <"$t" | tr -d ' ')
  cp "$t" "$f.redlist"
  sed 's/^/  /' "$f.redlist" >>"$f.redlist.detail"
  rm -f "$t"
  printf '%s %s\n' "$(md5sum "$f.redlist" | cut -d' ' -f1)" "$lines"
}

{
  echo "=== 批85 收口回归 ==="
  date -u '+stamp_utc=%Y-%m-%dT%H:%M:%SZ'
  echo "cmd: bash scripts/audit_1002/run_b85_regression_1009.sh $TAG"
  echo "HEAD=$(git --no-optional-locks rev-parse --short HEAD) tree_root=$(pwd)"
  echo "ENV_PIN gate_keys=$(printf '%s,' $KEYS | sed 's/,$//') values=DUMMY_NOT_REAL DATA_DIR=$DATA_DIR pointer_env=$(printf '%s' "$POINTER_ENVS" | tr '\n' ',' | sed 's/,$//')"
  echo "module_roster=$N_MOD（B 遍剔四枚＝跳过家族：批32=$B32_MOD、批29=$B29_MOD、批28=$B28_MOD、批27=$B27_MOD；本批件⛔ 在家族里⇒它同时进 A/B 两遍，见文件头第 4 段）"
  echo "分母边界|tests/contract/ 子目录 discover 收不到（无 __init__.py）⇒ 本件的 B 遍名册同样不收它；那 $CONTRACT_FILES 枚另走 scripts/contract_gate.sh（$CONTRACT_GATE，一档一进程）。在册基数 $RAN_INBOOK_WANT／A 遍 $RAN_A_WANT 两个数都⛔ 含契约档⇒ 比树时两边按同一边界"
  echo "LIVE_DB_BEFORE=$BEFORE / WAL_BEFORE=$WAL_BEFORE"
  echo "--- 跑前：本批测试件与 $N_BASE 枚被测件都必须在基线 ---"
  echo "test_mod=$TEST_MOD"
  md5sum "$TEST_FILE"
  echo "test_md5_ok=$([ "$(md5sum "$TEST_FILE" | cut -d' ' -f1)" = "$TEST_MD5_WANT" ] && echo YES || echo "NO(want=$TEST_MD5_WANT)")"
  base_check

  echo
  echo "--- A 遍：全量（-v 留 skip 行）---"
  start=$(date +%s)
  python3 -B -m unittest discover -v -s tests -t . >"$DISC_A" 2>&1
  echo "A_rc=$? wall=$(( $(date +%s) - start ))s"
  grep -a -E '^Ran |^OK$|^FAILED' "$DISC_A"
  echo "A count^FAIL: = $(grep -a -c '^FAIL: ' "$DISC_A") count^ERROR: = $(grep -a -c '^ERROR: ' "$DISC_A")"
  ran_a=$(grep -a -E '^Ran ' "$DISC_A" | awk '{print $2}')
  [ "$ran_a" = "$RAN_A_WANT" ] && echo "RAN_A_SAME($ran_a)" || echo "RAN_A_CHANGED(got=$ran_a want=$RAN_A_WANT)"

  echo "--- B 遍：剔掉批32＋批29＋批28＋批27 四枚测试件 ⇒ 必须回到 $RAN_B_WANT（剔四枚是被机制逼的：任一枚模块级 SkipTest 会把具名路线整趟打断；本批件⛔ 在家族里，见文件头第 4 段）---"
  start=$(date +%s)
  python3 -B -m unittest $MODS_NO_SKIPFAM >"$DISC_B" 2>&1
  echo "B_rc=$? wall=$(( $(date +%s) - start ))s"
  grep -a -E '^Ran |^OK$|^FAILED' "$DISC_B"
  ran_b=$(grep -a -E '^Ran ' "$DISC_B" | awk '{print $2}')
  [ "$ran_b" = "$RAN_B_WANT" ] && echo "NO_SKIPFAM_DENOM_SAME($ran_b)" || echo "NO_SKIPFAM_DENOM_CHANGED(ran=$ran_b want=$RAN_B_WANT)"
  echo "跳过家族四枚在全库里的净贡献=$((ran_a - ran_b))（＝批29 的 14＋批32 的 7；本批件⛔ 不在这个差里，因为它不在剔除名单⇒A/B 两遍都有它，见文件头第 4 段）"
  [ "$((ran_a - ran_b))" = "$RAN_DELTA_WANT" ] && echo "RAN_DELTA_SAME($((ran_a - ran_b)))" || echo "RAN_DELTA_ODD(got=$((ran_a - ran_b)) want=$RAN_DELTA_WANT)"

  echo "--- S 遍：单跑本批件（具名路线 -v）⇒ Ran $RAN_OWN_WANT ⇒ 在册基数 = B 遍 − 本批 = $RAN_INBOOK_WANT ---"
  start=$(date +%s)
  python3 -B -m unittest -v "$TEST_MOD" >"$DISC_S" 2>&1
  echo "S_rc=$? wall=$(( $(date +%s) - start ))s"
  grep -a -E '^Ran |^OK|^FAILED' "$DISC_S"
  ran_s=$(grep -a -E '^Ran ' "$DISC_S" | awk '{print $2}')
  [ "$ran_s" = "$RAN_OWN_WANT" ] && echo "RAN_OWN_SAME($ran_s)" || echo "RAN_OWN_CHANGED(got=$ran_s want=$RAN_OWN_WANT)"
  inbook=$((ran_b - ran_s))
  [ "$inbook" = "$RAN_INBOOK_WANT" ] && echo "RAN_INBOOK_SAME($inbook)" || echo "RAN_INBOOK_CHANGED(got=$inbook want=$RAN_INBOOK_WANT)"
  [ "$((RAN_INBOOK_WANT + RAN_DELTA_WANT + RAN_OWN_WANT))" = "$RAN_A_WANT" ] && echo "THREE_NUM_RECONCILE_OK($RAN_INBOOK_WANT+$RAN_DELTA_WANT+$RAN_OWN_WANT=$RAN_A_WANT)" || echo "THREE_NUM_RECONCILE_BAD"

  echo "--- skip 闭合（⛔ 钉行数／钉文案键⇒A 遍每一行跳过都要在当前源码里有来路；尺子先自校准）---"
  all_skip=$(grep -a -cE '\.\.\. skipped' "$DISC_A")
  echo "A_skip_lines_grep=$all_skip A_summary=$(grep -a -oE 'skipped=[0-9]+' "$DISC_A" | head -1)"
  python3 -B "$SKIP_RULER" --self-test >"$OUT/skip_ruler_selftest_${TAG}.txt" 2>&1
  st_rc=$?
  grep -a -E '^(VERDICT|CAL_ROSTER|CAL_EXPLAIN_RC|CAL_MATCH_NEGATIVE)' "$OUT/skip_ruler_selftest_${TAG}.txt"
  if [ "$st_rc" = "0" ] && grep -aq '^VERDICT|CALIBRATION_BITES' "$OUT/skip_ruler_selftest_${TAG}.txt"; then
    echo "SKIP_RULER_CALIBRATED"
  else
    echo "SKIP_RULER_CALIBRATION_DEAD(rc=$st_rc)"
  fi
  python3 -B "$SKIP_RULER" --root tests --module-prefix tests --explain-from "$DISC_A" >"$OUT/skip_closure_A_${TAG}.txt" 2>&1
  clos_a_rc=$?
  grep -a -E '^(EXPLAIN_SUM|VERDICT|UNMATCHED)' "$OUT/skip_closure_A_${TAG}.txt" | cut -c1-200 | sed 's/^/  /'
  [ "$clos_a_rc" = "0" ] && echo "SKIP_CLOSURE_A_GREEN" || echo "SKIP_CLOSURE_A_RED(rc=$clos_a_rc)"
  clos_lines=$(grep -a -oE 'skip_lines=[0-9]+' "$OUT/skip_closure_A_${TAG}.txt" | head -1 | cut -d= -f2)
  [ "$clos_lines" = "$all_skip" ] && echo "SKIP_TWO_RULERS_SAME($all_skip)" || echo "SKIP_TWO_RULERS_ODD(grep=$all_skip ruler=$clos_lines)"

  echo "--- S 遍闭合（本批件宿主 0 跳⇒尺子对空面按定义返 rc=7，这一腿只吃 unmatched，⛔ 吃 rc）---"
  python3 -B "$SKIP_RULER" --root tests --module-prefix tests --explain-from "$DISC_S" >"$OUT/skip_closure_S_${TAG}.txt" 2>&1
  clos_s_rc=$?
  s_lines=$(grep -a -oE 'skip_lines=[0-9]+' "$OUT/skip_closure_S_${TAG}.txt" | head -1 | cut -d= -f2)
  s_unmatched=$(grep -a -oE 'unmatched=[0-9]+' "$OUT/skip_closure_S_${TAG}.txt" | head -1 | cut -d= -f2)
  echo "S_skip_lines=$s_lines S_unmatched=$s_unmatched S_clos_rc=$clos_s_rc S_skip_lines_grep=$(grep -a -cE '\.\.\. skipped' "$DISC_S")"
  [ "$s_unmatched" = "0" ] && echo "SKIP_CLOSURE_S_GREEN" || echo "SKIP_CLOSURE_S_RED(unmatched=$s_unmatched)"

  echo
  echo "--- 红名单身份（A 遍）---"
  read RED_A RED_A_LINES <<<"$(redlist_of "$DISC_A")"
  echo "A redlist_lines=$RED_A_LINES redlist_md5=$RED_A want=$REDLIST_WANT want_lines=$REDLIST_LINES_WANT"
  sed 's/^/  /' "$DISC_A.redlist"
  known_extra=$(grep -a -c 'test_the_five_registered_sites_are_the_only_ones_touched' "$DISC_A.redlist")
  [ "$known_extra" = "1" ] && echo "KNOWN_EXTRA_OK(1)" || echo "KNOWN_EXTRA_ODD($known_extra)"
  grep -av 'test_the_five_registered_sites_are_the_only_ones_touched' "$DISC_A.redlist" > "$DISC_A.redlist.pre"
  pre_md5=$(md5sum "$DISC_A.redlist.pre" | cut -d' ' -f1)
  [ "$pre_md5" = "$PREDECESSOR_WANT" ] && echo "PREDECESSOR_SUBSET_OK($pre_md5)" || echo "PREDECESSOR_SUBSET_BAD(got=$pre_md5 want=$PREDECESSOR_WANT)"
  rm -f "$DISC_A.redlist.pre"
  [ "$RED_A_LINES" = "$REDLIST_LINES_WANT" ] && echo "REDLIST_LINES_SAME($RED_A_LINES)" || echo "REDLIST_LINES_CHANGED(got=$RED_A_LINES want=$REDLIST_LINES_WANT)"
  if [ "$RED_A" = "$REDLIST_WANT" ]; then
    echo "REDLIST_SAME"
  else
    echo "--- 名单变了⇒C 遍复跑（区分间歇红／真回归）---"
    python3 -B -m unittest discover -s tests -t . >"$DISC_C" 2>&1
    grep -a -E '^Ran |^OK$|^FAILED' "$DISC_C"
    read RED_C RED_C_LINES <<<"$(redlist_of "$DISC_C")"
    echo "C redlist_lines=$RED_C_LINES redlist_md5=$RED_C"
    if [ "$RED_C" = "$REDLIST_WANT" ]; then
      echo "REDLIST_FLAKY_CONFIRMED（A 多出的那几枚在 C 遍消失＝间歇红，⛔ 洗白：已登记）"
      diff "$DISC_A.redlist" "$DISC_C.redlist" | sed 's/^/  FLAKY_DIFF|/'
      c_lines=$(wc -l <"$DISC_C.redlist" | tr -d ' ')
      [ "$c_lines" = "$REDLIST_LINES_WANT" ] && echo "C_REDLIST_LINES_SAME($c_lines)" || echo "C_REDLIST_LINES_ODD($c_lines want=$REDLIST_LINES_WANT)"
      grep -av 'test_the_five_registered_sites_are_the_only_ones_touched' "$DISC_C.redlist" > "$DISC_C.redlist.pre"
      pre_c=$(md5sum "$DISC_C.redlist.pre" | cut -d' ' -f1)
      [ "$pre_c" = "$PREDECESSOR_WANT" ] && echo "C_PREDECESSOR_SUBSET_OK($pre_c)" || echo "C_PREDECESSOR_SUBSET_BAD(got=$pre_c want=$PREDECESSOR_WANT)"
      rm -f "$DISC_C.redlist.pre"
    else
      echo "REDLIST_CHANGED_BOTH_PASSES"
      diff "$DISC_A.redlist" "$DISC_C.redlist" | sed 's/^/  STILL_DIFF|/'
    fi
  fi
  rm -f "$DISC_A.redlist" "$DISC_A.redlist.detail" "$DISC_C.redlist" "$DISC_C.redlist.detail" 2>/dev/null
  echo "redlist_tmp_left=$(ls /tmp/b85_redlist_tmp_* 2>/dev/null | tr '\n' ' ')"

  echo
  echo "--- AST 三数（独立第二尺）---"
  python3 -B scripts/audit_1002/ast_test_census_1002.py >"$CEN" 2>&1
  echo "census_rc=$?"; head -6 "$CEN"
  echo "本件单枚尺：$(python3 -B "$PWD/scripts/audit_1002/b20_ast_denominator.py" "$TEST_FILE" | head -1)"
  echo "读数件：$(wc -c <"$DISC_A") B $DISC_A / $(wc -c <"$DISC_B") B $DISC_B / $(wc -c <"$CEN") B $CEN"

  echo
  echo "--- 跑后：$N_BASE 枚被测件仍回基线（回归⛔ 改生产码）＋现网库反证 ---"
  base_check
  echo "LIVE_DB_AFTER=$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null || echo NO_LIVE_DB)"
  echo "LIVE_DB_SAME=$([ "$BEFORE" = "$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null)" ] && echo YES || echo 'NO(现网有真流量，非本跑所致)')"
  echo "LIVE_DB_WAL_AFTER=$(stat -c '%Y %s' "${LIVE_DB}-wal" 2>/dev/null || echo NO_WAL)"
  rm -rf "$DATA_DIR"
  echo "data_dir_after_rm=$(ls -d "$DATA_DIR" 2>/dev/null || echo ABSENT)"
  echo "leftover_tmp=$(ls -d /tmp/b85_qa_data /tmp/b85_redlist_tmp_* 2>/dev/null | tr '\n' ' ')"
  echo "b85_tmp_present=$(ls /tmp/b85_* 2>/dev/null | wc -l | tr -d ' ')  # 信息位：含本批刻意保留的探针与回滚快照，⛔ 当残留判据"
  date -u '+end_stamp_utc=%Y-%m-%dT%H:%M:%SZ'
} >"$LOG" 2>&1
cat "$LOG"

b_ok=$(grep -a -c '^NO_SKIPFAM_DENOM_SAME' "$LOG")
clos_a_ok=$(grep -a -c '^SKIP_CLOSURE_A_GREEN' "$LOG")
same_ok=$(grep -a -c '^SKIP_TWO_RULERS_SAME' "$LOG")
red_ok=$(grep -a -cE '^(REDLIST_SAME|REDLIST_FLAKY_CONFIRMED)' "$LOG")
flaky=$(grep -a -c '^REDLIST_FLAKY_CONFIRMED' "$LOG")
base_bad=$(grep -a '^baseline_bad=' "$LOG" | head -1 | cut -d= -f2)
tmp_ok=$(grep -a -c '^redlist_tmp_left=$' "$LOG")
clean_ok=$(grep -a -c '^data_dir_after_rm=ABSENT' "$LOG")
cen_ok=$(grep -a -c '^census_rc=0' "$LOG")
ran_a_ok=$(grep -a -c '^RAN_A_SAME' "$LOG")
delta_ok=$(grep -a -c '^RAN_DELTA_SAME' "$LOG")
extra_ok=$(grep -a -c '^KNOWN_EXTRA_OK' "$LOG")
pre_ok=$(grep -a -c '^PREDECESSOR_SUBSET_OK' "$LOG")
rl_lines_ok=$(grep -a -c '^REDLIST_LINES_SAME' "$LOG")
own_ok=$(grep -a -c '^RAN_OWN_SAME' "$LOG")
cal_ok=$(grep -a -c '^SKIP_RULER_CALIBRATED' "$LOG")
clos_s_ok=$(grep -a -c '^SKIP_CLOSURE_S_GREEN' "$LOG")
inbook_ok=$(grep -a -c '^RAN_INBOOK_SAME' "$LOG")
rec_ok=$(grep -a -c '^THREE_NUM_RECONCILE_OK' "$LOG")
# 身份两枚＝「A 遍或确证的 C 遍任一遍成立」（批34 修的门缺陷；两边读数以 A_/C_ 前缀各自留痕）
rl_lines_ok=$( [ "$(grep -a -cE '^REDLIST_LINES_SAME|^C_REDLIST_LINES_SAME' "$LOG")" -ge 1 ] && echo 1 || echo 0 )
pre_ok=$( [ "$(grep -a -cE '^PREDECESSOR_SUBSET_OK|^C_PREDECESSOR_SUBSET_OK' "$LOG")" -ge 1 ] && echo 1 || echo 0 )
if [ "$b_ok$clos_a_ok$same_ok$red_ok$cen_ok$ran_a_ok$rl_lines_ok$extra_ok$pre_ok$delta_ok$own_ok$cal_ok$clos_s_ok$inbook_ok$rec_ok" = "111111111111111" ] \
   && [ "$base_bad" = "0" ] && [ "$tmp_ok" = "1" ] && [ "$clean_ok" = "1" ]; then
  if [ "$flaky" = "1" ]; then
    echo "VERDICT|GREEN-with-FLAKY-登记-B 遍回到 $RAN_B_WANT、在册基数 $RAN_INBOOK_WANT、skip 逐行有源码来路、红名单复跑回号（A 遍有一枚间歇红，已点名）、$N_BASE 枚基线件回基线" | tee -a "$LOG"
  else
    echo "VERDICT|GREEN-B 遍回到 $RAN_B_WANT、在册基数 $RAN_INBOOK_WANT、skip 逐行有源码来路、红名单逐字节同号、$N_BASE 枚基线件回基线、本脚本临时物删净" | tee -a "$LOG"
  fi
  exit 0
fi
echo "VERDICT|RED-minusSkipFam=$b_ok ranA=$ran_a_ok delta=$delta_ok closA=$clos_a_ok same=$same_ok redlist=$red_ok redlist_lines=$rl_lines_ok extra=$extra_ok pre=$pre_ok census=$cen_ok base_bad=$base_bad tmp=$tmp_ok clean=$clean_ok own=$own_ok cal=$cal_ok closS=$clos_s_ok inbook=$inbook_ok rec=$rec_ok" | tee -a "$LOG"
exit 1
