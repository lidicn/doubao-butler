"""批14 变异探针：`status()` 的键聚合每条分支都必须被某枚已知腿咬住。

跑法（权威树根）：`python3 -B scripts/audit_1002/mutate_b14_status_1002.py`
判定＝对 `butler/triggers/engine.py` 逐个注入形状各异「看似合理但错」的读法，跑
`tests.test_audit_1002_batch14_status_key` ＋ `tests.test_audit_1002_batch4`（后者是既有
`status()` 契约腿，用来抓「我把无 member 那条路改坏」），任一用例红＝BITTEN，全绿＝SURVIVED＝缺腿。

它测不到什么（先答这句再出口）：
  · ⛔ 碰库、碰网络、碰现网进程——本探针只在盘上改 `status()` 的显示逻辑；写侧 `:244`/`:358`
    的键生成、`repo.save_cooldowns` 的落库、熔断/防抖闸门一概不注入；
  · 注入的是「文本形状」的错，不是「运行期状态」的错（比如 `_last_fired` 被别处写脏）；
  · 若某枚变异体只有 `test_sibling_prefix_is_not_counted` 咬住，那说明我把笼头挂在一条腿上，
    这条腿被删掉时该变异体就逃逸——所以每枚变异体都点名咬它的腿，删腿时按这张表对账。

控制腿 `NOOP_RENAME_LOCAL` 必须 **SURVIVED**：它证明这把尺会报「活下来」，不是无论注入什么都判红。
"""
import hashlib
import re
import shutil
import subprocess
import sys

ENG = "butler/triggers/engine.py"
BASELINE = {"eng": "866ee386117f94a08fbedf834c57e5c1"}
SUITE = ["tests.test_audit_1002_batch14_status_key", "tests.test_audit_1002_batch4"]
READ = ('            tid = trig["id"]\n'
        '            last = max([v for k, v in self._last_fired.items()\n'
        '                        if k == tid or k.startswith(tid + ":")] or [0])\n')

MUTANTS = [
    ("STATUS_BARE_ID",
     "回到 §14-6 的原 bug：只用裸 id 查",
     READ,
     '            last = self._last_fired.get(trig["id"], 0)\n'),
    ("STATUS_ANY_PREFIX",
     "naive 前缀：把兄弟触发器 trig_ext 的键算进 trig 名下",
     'if k == tid or k.startswith(tid + ":")] or [0])',
     'if k == tid or k.startswith(tid)] or [0])'),
    ("STATUS_MEMBER_ONLY",
     "只认 member 键：把无 member 那条路（裸 id）丢回 0",
     'if k == tid or k.startswith(tid + ":")] or [0])',
     'if k.startswith(tid + ":")] or [0])'),
    ("STATUS_OLDEST_WINS",
     "取最旧一枚：显示与执行反向（对刚火过的那位谎报随时可火）",
     "            last = max([v for k",
     "            last = min([v for k"),
    ("STATUS_NO_ZERO_FALLBACK",
     "空名下不兜底：没火过的触发器直接 ValueError",
     "] or [0])",
     "])"),
    ("STATUS_MONOTONIC_NOW",
     "跨时基相减（批4 那个坑的另一种犯法）",
     "        now = time.time()   # 与 _last_fired 同域；跨时基相减会报出「还剩 55 年」",
     "        now = time.monotonic()   # mutant"),
    ("STATUS_NO_CLAMP",
     "去掉 max(0.0, …)：过期冷却报负数",
     '"cooldown_remaining": max(0.0, cd - (now - last)) if last > 0 else 0.0,',
     '"cooldown_remaining": (cd - (now - last)) if last > 0 else 0.0,'),
    ("NOOP_RENAME_LOCAL",
     "控制腿：只改局部变量名，行为不变 ⇒ 必须 SURVIVED",
     READ,
     READ.replace("tid", "cdbase")),
]

FAIL_RE = re.compile(r"^(FAIL|ERROR): (\S+)", re.M)
RAN_RE = re.compile(r"^Ran (\d+) tests", re.M)


def sha(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def run_suite(timeout=300):
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest"] + SUITE,
                          capture_output=True, text=True, timeout=timeout)
    out = proc.stdout + proc.stderr
    names = sorted({m.group(2) for m in FAIL_RE.finditer(out)})
    ran = RAN_RE.search(out)
    return proc.returncode, names, (ran.group(1) if ran else "?"), out[-400:]


def main():
    original = open(ENG, "rb").read()
    if hashlib.md5(original).hexdigest() != BASELINE["eng"]:
        raise SystemExit("BASELINE|eng=%s 期望=%s——先回权威树重读，⛔ 对着别的树跑尺"
                         % (sha(ENG), BASELINE["eng"]))
    rc, names, ran, err = run_suite()
    if rc != 0:
        raise SystemExit("BASELINE_RED|%s|%s" % (names, err))
    print("BASELINE|GREEN|Ran=%s（%s）" % (ran, ",".join(SUITE)))

    survived, bitten = [], []
    for name, why, old, new in MUTANTS:
        text = original.decode("utf-8")
        if text.count(old) != 1:
            print("%-24s|ANCHOR_BROKEN|%d（本枚作废，⛔ 当成咬住或逃逸记）" % (name, text.count(old)))
            continue
        open(ENG, "wb").write(text.replace(old, new, 1).encode("utf-8"))
        try:
            rc, names, ran, err = run_suite()
        finally:
            open(ENG, "wb").write(original)
        if hashlib.md5(open(ENG, "rb").read()).hexdigest() != BASELINE["eng"]:
            raise SystemExit("RESTORE_FAILED|%s" % sha(ENG))
        if name == "NOOP_RENAME_LOCAL":
            verdict = "CONTROL_SURVIVED" if rc == 0 else "CONTROL_RED"
        else:
            verdict = "BITTEN" if rc != 0 else "SURVIVED"
        print("%-24s|%-16s|Ran=%s legs=%s|%s" % (name, verdict, ran, ",".join(names) or "-", why))
        (bitten if verdict == "BITTEN" else survived).append((name, verdict))

    open(ENG, "wb").write(original)
    if sha(ENG) != BASELINE["eng"]:
        raise SystemExit("FINAL_RESTORE|%s" % sha(ENG))
    real_survivors = [n for n, v in survived if v == "SURVIVED"]
    print("CENSUS|bitten=%d control_ok=%s survivors=%s"
          % (len([n for n, v in bitten if v == "BITTEN"]),
             "CONTROL_SURVIVED" in [v for n, v in survived], real_survivors or "无"))
    if real_survivors or "CONTROL_SURVIVED" not in [v for n, v in survived]:
        print("VERDICT|RED——有变异体逃逸或控制腿失效，本批不许判收")
        sys.exit(1)
    print("VERDICT|GREEN|每枚变异体都有点名腿，控制腿按形状活下来")


main()
