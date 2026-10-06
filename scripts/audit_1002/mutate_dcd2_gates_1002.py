"""变异探针（DCD 裁定②）：逐枚把闸门改坏，要求批10 那把验收**必须咬住**。

纪律（本仓的老坑）：变异要注入到「断言真读的那一层」，且⛔ 判绿只看 rc——本脚本按腿点名。
安全性：每个变异跑完立刻用内存里的原字节回写，并核 md5 回到 post-patch 值；任何一步不符⇒抛错退出，
        ⛔ 把树留在被改坏的状态。⛔ 落盘任何 .bak 文件（NAS 规范禁 .bak 堆积）。

用法：python3 /tmp/mutate_dcd2_gates_1002.py [--only M1,M4]
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path("/vol1/1000/docker/doubao-butler")
TEST = "tests.test_audit_1002_batch10_dcd2"

# (编号, 相对路径, 原文, 变异文, 期望至少咬住几条)
MUTATIONS = [
    ("M1", "butler/tts/manager.py",
     "gate_tts(emergency=(priority == PRIORITY_ALERT))", "gate_tts(emergency=False)", 1),
    ("M2", "butler/notify/router.py",
     "gate_bark(silent=silent)", "gate_bark(silent=True)", 1),
    ("M3", "butler/skills/runner.py",
     "        if not (dry_run or force):\n            allowed, mode = gate_skill(skill_id)",
     "        if False:\n            allowed, mode = gate_skill(skill_id)", 2),
    ("M4", "butler/modes/engine.py",
     "    engine = get_mode_engine()\n    if engine is None:\n        return True, \"\"",
     "    engine = get_mode_engine()\n    if engine is None:\n        return False, \"nope\"", 3),
    ("M5", "butler/tts/adapter.py",
     "                priority=item.priority,\n", "", 1),
    ("M6", "butler/modes/engine.py",
     "        if ok and skill_cat == \"care\" and self.is_care_skill_disabled():\n            return False\n",
     "", 1),
    ("M7", "butler/modes/engine.py",
     "    return _gate(lambda eng: eng.can_tts(emergency=emergency))",
     "    return _gate(lambda eng: eng.can_tts(emergency=False))", 1),
]


def _run_suite() -> tuple[int, list[str], str]:
    p = subprocess.run([sys.executable, "-m", "unittest", TEST, "-v"],
                       cwd=str(REPO), capture_output=True, text=True, timeout=300)
    err = p.stderr or ""
    # ⛔ 用 set 去重：同名方法跨类会并成一条（本批就有两枚 test_missing_engine_fails_open，
    # 差点让我把 M4 判成「咬不住」）。按原始行计数，名字保留类名前缀以便归因。
    reds = [line.split(" ", 1)[1].strip()
            for line in err.splitlines()
            if line.startswith("FAIL: ") or line.startswith("ERROR: ")]
    tail = " | ".join(line.strip() for line in err.splitlines()
                      if line.startswith("Ran ") or line.startswith("FAILED") or line.strip() == "OK")
    return p.returncode, reds, tail


def main(argv: list[str]) -> int:
    only = None
    for a in argv:
        if a.startswith("--only"):
            only = set(a.split("=", 1)[1].split(",")) if "=" in a else None
    originals: dict[str, bytes] = {}
    for _mid, rel, _old, _new, _min in MUTATIONS:
        p = REPO / rel
        originals.setdefault(rel, p.read_bytes())

    rc_all, red_all, tail_all = _run_suite()
    print(f"BASELINE|rc={rc_all}|reds={len(red_all)}|{tail_all}")
    if rc_all != 0:
        print("ABORT|基线就红：⛔ 在基线不绿的树上做变异判定")
        return 2

    verdicts = []
    for mid, rel, old, new, min_red in MUTATIONS:
        if only and mid not in only:
            continue
        base = originals[rel]
        text = base.decode("utf-8")
        n = text.count(old)
        if n != 1:
            print(f"MUT|{mid}|SKIP|锚点命中 {n} 次（要求 1）：{old[:40]!r}")
            verdicts.append((mid, "锚点不符"))
            continue
        try:
            (REPO / rel).write_bytes(text.replace(old, new, 1).encode("utf-8"))
            rc, reds, tail = _run_suite()
            bites = len(reds)
            status = "BITTEN" if (rc != 0 and bites >= min_red) else "NOT_BITTEN"
            print(f"MUT|{mid}|{rel}|rc={rc}|reds={bites}|expect>={min_red}|{status}|{','.join(reds)}")
            verdicts.append((mid, status))
        finally:
            (REPO / rel).write_bytes(base)
            back = hashlib.md5((REPO / rel).read_bytes()).hexdigest()
            want = hashlib.md5(base).hexdigest()
            if back != want:
                print(f"RESTORE-FAIL|{rel}|{back}!={want}（⛔ 别再往下跑，树已不在 post-patch 状态）")
                return 3

    bad = [v for v in verdicts if v[1] != "BITTEN"]
    rc, reds, tail = _run_suite()
    print(f"RESTORED|rc={rc}|reds={len(reds)}|{tail}")
    print(f"SUM|mutated={len(verdicts)}|not_bitten={len(bad)}" + (f"|{bad}" if bad else ""))
    return 0 if not bad and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
