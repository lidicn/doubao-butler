#!/usr/bin/env python3
"""批86 全库红名单分桶尺（腿④-2 的量尺，⛔ 当定性结论用）。

吃什么：`python3 -B -m unittest discover -v` 的一整份输出（本批＝门自己写的 DISC_A 读数件）。
吐什么：每一枚红一行——kind（FAIL／ERROR）、是否 import 期炸（`unittest.loader._FailedTest`）、
        模块名、**块内最后一个异常行**（root_exc）与**第一个**（first_exc）、
        `No module named 'X'` 里的 X（missing_module＝跑法面与生产面的分诊钥匙）。

为什么不只数枚数：门只报 `redlist_lines=87`，而「87 枚怎么来的」⛔ 抄批85 的散文
  （那次 AssertionError=24／ModuleNotFoundError=14 是**宿主面**的账）。这把尺把每枚红的
  根异常现读出来，聚合值⛔ 与任何一批的历史数互抄。

对账（三数必须闭合，任一不合＝这把尺判红，⛔ 迁就读数）：
  解析出的 FAIL 块数 == 声明头 failures=
  解析出的 ERROR 块数 == 声明头 errors=
  `... skipped` 行数  == 声明头 skipped=
声明头本身（`Ran N tests` ＋ `FAILED (...)`）取不到 ⇒ rc=8（无声明＝无从对账，⛔ 报「0 枚红」）。

rc 语义（⛔ 把非 0 一律读成「有 bug」）：
  0＝三数闭合；7＝对账不合（逐格点名）；8＝没有声明头；3＝输入文件读不到；2＝自校准失败。

它测不到什么（出口前自报）：只分桶、不定性——`AssertionError` 那 24 类到底是生产 bug 还是
  测试件过期，要逐枚点开 traceback；块**外**（测试自己的 stdout 噪声）里的异常样子一概不吃；
  `expected failures` 那一档不在 FAIL／ERROR 名册里（本批真面＝1 枚），⛔ 当成漏。

自校准（--self-test，纯内存夹具，⛔ mkdtemp）：
  放行侧＝一份 3 FAIL＋2 ERROR＋skipped 1＋内嵌 stdout 噪声的假面，声明头同号 ⇒ rc 必须 0；
  咬住侧＝同一份删一枚 ERROR 块（声明仍写 2）⇒ rc 必须 7；
  咬住侧＝同一份把一枚块的异常行抹白 ⇒ rc 必须 7 且 UNATTRIBUTED>=1；
  咬住侧＝同一份删掉 `Ran/FAILED` 两行 ⇒ rc 必须 8。
"""
import re
import sys

HDR = re.compile(r"^(FAIL|ERROR): (.*?) \((.+?)\)\s*$")
RULE_A = "=" * 70
RULE_B = "-" * 70
EXC = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Failure|Exit|Interrupt|Interrupted)): ?(.*)$")
RAN = re.compile(r"^Ran (\d+) tests")
FAILED = re.compile(r"^FAILED \((.*?)\)\s*$")
SKIP = re.compile(r"\.\.\. skipped")
NOMOD = re.compile(r"No module named ['\"]([^'\"]+)['\"]")

PROBLEMS = []


def _blocks(text):
    """把 discover 输出切成「红块」列表：每块＝一行 HDR ＋ 到下一条 70 个 = 之前的正文。"""
    lines = text.split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        m = HDR.match(lines[i])
        if not m:
            i += 1
            continue
        body = []
        j = i + 1
        while j < n and not lines[j].startswith(RULE_A):
            body.append(lines[j])
            j += 1
        out.append((m.group(1), m.group(2), m.group(3), body))
        i = j
    return out


def _declared(text):
    ran = None
    dec = None
    for ln in text.split("\n"):
        m = RAN.match(ln)
        if m:
            ran = int(m.group(1))
        m = FAILED.match(ln)
        if m:
            d = {}
            for part in m.group(1).split(","):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    try:
                        d[k.strip()] = int(v.strip())
                    except ValueError:
                        d[part] = None
                else:
                    d[part] = None
            dec = d
    return ran, dec


