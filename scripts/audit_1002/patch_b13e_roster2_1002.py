"""批13e 一次性落件器（**只动在册坐标**）：批13d 的空行回收又推了 3 行，批6 的在册 30 枚按 AST 现读第二次刷新。

同一批里刷第二次的账要写明白（⛔ 让读者以为册子一直是 212/471）：
  · 旧（HEAD，`git show` → `/tmp/head_engine.py`，493 行）`:175 add_trigger_evaluation`、`:434 add_trigger_run`
  · 批13 落码后（530 行）`:212`、`:471`   —— 整段 +37，`patch_b13c_roster_1002.py` 刷的第一次
  · 批13d 补空行后（533 行）`:215`、`:474` —— 本器刷的第二次，坐标同样来自 AST 现读（`python3` 打印 `n.lineno`）
`classify_unawaited_1002.py` 自己的规矩是「行号漂了尺子报 DRIFT，⛔ 手改名单」⇒ 这里改的每一格都配一条读数：
活树 5 枚 `write_failures.record(` 的 `lineno + 首参常量` 全清单在
`workorders/readings/1002b13/record_sites_ast.txt`，其中 69/103/125 三枚是批13 新增的接入点（已进
`tests/test_write_failures.py::SITES`），215/474 两枚才是本器要刷的在册坐标。

基线现读于 2026-10-02 15:57Z 的权威树（LF／CR 0／末行有换行）：
  scripts/audit_1002/classify_unawaited_1002.py ff7123134f8c455aad65cdf82e330d4c  672 行

用法：
    python3 scripts/audit_1002/patch_b13e_roster2_1002.py          # DRY
    python3 scripts/audit_1002/patch_b13e_roster2_1002.py --apply  # 写盘（重跑必 raise）
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
REL = "scripts/audit_1002/classify_unawaited_1002.py"
BASE_MD5 = "ff7123134f8c455aad65cdf82e330d4c"
BASE_LINES = 672

OPS = [
    (84, '    ("triggers/engine.py", 471, "record"),',
     '    ("triggers/engine.py", 474, "record"),   # add_trigger_run：HEAD 434→批13 落码 471→补空行 474（AST 现读，非推算）'),
    (83, '    ("triggers/engine.py", 212, "record"),',
     '    ("triggers/engine.py", 215, "record"),   # add_trigger_evaluation：HEAD 175→批13 落码 212→补空行 215（同上）'),
]


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
    for lineno, anchor, new in sorted(OPS, key=lambda o: -o[0]):
        if not lines[lineno - 1].startswith(anchor):
            raise SystemExit(f"ABORT {REL}:{lineno} 锚点不符：{lines[lineno - 1]!r} 期以 {anchor!r} 起头")
        lines[lineno - 1] = new + "\n"
    out = "".join(lines)
    try:
        ast.parse(out)
    except SyntaxError as e:
        raise SystemExit(f"ABORT {REL} 施加后语法不过：{e}")
    if out.count("\r") != 0:
        raise SystemExit(f"ABORT {REL} 施加产物含 CR")
    if out.endswith("\n") != text.endswith("\n"):
        raise SystemExit(f"ABORT {REL} 末行换行被改")
    for want in ('("triggers/engine.py", 215, "record")', '("triggers/engine.py", 474, "record")'):
        if out.count(want) != 1:
            raise SystemExit(f"ABORT {REL} 新坐标没落成 1 处：{want}")
    for gone in ('("triggers/engine.py", 212', '("triggers/engine.py", 471',
                 '("triggers/engine.py", 175', '("triggers/engine.py", 434'):
        if gone in out:
            raise SystemExit(f"ABORT {REL} 旧坐标还留着：{gone}")
    print("DRY |%-42s 行 %4d->%4d 字节 %6d->%6d" % (
        REL, len(lines), len(out.splitlines(True)), len(text.encode("utf-8")), len(out.encode("utf-8"))))
    if not apply:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_b13e_roster2_1002.py --apply）")
        return
    p.write_text(out, encoding="utf-8", newline="")
    back = p.read_bytes()
    if back != out.encode("utf-8"):
        raise SystemExit(f"ABORT {REL} 回读与内存不一致")
    s2 = stats(back)
    print("APPLIED|%s|md5=%s|行=%d|字节=%d|CR=%d|末行换行=%s" % (
        REL, s2["md5"], s2["lines"], s2["bytes"], s2["crlf"], s2["endnl"]))


if __name__ == "__main__":
    main()
