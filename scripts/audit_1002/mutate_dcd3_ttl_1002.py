"""变异探针（DCD 裁定③ 优先级 TTL）：逐枚把新落的腿改坏，要求批11 那把验收**必须咬住**。

纪律（本仓的老坑）：
  · 变异要注入到「断言真读的那一层」；
  · ⛔ 判绿只看 rc——本脚本按腿点名，`reds` 用原始行计数（同名方法跨类会并成一条）；
  · 每枚变异跑完立刻用内存里的原字节回写，并核 md5 回到 post-patch 值；任何一步不符⇒抛错退出，
    ⛔ 把树留在被改坏的状态；⛔ 落盘任何 .bak（NAS 规范禁 .bak 堆积）。

post-patch 基线（现量于 `patch_dcd3_1002.py --apply` 的打印；`queue.py` 此后随两笔修正重钉两次：
`patch_dcd3_linefix_1002.py`＝注释里 `notify/router.py:283`→现读 `:281`（行数/字节⛔ 变，md5 33ec90dd140d→37afd1302452）、
`patch_dcd3_del_zerocaller_1002.py`＝拆掉本批自造的空号 `ttl_for_priority`（640 行/27,115 B → 635 行/26,958 B，
md5 37afd1302452→af4b1d0676e5）。八枚变异的锚点⛔ 触及那两行（一条注释、一条 helper 定义体），
但**基线数字变了就必须全跑一遍**才算数：跑完 `SUM|mutated=8|not_bitten=0`＋`RESTORED|rc=0`）：
  butler/tts/queue.py        md5=af4b1d0676e59f1fb1a06b83d5ab59ad lines=635 bytes=26958 crlf=0 endnl=False
  butler/guard/push_guard.py md5=cdcc2dd1825fde8b319b2aa2e612bda5 lines=419 bytes=18623 crlf=0 endnl=True
  butler/tts/singleton.py    md5=b6d5d646f6657ac247c66d41c27160c4 lines=72  bytes=3122  endnl=True

用法：python3 scripts/audit_1002/mutate_dcd3_ttl_1002.py [--only M1,M3]
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path("/vol1/1000/docker/doubao-butler")
TEST = "tests.test_audit_1002_batch11_dcd3"

AFTER = {
    "butler/tts/queue.py": "af4b1d0676e59f1fb1a06b83d5ab59ad",
    "butler/guard/push_guard.py": "cdcc2dd1825fde8b319b2aa2e612bda5",
    "butler/tts/singleton.py": "b6d5d646f6657ac247c66d41c27160c4",
}

MUTATIONS = [
    ("M1", "butler/tts/queue.py",
     "        ttl = self.config.ttl_for(item.priority) if item.ttl_s is None else float(item.ttl_s)",
     "        ttl = self.config.ttl_s if item.ttl_s is None else float(item.ttl_s)", 3),
    ("M2", "butler/tts/queue.py",
     "            self.on_expired(item, now)",
     "            pass  # MUT", 1),
    ("M3", "butler/tts/queue.py",
     "    PRIORITY_ALERT: 0.0,        # 告警不过期",
     "    PRIORITY_ALERT: 300.0,      # MUT 告警也过期", 2),
    ("M4", "butler/tts/queue.py",
     "    return PRIORITY_LEVEL_NAME[_band_key(priority)]",
     "    return str(priority)  # MUT", 2),
    ("M5", "butler/tts/queue.py",
     "    if priority == PRIORITY_HIGH:\n        return PRIORITY_HIGH\n    return PRIORITY_NORMAL",
     "    if priority == PRIORITY_HIGH:\n        return PRIORITY_HIGH\n    return PRIORITY_HIGH  # MUT 4/5 归高优", 1),
    ("M6", "butler/tts/singleton.py",
     "                    on_expired=on_expired or _audit_expiry_to_guard)",
     "                    on_expired=on_expired)  # MUT 默认审计腿不接", 1),
    ("M7", "butler/guard/push_guard.py",
     "            self._audit(channel, group, priority, DECISION_DROP, reason, \"\", text)",
     "            pass  # MUT 审计不落", 2),
    ("M8", "butler/guard/push_guard.py",
     "# 优先级 → TTL 那张表已拆",
     "PRIORITY_TTL = {\"critical\": 0}  # MUT 空号复活\n# 优先级 → TTL 那张表已拆", 1),
]


def _run_suite() -> tuple[int, list[str], str]:
    p = subprocess.run([sys.executable, "-m", "unittest", TEST, "-v"],
                       cwd=str(REPO), capture_output=True, text=True, timeout=300)
    err = p.stderr or ""
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

    rc0, reds0, tail0 = _run_suite()
    print(f"BASELINE|rc={rc0}|reds={len(reds0)}|{tail0}")
    if rc0 != 0:
        raise SystemExit("基线不绿⇒先修基线，变异探针无意义")

    originals = {rel: (REPO / rel).read_bytes() for rel in AFTER}
    for rel, want in AFTER.items():
        got = hashlib.md5(originals[rel]).hexdigest()
        if got != want:
            raise SystemExit(f"post-patch 基线不符 {rel}: md5={got} != {want}（⛔ 拿这串数当基线跑变异）")

    not_bitten = []
    for mid, rel, old, new, expect in MUTATIONS:
        if only and mid not in only:
            continue
        text = originals[rel].decode("utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"{mid} 锚点命中 {text.count(old)} 次（要求恰好 1）：{rel}")
        mutated = text.replace(old, new, 1)
        ast_check = compiled_ok(mutated)
        if not ast_check:
            raise SystemExit(f"{mid} 变异文语法不过：{ast_check}")
        (REPO / rel).write_bytes(mutated.encode("utf-8"))
        try:
            rc, reds, tail = _run_suite()
        finally:
            (REPO / rel).write_bytes(originals[rel])
            back = hashlib.md5((REPO / rel).read_bytes()).hexdigest()
            if back != AFTER[rel]:
                raise SystemExit(f"{mid} 回写失败 {rel}: md5={back} != {AFTER[rel]}（⛔ 树被留在坏状态）")
        verdict = "BITTEN" if len(reds) >= expect and rc != 0 else "NOT_BITTEN"
        if verdict != "BITTEN":
            not_bitten.append(mid)
        print(f"MUT|{mid}|{rel}|rc={rc}|reds={len(reds)}|expect>={expect}|{verdict}|"
              + ",".join(r.split(".")[-2] + "." + r.split(".")[-1] for r in reds[:4]))

    rc_end, reds_end, tail_end = _run_suite()
    print(f"RESTORED|rc={rc_end}|reds={len(reds_end)}|{tail_end}")
    if rc_end != 0:
        raise SystemExit("复绿失败⇒本探针未收口")
    print(f"SUM|mutated={len([m for m in MUTATIONS if not only or m[0] in only])}"
          f"|not_bitten={len(not_bitten)}" + (("|" + ",".join(not_bitten)) if not_bitten else ""))
    return 0


def compiled_ok(src: str) -> object:
    import ast
    try:
        ast.parse(src)
        return True
    except SyntaxError as e:
        return str(e)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