def parse(text):
    """返回 (rows, stats)。rows 里每枚红的 root_exc／first_exc／missing_module 都是块内现读。"""
    rows = []
    for kind, label, dotted, body in _blocks(text):
        excs = []
        for ln in body:
            m = EXC.match(ln.strip()) or EXC.match(ln)
            if m:
                excs.append((m.group(1), m.group(2)))
        loader = "unittest.loader._FailedTest" in dotted
        if loader:
            mm = re.search(r"tests\.[A-Za-z0-9_.]+", dotted)
            module = mm.group(0) if mm else "-"
        else:
            module = ".".join(dotted.split(".")[:2])
        missing = ""
        for _c, msg in excs:
            g = NOMOD.search(msg)
            if g:
                missing = g.group(1)
        rows.append({
            "kind": kind,
            "loader": loader,
            "module": module,
            "case": label,
            "dotted": dotted,
            "root_exc": excs[-1][0] if excs else "",
            "root_msg": excs[-1][1] if excs else "",
            "first_exc": excs[0][0] if excs else "",
            "missing_module": missing,
            "exc_count": len(excs),
        })
    ran, dec = _declared(text)
    skip_lines = sum(1 for ln in text.split("\n") if SKIP.search(ln))
    stats = {"ran": ran, "declared": dec, "skip_lines": skip_lines,
             "fail": sum(1 for r in rows if r["kind"] == "FAIL"),
             "error": sum(1 for r in rows if r["kind"] == "ERROR"),
             "parsed": len(rows)}
    return rows, stats


def reconcile(rows, stats):
    """三数对账＋逐枚必有根异常。返回 rc：8＝无声明头，7＝任一不合，0＝闭合。"""
    ran, dec = stats["ran"], stats["declared"]
    if ran is None or dec is None:
        print("BAD|no_declaration_header|ran=%s failed_line=%s" % (ran, dec))
        return 8
    d_fail = dec.get("failures", 0) or 0
    d_err = dec.get("errors", 0) or 0
    d_skip = dec.get("skipped", 0) or 0
    bad = 0
    if stats["fail"] != d_fail:
        print("BAD|fail_parsed=%s declared=%s" % (stats["fail"], d_fail)); bad += 1
    if stats["error"] != d_err:
        print("BAD|error_parsed=%s declared=%s" % (stats["error"], d_err)); bad += 1
    if stats["skip_lines"] != d_skip:
        print("BAD|skip_lines=%s declared=%s" % (stats["skip_lines"], d_skip)); bad += 1
    if stats["parsed"] != d_fail + d_err:
        print("BAD|blocks=%s want=%s" % (stats["parsed"], d_fail + d_err)); bad += 1
    noexc = [r for r in rows if not r["root_exc"]]
    if noexc:
        print("UNATTRIBUTED|%d" % len(noexc))
        for r in noexc[:20]:
            print("  UNATTR|kind=%s|module=%s|case=%s" % (r["kind"], r["module"], r["case"][:90]))
        bad += 1
    return 7 if bad else 0


def _report(rows, stats):
    print("SUM|ran=%s|declared_failures=%s|declared_errors=%s|declared_skipped=%s"
          "|parsed_fail=%s|parsed_error=%s|blocks=%s|skip_lines=%s"
          % (stats["ran"], (stats["declared"] or {}).get("failures"),
             (stats["declared"] or {}).get("errors"), (stats["declared"] or {}).get("skipped"),
             stats["fail"], stats["error"], stats["parsed"], stats["skip_lines"]))
    def agg(key, pred=lambda r: True):
        d = {}
        for r in rows:
            if not pred(r):
                continue
            v = r[key] or "-"
            d[v] = d.get(v, 0) + 1
        return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
    for k, v in agg("root_exc"):
        print("BY_ROOT|%s=%d" % (k, v))
    for k, v in agg("missing_module", lambda r: bool(r["missing_module"])):
        print("BY_MISSING_MODULE|%s=%d" % (k, v))
    print("LOADER_IMPORT_FAILS|%d" % sum(1 for r in rows if r["loader"]))
    for k, v in agg("module"):
        print("BY_MODULE|%s=%d" % (k, v))
    for r in rows:
        print("CASE|kind=%s|loader=%s|module=%s|root_exc=%s|first_exc=%s|missing_module=%s|case=%s|msg=%s"
              % (r["kind"], "Y" if r["loader"] else "N", r["module"], r["root_exc"],
                 r["first_exc"], r["missing_module"] or "-",
                 r["case"].replace("|", "/")[:90], r["root_msg"].replace("|", "/")[:120]))


# ---------------- 自校准：同一份材料，放行侧与咬住侧各跑一遍 ----------------
_FIX_HDR = ("======================================================================\n")


def _mk(kind, dotted, exc_line):
    return ("%s: %s (%s)\n%s\n%s\n" % (kind, dotted.split(".")[-1], dotted, RULE_B, exc_line))


