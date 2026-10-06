r"""批16 文书腿的量尺：台账 §17 里每个行号、每个计数都由本脚本现读，⛔ 我从别处（或从记忆）抄。

四块输出：
  A 已落码的三处（工作树，带行号）
  B 落码前的同一处（`git show HEAD:<file>`，带行号；⛔ checkout、⛔ 手写复刻）
    ⇒ A/B 两面一起给出「报告坐标 vs 现读坐标」的漂移，方向由命中的那一行自己说
  C ⛔ 本批改的兄弟落点（非原子 write_text / 硬编码 /app/data / 现成的 os.replace 正解）
    ⇒ 批17 的分母，判据＝**正则原文**随读数一并打
  D 符号可达性（`get_merge_cache_size` 的定义 vs 调用者）——命中不等于方向

用法（权威树根）：python3 -B scripts/audit_1002/probe_b16_ledger_1002.py
"""
import re
import subprocess
import sys
from pathlib import Path

FILES = ["butler/integrations/bark.py", "butler/integrations/memory_agent.py", "butler/core/aliases.py"]

NEEDLES = {
    "butler/integrations/bark.py": ["/推送加密", "_merge_cache", "flushed_groups", "if ok", "def _encrypt_url", "except Exception"],
    "butler/integrations/memory_agent.py": ["[-limit:]", "alive", '"revoked"', "def recall"],
    "butler/core/aliases.py": ["def _save", "write_text", "os.replace", "tmp", "import os"],
}

# 批17 分母：判据写成 (标签, 正则原文, 单位)；单位＝line（命中行数）|occ（命中次数）
CENSUS_PATTERNS = [
    ("write_text_json_inline", r"\.write_text\(json\.", "line"),
    ("write_text_any", r"\.write_text\(", "line"),
    ("hardcoded_app_data", r"/app/data", "occ"),
    ("atomic_os_replace", r"os\.replace\(", "line"),
]


def git_show(rev: str, path: str) -> str:
    out = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, check=True)
    return out.stdout.decode("utf-8", "replace")


def pointed(text: str, needles) -> None:
    lines = text.splitlines()
    for needle in needles:
        hits = [(i + 1, l.strip()) for i, l in enumerate(lines) if needle in l]
        print(f"  [{needle}] hits={len(hits)}")
        for no, body in hits:
            print(f"    :{no}: {body[:150]}")


def main() -> int:
    print("REPO|" + str(Path.cwd()))
    print("HEAD|" + subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                                   check=True).stdout.decode().strip())
    for path in FILES:
        print(f"### A|{path}|工作树（落码后）")
        pointed(Path(path).read_text(encoding="utf-8"), NEEDLES[path])
        print(f"### B|{path}|git show HEAD（落码前）")
        pointed(git_show("HEAD", path), NEEDLES[path])

    print("### C|批17 分母（扫描根＝butler/，⛔ 全集，只吃 .py）")
    py = sorted(Path("butler").rglob("*.py"))
    print(f"  SCAN_ROOT|butler|py_files={len(py)}")
    for label, pattern, unit in CENSUS_PATTERNS:
        rx = re.compile(pattern)
        total, files = 0, 0
        for f in py:
            n = len(rx.findall(f.read_text(encoding="utf-8", errors="replace")))
            if n:
                files += 1
                total += n
        counted = total if unit == "occ" else sum(
            1 for f in py for l in f.read_text(encoding="utf-8", errors="replace").splitlines() if rx.search(l))
        print(f"  {label}|re={pattern}|unit={unit}|hits={counted}|files={files}|py_files={len(py)}")

    print("### D|符号可达性（生产侧调用者⊖ 定义 ⊖ 我的测试）")
    for sym in ["get_merge_cache_size", "flush_merged"]:
        rows = subprocess.run(["grep", "-rn", sym, "butler", "tests", "scripts", "--include=*.py"],
                              capture_output=True, text=True).stdout.splitlines()
        prod = [r for r in rows if r.startswith("butler/")]
        tst = [r for r in rows if r.startswith("tests/")]
        print(f"  {sym}|all={len(rows)}|butler={len(prod)}|tests={len(tst)}")
        for r_ in prod:
            print(f"    {r_[:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
