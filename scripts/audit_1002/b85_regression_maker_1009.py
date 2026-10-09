#!/usr/bin/env python3
"""批85 回归门派生件：从 scripts/audit_1002/run_b36_regression_1005.sh 机械派生
scripts/audit_1002/run_b85_regression_1009.sh。

机制（discover 名册、A/B/S 三遍、减法对账、红名单身份、基线点名、临时物反证）逐字节继承，
只有本批差异变。派生规矩沿用批36 那份：
  1) 文件头（shebang 之后到「# ── 继承批」标记之前）整段换成批85 的说明；
  2) 常量行按整行正则重写，每条 ⛔ 命中数≠1；
  3) 散文与路径用精确短串替换，每条声明命中数，实际不等即 abort（不写盘）；
  4) 落盘后打印残留 b36/批36 锚点逐行点名（在册引用是刻意保留的，⛔ 假装清零）。
本批 ⛔ 新增判据腿，也 ⛔ 放宽任何一枚：
  本批 own 件是纯内存夹具（0 格宿主跳过）⇒ skip 分母不加键；
  B33/B36 两把 REPO_ROOT 指针与 B24..B36 七把 skip 键全部原样留着（它们现在数的是**在册件**）。
"""
import hashlib
import os
import re
import sys

SRC = "scripts/audit_1002/run_b36_regression_1005.sh"
DST = "scripts/audit_1002/run_b85_regression_1009.sh"
MARKER = "# ── 继承批29/批32 的机制约束"
PROBLEMS = []
PAIRS = []


def md5(b):
    return hashlib.md5(b).hexdigest()


def leg(name, cond, detail=""):
    print(("LEG|OK|" if cond else "LEG|RED|") + name + ("|" + detail if detail else ""))
    if not cond:
        PROBLEMS.append(name)
    return cond


def whole_line(text, anchor, new):
    pat = re.compile(r"(?m)^" + anchor + r".*$")
    hits = pat.findall(text)
    if not leg("whole_line[" + anchor[:24] + "]", len(hits) == 1, "hits=%d" % len(hits)):
        return text
    return pat.sub(lambda _m: new, text, count=1)


def replace(text, old, new, want, label):
    hits = text.count(old)
    if not leg("pair[%s]" % label, hits == want, "hits=%d want=%d" % (hits, want)):
        return text
    return text.replace(old, new)


HEADER = r'''# 批85 收口回归：全库 A 遍 + 剔跳过家族 B 遍 + 本批件单跑 S 遍 + skip 分母 + 红名单身份 + AST 三数。
# 本件由 scripts/audit_1002/b85_regression_maker_1009.py 从批36 那份机械派生（整行常量重写＋短串逐对替换，
# 每对命中数必须＝声明值），⛔ 手抄。
# 命名口径：本件与读数目录用 `_1009`/`1009_b85`＝本次收窗的 **UTC 日**（跨钟比一律 @epoch；
# 被测件文件名里的 `1009` 就是它的落盘日，与本次收窗同日，无跨日错配）。
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
#      ⛔ starlette、⛔ 网络、⛔ DB）⇒ **本批不新增 skip 键**，OWN_SKIP_WANT=0。
#   5) 三数对账换号（下面的数是先写死的**预测值**，由本次真跑判生死；
#      跑红就登记「预测错」⛔ 改判据迁就读数）：
#        RAN_A_WANT=922        批36 的 918 ＋ 本批 4 格
#        RAN_B_WANT=901        批36 的 897 ＋ 本批 4 格（本批件在这一遍里，⛔ 当在册基数读）
#        RAN_OWN_WANT=4        S 遍单跑本批件＝AST 总 4 同号
#        OWN_SKIP_WANT=0       宿主 0 跳（见第 4 段）
#        RAN_INBOOK_WANT=897   在册基数 = B 遍 901 − 本批 4 ＝ 批36 的 B 遍现量
#        RAN_DELTA_WANT=21     = 922 − 901（跳过家族四枚的贡献，⛔ 随本批变）
#        SKIP_OTHER_WANT=19    与批36 同号（本批 own=0，⛔ 往里加键）
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
'''

NEW_LINES = {}


