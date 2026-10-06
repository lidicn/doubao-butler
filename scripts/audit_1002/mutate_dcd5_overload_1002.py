"""变异探针（DCD 裁定⑤-2 过载语义）：逐枚把新落的腿改坏，要求批12 那把验收**必须咬住**。

跑两档：`tests.test_audit_1002_batch12_dcd5`（本批 16 条腿）＋ `tests.test_v25_pytest_shim`
（既有单测 `tests/test_tts_queue.py` 的 31 条模块级腿由这道闸代跑；只动生产码时它也该红）。
两档 reds 相加＝一枚变异的覆盖面，⛔ 只报「批12 全过」。

纪律（本仓的老坑，一条不移）：
  · 变异只注入**生产码**，⛔ 改测试文件来「配合」；
  · ⛔ 判绿只看 rc——本脚本按腿点名，reds 用原始 `FAIL:/ERROR:` 行计数；
  · 每枚跑完立刻用内存里的原字节回写并核 md5 回到 post-patch 值；任何一步不符 ⇒ 抛错退出，
    ⛔ 把树留在被改坏的状态；⛔ 落盘任何 .bak（NAS 规范禁 .bak 堆积）；
  · 基线不绿 ⇒ 直接退出（变异探针在无意义的前提上跑＝造数）。

post-patch 基线（现量于 `python3 scripts/audit_1002/patch_dcd5_1002.py --apply` 的 APPLIED 行）：
  butler/tts/queue.py md5=3349e9bf681614ff19126a3894de5cc5 lines=656 bytes=28598 crlf=0 endnl=False

用法：python3 scripts/audit_1002/mutate_dcd5_overload_1002.py [--only M1,M3]
"""
from __future__ import annotations

import ast
import hashlib
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path("/vol1/1000/docker/doubao-butler")
SUITES = ("tests.test_audit_1002_batch12_dcd5", "tests.test_v25_pytest_shim")
REL = "butler/tts/queue.py"
AFTER = "3349e9bf681614ff19126a3894de5cc5"

# (编号, 锚点, 变异后, 期望被咬住的腿数下限)
MUTATIONS = [
    ("M1", "            shed = self._shed_lowest_for_room()\n",
     "            shed = 0  # MUT 不让位（既不丢低优，助手也就没人走）\n", 6),
    ("M2", '            self._dropped["overload"] += shed\n',
     '            self._dropped["overload"] += shed + 1  # MUT 把当前这条也记成丢\n', 2),
    ("M3", "            if not already_paused:            # 暂停只从第一次触发起算，⛔ 被后续过载续期\n",
     "            if True:                                 # MUT 每次过载都把暂停窗口续期\n", 1),
    ("M4", "            if droppable == 0:\n                break                        # 只剩告警档，没得丢\n",
     "            if False:\n                break                        # MUT 告警档也进丢弃名单\n", 2),
    ("M5", "            for i, it in enumerate(self._q):\n                if _band(it.priority) == droppable:\n",
     "            for i, it in list(enumerate(self._q))[::-1]:\n                if _band(it.priority) == droppable:\n", 1),
    ("M6", "        if self._is_paused(now) and not overload_pause:\n",
     "        if self._is_paused(now):\n", 2),
    ("M7", '        overload_pause = self._pause_reason == "overload" and self._is_paused(now)\n',
     "        overload_pause = self._is_paused(now)  # MUT 手动暂停也放行告警\n", 1),
    ("M8", "            if overload_pause and item.priority != PRIORITY_ALERT:\n",
     "            if False and item.priority != PRIORITY_ALERT:\n", 2),
    ("M9", "        # 熔断冷却期间：丢弃非 P1\n",
     "        # MUT 把告警 TTL 顺延回暂停结束后（⑤-2 起它当场能播，这句该拆）\n"
     "        if (self._pause_reason == \"overload\" and self._is_paused(now)\n"
     "                and item.priority == PRIORITY_ALERT and ttl > 0):\n"
     "            item.expires_at = max(item.expires_at, self._paused_until + ttl)\n"
     "        # 熔断冷却期间：丢弃非 P1\n", 1),
    ("M10", "            if not already_paused:\n                self._fire_overload()\n",
     "            if not already_paused:\n                self._fire_overload()\n"
     "            return EnqueueResult(False, REASON_DROPPED_PAUSED, None, len(self._q), replaced)  # MUT 当前这条又被吞\n", 6),
    ("M11", '    overload_text: str = "TTS 队列积压，已丢弃若干条低优先级提醒，告警照常播报。"\n',
     '    overload_text: str = "TTS 队列过载，已清空并暂停播放。"\n', 1),
    ("M12", "- 过载：入队后（含新消息）条数 >= overload_threshold → 丢最低优先级档（同带丢最旧）、保 P1 告警档、\n",
     "- 过载：入队后（含新消息）条数 >= overload_threshold → 清空 + 暂停 overload_pause_s\n", 1),
    ("M13", 'REASON_REPLACED = "replaced"\n',
     'REASON_REPLACED = "replaced"\nREASON_OVERLOAD_RESET = "overload_reset"  # MUT 旧原因码复活\n', 2),
    ("M14", '        # 过载暂停期间：新来的非 P1 负载直接挡掉（P1 照收，暂停期间当场能播——见 dequeue）\n'
     '        if self._pause_reason == "overload" and self._is_paused(now) and item.priority != PRIORITY_ALERT:\n',
     '        # MUT 暂停期间连告警一起挡（＝裁定⑤ 点名的「该响的没响」）\n'
     '        if self._pause_reason == "overload" and self._is_paused(now):\n', 2),
]


