"""批13b-2 一次性落码器（**只动生产码**）：迁移腿的快照句柄改成 `with`。

红在哪：`tests/test_audit_1002_batch13_cooldown.py::SnapshotImportTest::
test_snapshot_import_does_not_leak_the_snapshot_handle` 在 15:38Z 的权威树上跑红
（读数件 `workorders/readings/1002b13/leak_leg_red.txt`），报的是
`butler/triggers/engine.py:83  ResourceWarning: unclosed file …/trigger_cooldowns.json`。
我自己写的迁移腿用了 `json.load(open(path))`：测试里无声，容器里是一枚不关的 fd，
而这条路径每次重启只走一次＝泄漏不会自己暴露。

⛔ 顺手把 `except Exception` 收窄、⛔ 改语义：本器只换取文件的方式，读到的 dict 一模一样。

基线现读于 2026-10-02 15:38Z 的权威树（LF／CR 0／末行有换行）：
  butler/triggers/engine.py c0876d191902300dc68f858634ec073b  529 行／26,926 B

用法：
    python3 scripts/audit_1002/patch_b13b_engine_1002.py          # DRY
    python3 scripts/audit_1002/patch_b13b_engine_1002.py --apply  # 写盘（重跑必 raise）
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

REL = "butler/triggers/engine.py"
BASE_MD5 = "c0876d191902300dc68f858634ec073b"
BASE_LINES = 529

# 行号来自 `awk NR` 打印行号的尺（15:29Z 现读）：
#   82         try:
#   83             data = json.load(open(path, encoding='utf-8'))
#   84         except Exception as e:
ANCHOR_FIRST = "            data = json.load(open(path, encoding='utf-8'))"
ANCHOR_LAST = "        except Exception as e:"
# 缩进层级现读：`def _import_legacy_snapshot` 在 4、函数体在 8、`try:` 在 8 ⇒ try 支在 12。
PAYLOAD = '''            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)'''


def stats(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    return dict(md5=hashlib.md5(raw).hexdigest(), lines=len(text.splitlines(True)),
                bytes=len(raw), crlf=raw.count(b"\r\n"), endnl=raw.endswith(b"\n"))


def main() -> None:
    apply = "--apply" in sys.argv
    p = REPO / REL
    raw = p.read_bytes()
    s = stats(raw)
    if s["md5"] != BASE_MD5:
        raise SystemExit(f"ABORT {REL} 基线 md5 不符：现={s['md5']} 期={BASE_MD5}（重跑必红＝已施加过）")
    if s["lines"] != BASE_LINES or s["crlf"] != 0 or not s["endnl"]:
        raise SystemExit(f"ABORT {REL} 基线形状不符：{s}")

    text = raw.decode("utf-8")
    lines = text.splitlines(True)
    if len(lines) != BASE_LINES:
        raise SystemExit(f"ABORT {REL} 行数与基线不符：{len(lines)}")
    prev, target, nxt = lines[81], lines[82], lines[83]      # 0-based：行 82／83／84
    if not prev.startswith("        try:"):
        raise SystemExit(f"ABORT {REL}:82 上锚点不符：{prev!r}")
    if not target.startswith(ANCHOR_FIRST):
        raise SystemExit(f"ABORT {REL}:83 目标行不符：{target!r} 期以 {ANCHOR_FIRST!r} 起头")
    if not nxt.startswith(ANCHOR_LAST):
        raise SystemExit(f"ABORT {REL}:84 下锚点不符：{nxt!r}")

    repl = [ln + "\n" for ln in PAYLOAD.split("\n")]
    new_lines = lines[:82] + repl + lines[83:]               # 只换 83 这一行，82／84 原样留着
    new = "".join(new_lines)

    if "json.load(open(" in new:
        raise SystemExit(f"ABORT {REL} 产物里仍有 json.load(open(：{new.count('json.load(open(')} 处")
    if new.count("data = json.load(fh)") != 1:
        raise SystemExit(f"ABORT {REL} 新读法落点不是 1 处")
    if 'except Exception as e:\n            logger.warning("cooldown snapshot unreadable, kept' not in new:
        raise SystemExit(f"ABORT {REL} 接住异常那一支被移动过＝本器只该换取文件的方式")
    try:
        tree = ast.parse(new)
    except SyntaxError as e:
        raise SystemExit(f"ABORT {REL} 施加后语法不过：{e}")
    if new.count("\r") != 0:
        raise SystemExit(f"ABORT {REL} 施加产物含 CR")
    if new.endswith("\n") != text.endswith("\n"):
        raise SystemExit(f"ABORT {REL} 末行换行被改")
    # 只有 `_import_legacy_snapshot` 的 `with` 计数 +1，其余函数⛔ 跟着变
    def fn_src(name):
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                for m in n.body:
                    if isinstance(m, ast.FunctionDef) and m.name == name:
                        return ast.get_source_segment(new, m) or ""
        return ""
    seg = fn_src("_import_legacy_snapshot")
    if seg.count("with open(") != 1 or seg.count("json.load(") != 1:
        raise SystemExit(f"ABORT {REL} `_import_legacy_snapshot` 形状不符：with={seg.count('with open(')} load={seg.count('json.load(')}")

    print("DRY |%-28s 行 %4d->%4d 字节 %6d->%6d" % (
        REL, len(lines), len(new_lines), len(text.encode("utf-8")), len(new.encode("utf-8"))))
    if not apply:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_b13b_engine_1002.py --apply）")
        return
    p.write_text(new, encoding="utf-8", newline="")
    back = p.read_bytes()
    if back != new.encode("utf-8"):
        raise SystemExit(f"ABORT {REL} 回读与内存不一致")
    s2 = stats(back)
    print("APPLIED|%s|md5=%s|行=%d|字节=%d|CR=%d|末行换行=%s" % (
        REL, s2["md5"], s2["lines"], s2["bytes"], s2["crlf"], s2["endnl"]))


if __name__ == "__main__":
    main()