def main():
    t = open(SRC, encoding="utf-8").read()
    print("SOURCE|%s|md5=%s|bytes=%d|CR=%d|LF=%d" % (
        SRC, md5(t.encode("utf-8")), len(t), t.count("\r"), t.count("\n")))
    leg("source_is_lf", t.count("\r") == 0, "CR=%d" % t.count("\r"))
    i = t.index(MARKER)
    head_marker_line = t.rfind("\n", 0, i) + 1
    body = t[head_marker_line:]
    leg("marker_hit_once", body.count(MARKER) == 1, "n=%d" % body.count(MARKER))

    # 1) 头部整段替换（shebang 单独留着：它不属于「头部说明」）
    t = "#!/usr/bin/env bash\n" + HEADER + body
    leg("shebang_first_line", t.startswith("#!/usr/bin/env bash\n"), t[:24])

    # 2) 常量行整行重写（每条命中数必须＝1）
    t = whole_line(t, r"OUT=", "OUT=workorders/readings/1009_b85")
    t = whole_line(t, r"TEST_FILE=", "TEST_FILE=tests/test_audit_1009_batch85_anomaly_dedup_prune.py")
    t = whole_line(t, r"TEST_MD5_WANT=",
                   "TEST_MD5_WANT=84edbd47f4021ce22be04b380c6d6269   # 本批验收件（部署树＋E 镜像两面同值；容器无 tests/ 目录，⛔ 第三面）")
    t = whole_line(t, r"RAN_A_WANT=", "RAN_A_WANT=922   # 批36 的 918 ＋ 本批 4 格（算式见文件头第 5 段）")
    t = whole_line(t, r"RAN_B_WANT=", "RAN_B_WANT=901   # 批36 的 897 ＋ 本批 4 格（本批件在这一遍里，⛔ 当在册基数读）")
    t = whole_line(t, r"RAN_OWN_WANT=", "RAN_OWN_WANT=4   # S 遍单跑本批件＝AST 总 4 同号（三把尺对账见文件头第 4 段）")
    t = whole_line(t, r"OWN_SKIP_WANT=", "OWN_SKIP_WANT=0   # 本批件纯内存夹具，宿主 0 跳⇒本批⛔ 新增 skip 键")
    t = whole_line(t, r"B33_SKIP_WANT=", "B33_SKIP_WANT=2   # 在册件批33 在宿主的 2 格方法内跳过（t5/t6 要 starlette）")
    t = whole_line(t, r"B36_SKIP_WANT=", "B36_SKIP_WANT=4   # 在册件批36 的 4 格方法内跳过（PanelHonesty 要 starlette；本批继承这枚键）")
    t = whole_line(t, r"B36_SKIP_KEY=", "B36_SKIP_KEY='system_routes 导不进来：'   # 在册件批36 的 4 格（⛔ 本批键，随批36 件一起留）")
    t = whole_line(t, r"RAN_INBOOK_WANT=", "RAN_INBOOK_WANT=897   # 在册基数 = B 遍 901 − 本批 4 ＝ 批36 的 B 遍现量 897")
    t = whole_line(t, r"RAN_DELTA_WANT=", "RAN_DELTA_WANT=21   # 922 − 901 ＝ 跳过家族四枚的贡献（批29 的 14 ＋ 批32 的 7），与本批无关")
    t = whole_line(t, r"SKIP_OTHER_WANT=", "SKIP_OTHER_WANT=19   # 与批36 同号（本批 own=0，⛔ 往里加键）")

    # 3) 基线名册：本批被测面一枚重钉（枚数不变）
    t = replace(t, "712982b834a41dbacf77e308ed6396db butler/timeseries/anomaly.py",
                "6a90bd132874272a0a9c617a4207f00d butler/timeseries/anomaly.py",
                1, "base_anomaly_repin")
    old_row = "712982b834a41dbacf77e308ed6396db butler/timeseries/anomaly.py"
    new_row = "6a90bd132874272a0a9c617a4207f00d butler/timeseries/anomaly.py"
    leg("base_anomaly_new_row_once", t.count(new_row) == 1, "n=%d" % t.count(new_row))

    # 4) 短串对：路径／时刻／批号（每条声明命中数）
    for old, new, want, label in [
        ("scripts/audit_1002/run_b36_regression_1005.sh", "scripts/audit_1002/run_b85_regression_1009.sh", 2, "self_path"),
        ("sed -n '300,326p' butler/config.py", "sed -n '315,331p' butler/config.py", 1, "gate_key_window_repin"),
        ("/tmp/b36_qa_data", "/tmp/b85_qa_data", 2, "qa_data"),
        ("/tmp/b36_redlist_tmp_", "/tmp/b85_redlist_tmp_", 3, "redlist_tmp"),
        ("b36_tmp_present", "b85_tmp_present", 1, "tmp_present"),
        ("/tmp/b36_*", "/tmp/b85_*", 1, "tmp_glob"),
        ('"=== 批36 收口回归 ==="', '"=== 批85 收口回归 ==="', 1, "title"),
        ("                  # ⛔ 与 OWN_SKIP_WANT 共用一枚常量：批33 own=2 而本批 own=4 ⇒ 必须分家",
         "                  # ⛔ 与 OWN_SKIP_WANT 共用一枚常量：批33 own=2／批36 own=4 而本批 own=0 ⇒ 三把必须分家",
         1, "b33_comment"),
        ("#   ⇒ 减法对账两头必须同一个机制：B 遍只吃顶层 tests/test_*.py，才配和在册基数比（本批的 884 由\n"
         "#     S 遍从 B 遍里减出：884 = 897 − 13；⛔ 直接把 B 遍的 897 当在册基数读，它含本批 13 格）。",
         "#   ⇒ 减法对账两头必须同一个机制：B 遍只吃顶层 tests/test_*.py，才配和在册基数比（本批的 897 由\n"
         "#     S 遍从 B 遍里减出：897 = 901 − 4；⛔ 直接把 B 遍的 901 当在册基数读，它含本批 4 格）。",
         1, "inbook_comment"),
    ]:
        t = replace(t, old, new, want, label)

    # 5) 写盘前方向检查：本批 own 件⛔ 再吃批36 的模块名（继承腿 R-a 用的是 $TEST_MOD，此处只验残留）
    b36_live = [(n + 1, ln) for n, ln in enumerate(t.split("\n"))
                if "batch36_job_isolation" in ln and not ln.lstrip().startswith("#")]
    leg("b36_testmod_not_in_live_leg", len(b36_live) == 0, "live_lines=%s" % [n for n, _ in b36_live])

    if PROBLEMS:
        print("ABORT|NO_WRITE|problems=%s" % ",".join(PROBLEMS))
        return 2

    # 5.5) 目标里已有批86 腿② 的「名册闭合」四腿⇒⛔ 再写：本件是从批36 那份**机械派生**的，
    #      重跑会把四腿退回八把 *_SKIP_KEY ＋ 四枚 *_SKIP_WANT 的行数钉值判据（那组常量已删）。
    #      真要重造：先手工把那一块摘掉再跑本件，跑完重新接线。
    if os.path.exists(DST):
        cur = open(DST, encoding="utf-8").read()
        if "SKIP_CLOSURE_A_GREEN" in cur or "SKIP_RULER_CALIBRATED" in cur:
            print("ABORT|NO_WRITE|reason=dst_has_b86_closure_legs(md5=%s)" % md5(cur.encode("utf-8")))
            return 3

    with open(DST, "w", encoding="utf-8", newline="") as fh:
        fh.write(t)
    b = t.encode("utf-8")
    print("WROTE|%s|bytes=%d|lines=%d|CR=%d|md5=%s" % (DST, len(b), t.count("\n"), b.count(b"\r"), md5(b)))

    # 6) 残留锚点逐行点名（在册引用是刻意保留的，⛔ 假装清零）
    print("--- 残留 b36/批36 锚点 ---")
    left = [(n + 1, ln) for n, ln in enumerate(t.split("\n")) if "b36" in ln.lower() or "批36" in ln]
    for n, ln in left:
        print("  RESIDUAL|%d|%s" % (n, ln[:150]))
    print("RESIDUAL_COUNT|%d" % len(left))
    print("OLD_BASELINE_ROW|present=%d" % t.count(old_row))
    print("NEW_BASELINE_ROW|present=%d" % t.count(new_row))
    print("B35_ROW_ANOMALY_PREPATCH|present=%d  # 只在文件头第 6 段散文里出现＝刻意留痕，⛔ 表行形"
          % t.count("712982b834a41dbacf77e308ed6396db butler/timeseries/anomaly.py"))
    print("VERDICT|DERIVED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