def _run_suite(test: str) -> tuple[int, list[str], str]:
    env = dict(os.environ, DATA_DIR="/tmp/mut_b12_data")
    p = subprocess.run([sys.executable, "-m", "unittest", test, "-v"],
                       cwd=str(REPO), capture_output=True, text=True, timeout=300, env=env)
    err = p.stderr or ""
    reds = [line.split(" ", 1)[1].strip()
            for line in err.splitlines()
            if line.startswith("FAIL: ") or line.startswith("ERROR: ")]
    tail = " | ".join(line.strip() for line in err.splitlines()
                      if line.startswith("Ran ") or line.startswith("FAILED") or line.strip() == "OK")
    return p.returncode, reds, tail


def _run_all() -> tuple[int, list[str], list[str]]:
    rcs, reds, tails = 0, [], []
    for t in SUITES:
        rc, r, tail = _run_suite(t)
        rcs += rc
        reds += r
        tails.append(tail)
    return rcs, reds, tails


def main(argv: list[str]) -> int:
    only = None
    for a in argv:
        if a.startswith("--only"):
            only = set(a.split("=", 1)[1].split(",")) if "=" in a else None

    rc0, reds0, tails0 = _run_all()
    print(f"BASELINE|rc={rc0}|reds={len(reds0)}|{' ; '.join(tails0)}")
    if rc0 != 0:
        raise SystemExit("基线不绿 ⇒ 先修基线，变异探针无意义")

    original = (REPO / REL).read_bytes()
    got = hashlib.md5(original).hexdigest()
    if got != AFTER:
        raise SystemExit(f"post-patch 基线不符 {REL}: md5={got} != {AFTER}（⛔ 拿这串数当基线跑变异）")

    not_bitten = []
    run_count = 0
    for mid, old, new, expect in MUTATIONS:
        if only and mid not in only:
            continue
        run_count += 1
        text = original.decode("utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"{mid} 锚点命中 {text.count(old)} 次（要求恰好 1）：{old[:48]!r}")
        mutated = text.replace(old, new, 1)
        try:
            ast.parse(mutated)
        except SyntaxError as e:
            raise SystemExit(f"{mid} 变异文语法不过：{e}")
        (REPO / REL).write_bytes(mutated.encode("utf-8"))
        try:
            rc, reds, _tails = _run_all()
        finally:
            (REPO / REL).write_bytes(original)
            back = hashlib.md5((REPO / REL).read_bytes()).hexdigest()
            if back != AFTER:
                raise SystemExit(f"{mid} 回写失败 {REL}: md5={back} != {AFTER}（⛔ 树被留在坏状态）")
        verdict = "BITTEN" if len(reds) >= expect and rc != 0 else "NOT_BITTEN"
        if verdict != "BITTEN":
            not_bitten.append(mid)
        print(f"MUT|{mid}|rc={rc}|reds={len(reds)}|expect>={expect}|{verdict}|"
              + ",".join(r.split(".")[-2] + "." + r.split(".")[-1] for r in reds[:5]))

    rc_end, reds_end, tails_end = _run_all()
    print(f"RESTORED|rc={rc_end}|reds={len(reds_end)}|{' ; '.join(tails_end)}")
    if rc_end != 0:
        raise SystemExit("复绿失败 ⇒ 本探针未收口")
    print(f"SUM|mutated={run_count}|not_bitten={len(not_bitten)}"
          + (("|" + ",".join(not_bitten)) if not_bitten else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
