#!/usr/bin/env python3
"""批18 连带维护：把未-await 花名册里被本批挪了行的那一枚锚点重新钉死。

为什么必须动它：批18 把 dialog.py 的 8 处 SPEAKING 收敛进 `_speak_transition`，
文件从 772 行变成 777 行、`self.tv.notify({...})` 从 :518 移到 :500。
`classify_unawaited_1002.py` 的 SITES 按 (文件, 行号, 名字) 定位，行号一动就报
`DRIFT：这一行 AST 里没有该名字的 Call` ⇒ 批6 门当场两枚红（这正是它该有的样子）。

⛔ 改判据去迁就代码：这里只改**锚点行号**（数据），20 SYNC_OK / 0 UNKNOWN 那道分布
断言一个字不动。改前先现读 :500 是不是那句调用，改后再跑一次门让它自己判。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

# argv 里只把「不以 - 开头」的第一个当仓库根（与同批另两枚对齐）：上一版拿 sys.argv[1]
# 直接当根，`--dry-run` 会被当成路径 → FileNotFoundError，判据没跑却像"跑过了"。
_args = [a for a in sys.argv[1:] if not a.startswith("-")]
REPO = pathlib.Path(_args[0]).resolve() if _args else pathlib.Path.cwd()
TARGET = REPO / "scripts" / "audit_1002" / "classify_unawaited_1002.py"
OLD = '    ("core/dialog.py", 518, "notify"),\n'
NEW = '    ("core/dialog.py", 500, "notify"),   # 批18 收敛 SPEAKING 后从 :518 移到 :500\n'
DIALOG = REPO / "butler" / "core" / "dialog.py"

raw = TARGET.read_bytes()
cr = raw.count(b"\r")
print(f"BASELINE|{TARGET.name}|bytes={len(raw)}|CR={cr}|md5={hashlib.md5(raw).hexdigest()}")
if cr:
    raise SystemExit(f"ABORT|CR={cr} 本脚本按 LF 逐行替换，⛔ 混血文件")
lines = raw.decode("utf-8").split("\n")
assert lines[-1] == "", "尾部缺换行"
lines = lines[:-1]
if OLD.rstrip("\n") not in lines:
    hits = [i + 1 for i, l in enumerate(lines) if "core/dialog.py" in l]
    if any(l == NEW.rstrip("\n") or l.startswith(NEW.split("   #")[0]) for l in lines):
        print("ALREADY_APPLIED|锚点已是 :500")
        raise SystemExit(1)
    raise SystemExit(f"ABORT|找不到锚点行，core/dialog.py 出现在行号 {hits}")
idx = lines.index(OLD.rstrip("\n"))
print(f"ANCHOR|第 {idx + 1} 行原样={OLD.rstrip(chr(10))!r}")

# 改前现读：新锚点那一行必须真是那句调用（⛔ 只信批18 的 PLAN）
dlines = DIALOG.read_bytes().decode("utf-8").split("\n")
got = dlines[499].strip()
if not got.startswith("self.tv.notify("):
    raise SystemExit(f"ABORT|dialog.py:500 实读 {got!r}，⛔ self.tv.notify(")
old518 = dlines[517].strip()
print(f"CONFIRM|dialog.py:500={got!r}")
print(f"CONFIRM|dialog.py:518(旧锚点现读)={old518!r}")

lines[idx] = NEW.rstrip("\n")
payload = ("\n".join(lines) + "\n").encode("utf-8")
try:
    compile(payload.decode("utf-8"), str(TARGET), "exec")
except SyntaxError as exc:
    raise SystemExit(f"SYNTAX|{exc}")
print(f"PLAN|bytes={len(raw)}->{len(payload)}|"
      f"md5={hashlib.md5(raw).hexdigest()[:12]}->{hashlib.md5(payload).hexdigest()[:12]}")
if "--dry-run" in sys.argv:
    print("DRY|未写盘")
    raise SystemExit(0)
TARGET.write_bytes(payload)
back = TARGET.read_text(encoding="utf-8").split("\n")
assert back[idx] == NEW.rstrip("\n"), "WRITE_VERIFY|回读不一致"
assert len(back) == len(lines) + 1, f"WRITE_VERIFY|行数变了 {len(back)}"
assert TARGET.read_bytes().count(b"\r") == 0, "WRITE_VERIFY|引入了 CR"
print(f"WRITTEN|lines={len(lines)}|bytes={len(payload)}|md5={hashlib.md5(payload).hexdigest()}")
