r"""批15 变异探针：每个变异体必须被「点名的腿」咬住，并留一枚必须存活的对照变异。

用法：python3 scripts/audit_1002/mutate_b15_1002.py            # 全跑
输出同时落 workorders/readings/1003b15/mutation_probe_b15.txt（由外层 tee 负责）。

它测不到什么：
  - 只跑批15 那一档（17 腿）。跨档的连带破坏由全量回归那一步负责，不在本探针的判据里。
  - 变异体是「等价类」的枚举，不是全空间：只覆盖我点名的 7 处公式/开关形状。
  - CONTROL_* 是等价变异体（改了字面不改语义），判据反过来：它们必须存活。
    上一轮把「改局部变量名」当对照，结果它咬了——因为那个局部变量下一行还在读，
    那是破坏性变异不是对照；本轮两条对照都改成可证明语义不变的字面重排。
"""
import hashlib
import re
import subprocess
import sys

SUITE = ["tests.test_audit_1002_batch15_env_bool"]
# 批15 GREEN 那把尺的原样读数：「Ran 17 tests」
EXPECT_RAN = 17
FAIL_RE = re.compile(r"^(FAIL|ERROR): (\S+)", re.M)
RAN_RE = re.compile(r"^Ran (\d+) tests", re.M)

BASELINE = {
    "butler/config.py": "5034b71f6fa5eab4bf51cc3b9054fbf8",
    "butler/tts/base.py": "3419f28816b8057513aa2fb9206722da",
    "butler/tts/manager.py": "69d9ffa64a371bead4dd5bfc8abf20da",
    "butler/tts/edge_tts.py": "3da21cf511f22019dceaaf7d6f6471b6",
    "butler/tts/kokoro.py": "48d6df118005a2f6bfdf0231e4282587",
    "butler/tts/nowvoice_tts.py": "a0ecf93f66507fe1785ef2a054ba1983",
}

MUTANTS = [
    ("ENVBOOL_EMPTY_BACK", "butler/config.py",
     '    if v in ("0", "false", "no", "off"):', '    if v in ("0", "false", "no", "off", ""):'),
    # 把 P0-2 的原始两行形状整段复原（假元组 + 恒假三元），必须被咬。
    ("ENVBOOL_ORIGINAL_BUG_BACK", "butler/config.py",
     '    if v in ("0", "false", "no", "off"):\n        return False\n',
     '    if v in ("0", "false", "no", "off", ""):\n        return False if v == "" else False\n'),
    ("KEY_DROPS_SPEED", "butler/tts/base.py",
     '{text}|{voice}|{engine}|{speed}', '{text}|{voice}|{engine}'),
    ("READER_HARDCODES_SPEED", "butler/tts/manager.py",
     "cache_filename(text, voice, engine, speed)", "cache_filename(text, voice, engine, 1.0)"),
    ("EDGE_HARDCODES_SPEED", "butler/tts/edge_tts.py",
     'cache_filename(text, voice, "edge-tts", speed)', 'cache_filename(text, voice, "edge-tts", 1.0)'),
    ("KOKORO_HARDCODES_SPEED", "butler/tts/kokoro.py",
     'cache_filename(text, voice, "kokoro", speed)', 'cache_filename(text, voice, "kokoro", 1.0)'),
    ("NOWVOICE_HARDCODES_SPEED", "butler/tts/nowvoice_tts.py",
     'cache_filename(text, voice, "nowvoice", speed)', 'cache_filename(text, voice, "nowvoice", 1.0)'),
    # 对照变异（等价类，必须存活）：只把三元塞回一个本来就走不到空的分支。
    # 上一轮实测：这条 rc=0/ran=17 存活——它不是测试缺口，是等价变异体。
    ("CONTROL_EQUIV_TERNARY_ONLY", "butler/config.py",
     '    if v in ("0", "false", "no", "off"):\n        return False\n',
     '    if v in ("0", "false", "no", "off"):\n        return False if v == "" else False\n'),
    # 对照变异：集合成员顺序改写，语义不变，任何腿都不该咬。
    ("CONTROL_EQUIV_TUPLE_REORDER", "butler/config.py",
     '    if v in ("0", "false", "no", "off"):',
     '    if v in ("off", "no", "false", "0"):'),
]


def md5_of(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def run_suite():
    p = subprocess.run([sys.executable, "-m", "unittest", *SUITE, "-v"],
                       capture_output=True, text=True)
    blob = p.stdout + p.stderr
    names = sorted({n for _kind, n in FAIL_RE.findall(blob)})
    ran = RAN_RE.search(blob)
    return p.returncode, names, int(ran.group(1)) if ran else -1


def main():
    for rel, want in BASELINE.items():
        got = md5_of(rel)
        if got != want:
            print("BASELINE_DRIFT|%s|got=%s|want=%s" % (rel, got, want))
            return 2
    survivors, bitten_total, bad_restore = [], 0, []
    control_total, control_survived = 0, 0
    for name, rel, old, new in MUTANTS:
        raw = open(rel, "rb").read()
        if raw.count(old.encode("utf-8")) != 1:
            print("MUTANT_ANCHOR|%s|%s|hits=%d" % (name, rel, raw.count(old.encode("utf-8"))))
            return 3
        tampered = raw.replace(old.encode("utf-8"), new.encode("utf-8"), 1)
        if tampered == raw:
            print("MUTANT_NOOP|%s" % name)
            return 4
        open(rel, "wb").write(tampered)
        rc, names, ran = run_suite()
        open(rel, "wb").write(raw)
        if md5_of(rel) != BASELINE[rel]:
            bad_restore.append(rel)
        if name.startswith("CONTROL"):
            control_total += 1
            if rc == 0 and ran == EXPECT_RAN:
                control_survived += 1
                print("%s|CONTROL_SURVIVED(ok)|rc=%d|ran=%d|legs=%s"
                      % (name, rc, ran, ",".join(names) or "无"))
            else:
                print("%s|CONTROL_BITTEN(红)|rc=%d|ran=%d|want_ran=%d|legs=%s"
                      % (name, rc, ran, EXPECT_RAN, ",".join(names) or "无"))
        else:
            if rc != 0 and names:
                bitten_total += 1
                print("%s|BITTEN|legs=%d|%s" % (name, len(names), ",".join(names)))
            else:
                survivors.append(name)
                print("%s|SURVIVED(红)|rc=%d|ran=%d" % (name, rc, ran))
    real = len(MUTANTS) - control_total
    print("CENSUS|mutants=%d bitten=%d controls=%d survived=%d survivors=%s|restore_bad=%s"
          % (real, bitten_total, control_total, control_survived,
             survivors or "无", bad_restore or "无"))
    ok = (not survivors and not bad_restore
          and bitten_total == real and control_survived == control_total > 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
