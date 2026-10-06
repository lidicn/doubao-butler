#!/usr/bin/env python3
"""批18 变异探针 · 真变异必须被点名腿咬住，等价对照必须存活。

口径（与批17 同一份）：
- real 腿：每条只改一个语义点，跑 tests/test_audit_1002_batch18_silent_raise_paths，
  期望「该腿名单里至少一条」出现在 FAIL:/ERROR: 汇总行里＝咬住；⛔ 用 rc 当判据（rc 只证不绿）。
- control 腿：等价改动（动文书/动日志文案/加 debug 行），期望 9 条全 ok＝存活；
  若对照被打红＝我的腿在测文案⛔ 在测行为。
- 每条都跑 `Ran 9 tests` 分母门；每条还原后逐文件 md5 必须等于打完补丁的真值（restore_bad 一票红）。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIALOG = ROOT / "butler" / "core" / "dialog.py"
SINGLETON = ROOT / "butler" / "tts" / "singleton.py"
GOOD_MD5 = {
    DIALOG: "1159d960069c597c97eee1642a0dff9e",
    SINGLETON: "eb0e6e3053814066fa6586d4b5a97e79",
}
MODULE = "tests.test_audit_1002_batch18_silent_raise_paths"
RAN = "Ran 9 tests"

L1 = "test_worker_task_is_kept_in_a_strong_reference"
L2 = "test_worker_failure_is_logged_as_error"
L3 = "test_transition_returns_value_and_restores_waiting"
L4 = "test_transition_restores_waiting_when_action_raises"
L5 = "test_transition_idle_task_keeps_strong_reference"
L6 = "test_quick_reply_does_not_stick_at_speaking"
L7 = "test_speaking_transition_has_a_single_exit_site"
L8 = "test_expired_pending_does_not_feed_generator"

FINALLY_BLOCK = ("            return result\n        finally:\n"
                 "            self.state.set_state(DialogState.WAITING)\n"
                 "            self._spawn_idle()")
FINALLY_SWAPPED = ("            self.state.set_state(DialogState.WAITING)\n"
                   "            self._spawn_idle()\n            return result\n"
                   "        finally:\n            pass")

MUTANTS = [
    # ---- real：每条一个语义点 ----
    dict(id="S1-worker-task-no-strong-ref", file=SINGLETON, kind="real", expect=[L1],
         old="        _worker_tasks.add(task)\n", new=""),
    dict(id="S2-worker-no-done-callback", file=SINGLETON, kind="real", expect=[L1, L2],
         old="        task.add_done_callback(_on_worker_done)\n", new=""),
    dict(id="S3-worker-death-log-downgraded", file=SINGLETON, kind="real", expect=[L2],
         old='logger.error("TTSQueue worker died', new='logger.warning("TTSQueue worker died'),
    dict(id="S4-registry-never-drained", file=SINGLETON, kind="real", expect=[L1, L2],
         old="    _worker_tasks.discard(task)\n", new=""),
    dict(id="D1-no-speaking-entry", file=DIALOG, kind="real", expect=[L3, L7],
         old="        self.state.set_state(DialogState.SPEAKING)\n        try:", new="        try:"),
    dict(id="D2-settle-only-on-success", file=DIALOG, kind="real", expect=[L4, L6],
         old=FINALLY_BLOCK, new=FINALLY_SWAPPED),
    dict(id="D3-idle-task-no-strong-ref", file=DIALOG, kind="real", expect=[L5],
         old="        _idle_tasks.add(task)\n", new=""),
    dict(id="D4-expired-enters-generator", file=DIALOG, kind="real", expect=[L8],
         old="            if time.time() > _pending_exp:", new="            if False:"),
    # ---- control：等价改动，必须存活 ----
    dict(id="C1-docstring-only", file=DIALOG, kind="control",
         old='"""SPEAKING → action → 无论成败都收尾（第六轮 P2-18）。',
         new='"""SPEAKING → action → 无论成败都收尾（第六轮 P2-18）。\n\n        （对照腿：只多动作文书，⛔ 动语义）'),
    dict(id="C2-extra-debug-line", file=DIALOG, kind="control",
         old="        task = asyncio.create_task(self._return_idle())",
         new='        logger.debug("spawn idle")\n        task = asyncio.create_task(self._return_idle())'),
    dict(id="C3-info-text", file=SINGLETON, kind="control",
         old='logger.info("TTSQueue worker started")', new='logger.info("TTSQueue worker started (ref-kept)")'),
    dict(id="C4-expired-log-text", file=DIALOG, kind="control",
         old='"skill create pending expired for role=%s"', new='"skill create pending expired (b18) for role=%s"'),
]


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def run_suite() -> tuple[int, str]:
    env = dict(os.environ,
               DOUBAO_API_KEY="dummy", DESKPILOT_API_TOKEN="dummy",
               TASK_REPORT_TOKEN="dummy", BUTLER_WEB_PASSWORD="dummy",
               DATA_DIR="/tmp/b18_qa_data")
    r = subprocess.run([sys.executable, "-B", "-m", "unittest", MODULE, "-v"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def red_legs(out: str) -> list[str]:
    legs = []
    for line in out.splitlines():
        for mark in ("FAIL: ", "ERROR: "):
            if line.startswith(mark):
                name = line[len(mark):].split(" (")[0]
                if name not in legs:
                    legs.append(name)
    return legs


def main() -> int:
    date = os.environ.get("PROBE_STAMP", "")
    print(f"PROBE_STAMP {date}")
    for p, want in GOOD_MD5.items():
        if md5(p) != want:
            print(f"PRECHECK_RED|{p.name} md5={md5(p)} 期望 {want}＝树不是打完补丁的真值，⛔ 在此刻度的变异⛔ 算本批证据")
            return 2
    pre_rc, pre_out = run_suite()
    if RAN not in pre_out or pre_rc != 0:
        print(f"BASELINE_RED|rc={pre_rc} ran={'Ran' in pre_out} red={red_legs(pre_out)}")
        return 2
    print(f"BASELINE|rc=0|{RAN}|all-ok")

    real = bitten = controls = survived = misbite = restore_bad = anchor_bad = 0
    for m in MUTANTS:
        p, old, new = m["file"], m["old"], m["new"]
        text = p.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            print(f"ANCHOR|{m['id']} 锚点命中 {n} 次（期望 1）⇒ 该腿未跑，⛔ 计入存活")
            anchor_bad += 1
            continue
        if m["kind"] == "real":
            real += 1
        else:
            controls += 1
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        try:
            rc, out = run_suite()
            legs = red_legs(out)
            ran_ok = RAN in out
            if m["kind"] == "real":
                hit = [x for x in m["expect"] if x in legs]
                extra = [x for x in legs if x not in m["expect"]]
                if hit and ran_ok:
                    bitten += 1
                    print(f"BITE|{m['id']}|expect_hit={','.join(hit)}|other_red={','.join(extra) or '无'}|rc={rc}")
                else:
                    misbite += 1
                    print(f"NOT_BITTEN|{m['id']}|expect={','.join(m['expect'])}|actual_red={','.join(legs) or '无'}|rc={rc}|ran_ok={ran_ok}")
            else:
                if not legs and ran_ok and rc == 0:
                    survived += 1
                    print(f"SURVIVED|{m['id']}|等价对照存活（9 条全 ok）")
                else:
                    misbite += 1
                    print(f"CONTROL_BITTEN|{m['id']}|red={','.join(legs)}|rc={rc}|ran_ok={ran_ok}"
                          f"⇒ 该腿在测文案/结构⛔ 行为")
        finally:
            p.write_text(text, encoding="utf-8")
            if md5(p) != GOOD_MD5[p]:
                restore_bad += 1
                print(f"RESTORE_BAD|{m['id']}|{p.name} md5={md5(p)} 期望 {GOOD_MD5[p]}")

    skipped = "无" if anchor_bad == 0 else f"{anchor_bad}条锚点不唯一"
    print(f"CENSUS|real={real} bitten={bitten} controls={controls} survived={survived} "
          f"misbite={misbite} restore_bad={restore_bad} skipped={skipped}")
    return 0 if (bitten == real and survived == controls and misbite == 0
                 and restore_bad == 0 and anchor_bad == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
