"""批13d 一次性落件器（**只补空行，一字内容不动**）：把批13 落码器吃掉的行间空行还回去。

根因在工具不在码：`patch_b13_cooldown_1002.py` 的 payload 切法是
`[l + "\\n" for l in new.split("\\n") if l != ""]`——那个 `if l != ""` 把我 payload 里的空行
**全部静默丢掉**了。语法照样过（Python 不要求 def 前有空行），所以我当时看到的 GREEN 是真的，
但盘上的形状不是我写的那份：

  AST 现读（15:54Z 活树 vs `git show HEAD` 两份各测一次「符号前空行数」）：
    butler/store/repo.py     :253 load_cooldowns 0（新符号，按本仓顶层 def 应为 2）
                             :264 save_cooldowns 0（同上）
                             :278 EVALUATION_CALIBER_NOTE 0（**HEAD 是 2＝我把它的空行吃了**）
    butler/store/db.py       :112 _ensure_trigger_cooldowns 0（新符号）
                             :128 _init 0（**HEAD 是 2＝同上**）
    butler/triggers/engine.py :74 _import_legacy_snapshot 0、:117 _save_cooldowns 0、
                             :127 set_runtime 0（**HEAD 三枚方法前各 1**）
  ⛔ 顺手动别人/别批：`db.py:81 _TRIGGER_EVALUATIONS_IDX`、`repo.py:453 _FAIL_STATUSES`、
    `db.py:540 _COMPAT_INITED`、`engine.py:16 _CST` 在 HEAD 就也是 0 行空行＝本仓既有形状，本器不碰。

判据（每条腿自己出声）：
  · 八处插入位置逐条 `startswith` 锚点，行号来自 `grep -n` 打印行号的尺；
  · 施加后 `ast.parse` 过、CR 0、末行换行不变；
  · **剔空行后的内容序列必须与施加前逐行相等**＝本器只加空行，动到任何一个字就 raise；
  · 每处的「前面空行数」按目标值复核（顶层 2／方法 1）。

基线现读于 2026-10-02 15:54Z 的权威树（全 LF／CR 0／末行有换行）：
  butler/store/db.py         1be4c5bcd343a8c9bd3ef8d9fe61c9ff  589 行
  butler/store/repo.py       de0a1a4fae70c27c79ed239050653e4a  667 行
  butler/triggers/engine.py  2d368c40cbda511e80e0dfc7e364c2a4  530 行

用法：
    python3 scripts/audit_1002/patch_b13d_blanklines_1002.py          # DRY
    python3 scripts/audit_1002/patch_b13d_blanklines_1002.py --apply  # 写盘（重跑必 raise）
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

BASE = {
    "butler/store/db.py": ("1be4c5bcd343a8c9bd3ef8d9fe61c9ff", 589),
    "butler/store/repo.py": ("de0a1a4fae70c27c79ed239050653e4a", 667),
    "butler/triggers/engine.py": ("2d368c40cbda511e80e0dfc7e364c2a4", 530),
}

# (在此行**之前**插入, 该行原文锚点, 补几行空行)
INSERTS = {
    "butler/store/db.py": [
        (128, "def _init(c: sqlite3.Connection) -> None:", 2),
        (112, "def _ensure_trigger_cooldowns(c: sqlite3.Connection) -> None:", 2),
    ],
    "butler/store/repo.py": [
        (278, "EVALUATION_CALIBER_NOTE = (", 2),
        (264, "def save_cooldowns(values: dict[str, float]", 2),
        (253, "def load_cooldowns(retention_s: float = COOLDOWN_RETENTION_S,", 2),
    ],
    "butler/triggers/engine.py": [
        (127, "    def set_runtime(self, rt) -> None:", 1),
        (117, "    def _save_cooldowns(self):", 1),
        (74, "    def _import_legacy_snapshot(self, path: str) -> None:", 1),
    ],
}

# 施加后这些符号前的空行数必须达到的目标（同 INSERTS，逐条复核）
TARGETS = {
    "butler/store/db.py": {"_init": 2, "_ensure_trigger_cooldowns": 2},
    "butler/store/repo.py": {"load_cooldowns": 2, "save_cooldowns": 2,
                             "EVALUATION_CALIBER_NOTE": 2},
    "butler/triggers/engine.py": {"set_runtime": 1, "_save_cooldowns": 1,
                                  "_import_legacy_snapshot": 1},
}


def stats(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    return dict(md5=hashlib.md5(raw).hexdigest(), lines=len(text.splitlines(True)),
                bytes=len(raw), crlf=raw.count(b"\r\n"), endnl=raw.endswith(b"\n"))


def blanks_before(lines, lineno) -> int:
    k, b = lineno - 2, 0
    while k >= 0 and lines[k].strip() == "":
        b += 1
        k -= 1
    return b


def sig(text: str) -> list[str]:
    """剔空行的内容序列：本器只准加空行，这条相等＝没动到任何一个字。"""
    return [ln for ln in text.splitlines() if ln.strip()]


def main() -> None:
    apply = "--apply" in sys.argv
    originals = {}
    for rel, (md5, nlines) in BASE.items():
        raw = (REPO / rel).read_bytes()
        s = stats(raw)
        if s["md5"] != md5:
            raise SystemExit(f"ABORT {rel} 基线 md5 不符：现={s['md5']} 期={md5}（重跑必红＝已施加过）")
        if s["lines"] != nlines or s["crlf"] != 0 or not s["endnl"]:
            raise SystemExit(f"ABORT {rel} 基线形状不符：{s}")
        originals[rel] = raw.decode("utf-8")

    results = {}
    for rel, text in originals.items():
        lines = text.splitlines(True)
        for lineno, anchor, count in sorted(INSERTS[rel], key=lambda o: -o[0]):
            if lineno > len(lines) or not lines[lineno - 1].startswith(anchor):
                raise SystemExit(f"ABORT {rel}:{lineno} 锚点不符：{lines[lineno - 1:lineno]!r} "
                                 f"期以 {anchor!r} 起头")
            if blanks_before(lines, lineno) != 0:
                raise SystemExit(f"ABORT {rel}:{lineno} 前已有空行（本器只补 0 空行的位置）")
            lines[lineno - 1:lineno - 1] = ["\n"] * count
        new = "".join(lines)
        try:
            tree = ast.parse(new)
        except SyntaxError as e:
            raise SystemExit(f"ABORT {rel} 施加后语法不过：{e}")
        if sig(new) != sig(text):
            raise SystemExit(f"ABORT {rel} 剔空行后的内容与施加前不等＝本器动到了字")
        if new.count("\r") != 0:
            raise SystemExit(f"ABORT {rel} 施加产物含 CR")
        if new.endswith("\n") != text.endswith("\n"):
            raise SystemExit(f"ABORT {rel} 末行换行被改")
        out_lines = new.splitlines(True)
        found = {}
        for node in ast.walk(tree):
            nm = getattr(node, "name", None)
            if nm is None and isinstance(node, ast.Assign) and node.targets:
                nm = getattr(node.targets[0], "id", None)
            if nm in TARGETS[rel]:
                found[nm] = blanks_before(out_lines, node.lineno)
        for nm, want in TARGETS[rel].items():
            if found.get(nm) != want:
                raise SystemExit(f"ABORT {rel} {nm} 前空行数={found.get(nm)} 期望 {want}")
        results[rel] = new
        print("DRY|%-28s 行 %4d->%4d 字节 %6d->%6d（+%d 空行）" % (
            rel, len(text.splitlines(True)), len(out_lines),
            len(text.encode("utf-8")), len(new.encode("utf-8")),
            sum(c for _, _, c in INSERTS[rel])))

    if not apply:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_b13d_blanklines_1002.py --apply）")
        return
    for rel, new in results.items():
        (REPO / rel).write_text(new, encoding="utf-8", newline="")
    for rel, new in results.items():
        back = (REPO / rel).read_bytes()
        if back != new.encode("utf-8"):
            raise SystemExit(f"ABORT {rel} 回读与内存不一致")
        if sig(back.decode("utf-8")) != sig(originals[rel]):
            raise SystemExit(f"ABORT {rel} 落盘后内容与原文件不等")
        s = stats(back)
        print("APPLIED|%s|md5=%s|行=%d|字节=%d|CR=%d|末行换行=%s" % (
            rel, s["md5"], s["lines"], s["bytes"], s["crlf"], s["endnl"]))


if __name__ == "__main__":
    main()