_FIXTURE = (
    _FIX_HDR + _mk("FAIL", "tests.test_fake.Module.test_one", "AssertionError: 1 != 2")
    + _FIX_HDR + _mk("FAIL", "tests.test_fake.Module.test_two", "AssertionError: 期望 3 实得 4")
    + _FIX_HDR + _mk("FAIL", "tests.test_fake.Module.test_three", "IndexError: list index out of range")
    + _FIX_HDR + _mk("ERROR", "tests.test_fake.Module.test_four", "TypeError: bad arg")
    + _FIX_HDR
    + ("ERROR: tests.test_fake (unittest.loader._FailedTest.tests.test_fake)\n" + RULE_B + "\n"
       "ImportError: Failed to import test module: tests.test_fake\n"
       "Traceback (most recent call last):\n"
       "  File \"loader.py\", line 1, in _find_test_path\n"
       "ModuleNotFoundError: No module named 'starlette'\n")
    + "\nsome test stdout noise: RuntimeError: 块外噪声，⛔ 被数进来\n"
    + "test_case_a ... skipped '宿主跑不动'\n"
    + "test_case_b ... skipped '宿主跑不动'\n"
    + "test_case_c ... skipped '宿主跑不动'\n"
    + "\nRan 8 tests in 1.000s\n\nFAILED (failures=3, errors=2, skipped=3)\n"
)


def selftest():
    print("--- 自校准：放行侧 ---")
    rows, stats = parse(_FIXTURE)
    want = {"fail": 3, "error": 2, "skip_lines": 3, "parsed": 5, "ran": 8}
    for k, v in want.items():
        got = stats[k]
        ok = "OK" if got == v else "BAD"
        print("CAL_FIXTURE|%s=%s want=%s %s" % (k, got, v, ok))
        if got != v:
            PROBLEMS.append("fixture_%s" % k)
    rc = reconcile(rows, stats)
    print("CAL_POSITIVE|rc=%s want=0" % rc)
    if rc != 0:
        PROBLEMS.append("positive_rc")
    # 三枚幽灵分落三处：少一块／块内无异常行／没有声明头
    g1 = _FIXTURE.replace(_mk("ERROR", "tests.test_fake.Module.test_four", "TypeError: bad arg"), "")
    r1, s1 = parse(g1)
    rc1 = reconcile(r1, s1)
    print("CAL_NEG1_drop_block|rc=%s want=7" % rc1)
    if rc1 != 7:
        PROBLEMS.append("neg1_not_biting")
    g2 = _FIXTURE.replace("AssertionError: 1 != 2", "（异常行被抹白）")
    r2, s2 = parse(g2)
    rc2 = reconcile(r2, s2)
    print("CAL_NEG2_unattributed|rc=%s want=7" % rc2)
    if rc2 != 7:
        PROBLEMS.append("neg2_not_biting")
    g3 = _FIXTURE.replace("\nRan 8 tests in 1.000s\n\nFAILED (failures=3, errors=2, skipped=3)\n", "\n")
    r3, s3 = parse(g3)
    rc3 = reconcile(r3, s3)
    print("CAL_NEG3_no_header|rc=%s want=8" % rc3)
    if rc3 != 8:
        PROBLEMS.append("neg3_not_biting")
    if PROBLEMS:
        print("VERDICT|CALIBRATION_FAILED|problems=%s" % ",".join(PROBLEMS))
        return 2
    print("VERDICT|CALIBRATION_BITES（放行 0＋三枚幽灵各自被咬：少块 7／无异常行 7／无声明头 8）")
    return 0


def main(argv):
    if "--self-test" in argv:
        return selftest()
    path = None
    for i, a in enumerate(argv):
        if a in ("-f", "--file"):
            path = argv[i + 1] if i + 1 < len(argv) else None
    if not path:
        print("ABORT|no_input_file|用法：b86_redlist_bucket_1009.py -f <discover 读数件>")
        return 3
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        print("ABORT|read|%s" % e)
        return 3
    print("INPUT|%s|lines=%d|md5=%s" % (path, text.count("\n") + 1,
                                         __import__("hashlib").md5(text.encode("utf-8")).hexdigest()[:12]))
    rows, stats = parse(text)
    _report(rows, stats)
    rc = reconcile(rows, stats)
    if rc == 0:
        print("VERDICT|BUCKET_CLOSED（三数闭合；⛔ 本 VERDICT 只证「87 枚每枚都有根异常且与声明头同号」，"
              "⛔ 等于「这些红都该存在」⇒ 定性要逐枚点开）")
    else:
        print("VERDICT|BUCKET_RED|rc=%s（对账没闭合⇒先修尺或说明差集，⛔ 迁就读数）" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
