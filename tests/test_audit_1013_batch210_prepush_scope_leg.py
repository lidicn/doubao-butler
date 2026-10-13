# -*- coding: utf-8 -*-
"""批210（2026-10-13）pre-push 的**分支形态判定**＋`--range` 空区间不许当绿。

现读病根（两枚，都有出处）：
· `scripts/git-hooks/pre-push` 用 `[ -z "${remote_oid##*[^0]}" ]` 判「新分支（远端全 0）」——
  这条表达式是**反的**：`##*[^0]` 剥掉的是「最后一个非 0 字符及其之前的一切」，剩下的是**尾随 0 串**。
  真基底 `dd1a775…421e6`（尾字符 6）⇒ 剩下空串 ⇒ 判成「新分支」⇒ 走降级腿；
  而真正的全 0（40 枚 0）⇒ 剩下 40 个 0 ⇒ 判成「有基底」⇒ 把全 0 当 sha 传给 `--range`。
  10-13 现读：这次 `git push origin main` 的 stdout 就是「pre-push: refs/heads/main 是新分支（无远端基底）
  ⇒ 降级为只扫 8d78d3c… 的树」——远端基底明明在（`dd1a775`），逐枚扫整条 range 那条腿**压根没跑**。
· 降级之后的第二个洞：`leak_gate.py --range <取不到的 rev>..HEAD` 时 `git rev-list` 报错走 stderr、
  stdout 空 ⇒ `共 0 枚 commit` ⇒ `[verdict] 待推区间逐枚 0 命中`、rc=0。
  现读命令原文：`python scripts/leak_gate.py --range 0000…0..HEAD --repo .` ⇒ 「共 0 枚 commit」＋rc 0。
  ⇒ **恒绿的闸与没有闸同值**（判例：测不出收益的闸＝没有闸）。全 0 那条路径下，新分支的历史⛔ 看过一眼。

修法两格：判定改用与本档另一处同形的 `case … in *[^0]*)`（含非 0 字符＝真 sha）；
`--range` 取到 0 枚时打印「未量」并返回 2（⛔ 记成绿）。

校准：负控＝真基底必须走 range 腿（不是只测全 0 那一侧，否则改错方向也能全绿）。
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import unittest

TREE = pathlib.Path(__file__).resolve().parents[1]
GATE = TREE / "scripts" / "leak_gate.py"
HOOK = TREE / "scripts" / "git-hooks" / "pre-push"
ZERO = "0" * 40


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=TREE, capture_output=True, text=True,
                       errors="replace")
    return r.stdout.strip()


def _run_gate(range_spec: str) -> subprocess.CompletedProcess:
    return subprocess.run([_python(), str(GATE), "--range", range_spec, "--repo", str(TREE)],
                          cwd=TREE, capture_output=True, text=True, errors="replace")


def _python() -> str:
    return shutil.which("python3") or shutil.which("python") or shutil.which("py") or "python"


def _bash() -> str | None:
    return shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe"
                                    if pathlib.Path("C:/Program Files/Git/bin/bash.exe").exists()
                                    else None)


class RangeLegEmptyIsNotGreenTest(unittest.TestCase):
    def test_unresolvable_base_reports_unmeasured(self):
        """全 0（新分支形态）传进 --range ⇒ ⛔ 0 枚当成 0 命中，必须 rc=2「未量」。"""
        cp = _run_gate(f"{ZERO}..HEAD")
        self.assertIn("取到 0 枚", cp.stdout, cp.stdout[-400:])
        self.assertEqual(cp.returncode, 2, cp.stdout[-400:])
        self.assertIn("未量", cp.stdout, cp.stdout[-400:])

    def test_genuinely_empty_range_reports_unmeasured(self):
        cp = _run_gate("HEAD..HEAD")
        self.assertEqual(cp.returncode, 2, cp.stdout[-400:])
        self.assertIn("未量", cp.stdout, cp.stdout[-400:])

    def test_nonempty_range_still_scans(self):
        """正对照（负控腿）：真有一枚待推时必须照常扫并给出 0/1 判定，⛔ 被新逻辑吞成 2。"""
        cp = _run_gate("HEAD~1..HEAD")
        self.assertIn("共 1 枚", cp.stdout, cp.stdout[-400:])
        self.assertIn("[verdict]", cp.stdout, cp.stdout[-400:])
        self.assertIn(cp.returncode, (0, 1), cp.stdout[-400:])


class PrePushScopeTest(unittest.TestCase):
    def _run_hook(self, local_oid: str, remote_oid: str) -> str:
        bash = _bash()
        self.assertIsNotNone(bash, "取不到 bash ⇒ 本档两格判未量（⛔ 记成绿）")
        stdin = f"refs/heads/main {local_oid} refs/heads/main {remote_oid}\n"
        # ⛔ text=True 喂 input：Windows 下 TextIOWrapper 会把换行翻成 CRLF，read -r 会把
        # 回车留在 remote_oid 尾上 ⇒ 判定被带偏（全 0 那格会因**错的原因**绿过去）。喂字节。
        cp = subprocess.run([bash, str(HOOK)], cwd=TREE, input=stdin.encode("utf-8"),
                            capture_output=True)
        return cp.stdout.decode("utf-8", errors="replace")

    def test_existing_base_selects_range_leg(self):
        head = _git("rev-parse", "HEAD")
        base = _git("rev-parse", "HEAD~1")
        self.assertNotEqual(head, "")
        out = self._run_hook(head, base)
        self.assertIn(f"逐枚扫 {base}..{head}", out, out[-600:])
        self.assertNotIn("降级", out, out[-600:])

    def test_all_zero_base_degrades_and_says_so(self):
        head = _git("rev-parse", "HEAD")
        out = self._run_hook(head, ZERO)
        self.assertIn("无远端基底", out, out[-600:])
        self.assertIn("降级", out, out[-600:])


if __name__ == "__main__":
    unittest.main()
