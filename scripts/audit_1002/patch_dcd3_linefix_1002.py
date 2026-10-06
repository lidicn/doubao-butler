"""批11 的二次修正腿：把 `butler/tts/queue.py` 头注里一枚**错的行号**改口。

为什么要单独一笔（⛔ 塞进 `patch_dcd3_1002.py` 重跑）：那把一次性落码器的锚点已经施加过，
重跑必 raise（raise＝它对，不是坏）；本笔只动一行注释，走同一套护栏＝基线 md5＋锚点唯一＋
`ast.parse`＋CR 0＋末行换行不变。

现读（尺＝`grep -n "self.tts_queue.enqueue" butler/notify/router.py`）：
    281:            res = self.tts_queue.enqueue(
⇒ 注释里写的 `notify/router.py:283` 是**那条语句的第二行**（`room=note.room, ...`），⛔ 语句本体。
这条指针是我本批写进去的，写的时候凭手感没挂尺——判别人的交付物时我守「行号只能来自打印行号的尺」，
自己写的注释同样适用（DCD 判例：文件头那类文字在同一条承诺上盖章＝空号，注释指错路＝半空号）。

用法：
    python3 scripts/audit_1002/patch_dcd3_linefix_1002.py          # DRY（默认，不写盘）
    python3 scripts/audit_1002/patch_dcd3_linefix_1002.py --apply  # 写盘
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
TARGET = "butler/tts/queue.py"

BASE_MD5 = "33ec90dd140d5674f2d6f507ea6b71a8"
BASE_LINES = 640
BASE_BYTES = 27115
BASE_ENDNL = False          # 该文件末行无换行＝本批前后都保持原样

OLD = "notify/router.py:283"
NEW = "notify/router.py:281"


def stats(raw: bytes) -> str:
    return ("md5=%s lines=%d bytes=%d crlf=%d endnl=%s" % (
        hashlib.md5(raw).hexdigest(), len(raw.splitlines(True)), len(raw),
        raw.count(b"\r\n"), raw.endswith(b"\n")))


def main() -> None:
    apply = "--apply" in sys.argv
    p = REPO / TARGET
    raw = p.read_bytes()
    got = hashlib.md5(raw).hexdigest()
    if got != BASE_MD5:
        raise SystemExit(f"ABORT 基线不符 {TARGET}: 现={got} 期={BASE_MD5}")
    text = raw.decode("utf-8")
    if text.count(OLD) != 1:
        raise SystemExit(f"ABORT 锚点命中 {text.count(OLD)} 次（须恰 1）: {OLD}")
    if text.count(NEW) != 0:
        raise SystemExit(f"ABORT 改口目标已存在（本笔已施加过？）: {NEW}")
    new_text = text.replace(OLD, NEW)
    new_raw = new_text.encode("utf-8")
    ast.parse(new_text)                      # 语法仍成立
    if new_raw.count(b"\r\n") != 0:
        raise SystemExit("ABORT 引入 CRLF")
    if len(new_raw) != len(raw):
        raise SystemExit(f"ABORT 字节数变了（注释等长替换）：{len(raw)} -> {len(new_raw)}")
    if new_raw.endswith(b"\n") != BASE_ENDNL:
        raise SystemExit("ABORT 末行换行变了")
    if len(new_raw.splitlines(True)) != BASE_LINES:
        raise SystemExit("ABORT 行数变了")
    print(f"DRY  {TARGET} 改前| {stats(raw)}")
    print(f"DRY  {TARGET} 改后| {stats(new_raw)}")
    if not apply:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_dcd3_linefix_1002.py --apply）")
        return
    p.write_bytes(new_raw)
    back = p.read_bytes()
    if hashlib.md5(back).hexdigest() != hashlib.md5(new_raw).hexdigest():
        raise SystemExit("ABORT 回读 md5 不符")
    print(f"APPLIED| {stats(back)}")


if __name__ == "__main__":
    main()
