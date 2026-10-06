"""批11 收册：把 §12 追加进 `doc/审计报告分诊台账-20261002.md`（append-only，⛔ 改已定稿的字）。

正文本体＝同目录 `ledger_1002_batch11_section.md`（⛔ 把文书塞进 python 字面量：字符串里的
`\\r`/`\\f` 会被静默吃掉，而 shell 双引号又把反引号当命令替换——§12 正文里满是反引号与 Windows 路径）。

护栏（每一条都在 main 里真跑，任何一格不符 ⇒ raise，⛔ 落盘）：
 1. 施加前 md5／行数／字节／CR／末行换行／`^## `／`^### ` 全等 §12-8 文末登记的那组「追加前」定值；
 2. 正文首行必须是 `## §12 ...`（防我贴错段）；正文内 CR＝0（这台机器的老坑：跨面换行）；
 3. **旧文本是新文本的前缀**＝append-only 的最强形式（新文本＝旧文本 + 分隔符 + 正文）；
 4. 写盘后回读 md5 与内存一致，并打印最终字节（最终那组数只出现在打印里，
    ⛔ 回填进 §12 正文当 claim——见 §12-5-2 那 7 字节的教训）。

用法：
    python3 scripts/audit_1002/patch_ledger_batch11_1002.py          # DRY
    python3 scripts/audit_1002/patch_ledger_batch11_1002.py --apply  # 写盘
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
LED = REPO / "doc" / "审计报告分诊台账-20261002.md"
BODY = REPO / "scripts" / "audit_1002" / "ledger_1002_batch11_section.md"

BASE_MD5 = "bd8553169dc88dea31651ae8d979d7aa"
BASE = dict(lines=663, bytes=80035, crlf=0, endnl=True, h2=11, h3=19)
SEP = b"\n"


def stats(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    lines = text.splitlines(True)
    return dict(md5=hashlib.md5(raw).hexdigest(), lines=len(lines), bytes=len(raw),
                crlf=raw.count(b"\r\n"), endnl=raw.endswith(b"\n"),
                h2=sum(1 for l in lines if l.startswith("## ")),
                h3=sum(1 for l in lines if l.startswith("### ")))


def fmt(s: dict) -> str:
    return ("md5=%s 行=%d 字节=%d CR=%d 末行换行=%s ^##=%d ^###=%d" % (
        s["md5"], s["lines"], s["bytes"], s["crlf"], s["endnl"], s["h2"], s["h3"]))


def main() -> None:
    apply = "--apply" in sys.argv
    if not LED.exists():
        raise SystemExit(f"ABORT 台账不存在：{LED}")
    old = LED.read_bytes()
    s_old = stats(old)
    if s_old["md5"] != BASE_MD5:
        raise SystemExit(f"ABORT 基线 md5 不符：现={s_old['md5']} 期={BASE_MD5}")
    for k, v in BASE.items():
        if s_old[k] != v:
            raise SystemExit(f"ABORT 基线 {k} 不符：现={s_old[k]} 期={v}（{fmt(s_old)}）")

    body = BODY.read_bytes()
    if body.count(b"\r\n") != 0:
        raise SystemExit("ABORT 正文含 CRLF（这台机器的老坑：跨面换行）")
    if not body.startswith(b"## \xc2\xa712 "):
        raise SystemExit(f"ABORT 正文首行不是 `## \u00a712 ...`：{body[:40]!r}")
    if not body.endswith(b"\n"):
        raise SystemExit("ABORT 正文末行无换行（追加会啃掉下一节）")

    new = old + SEP + body
    if not new.startswith(old):
        raise SystemExit("ABORT append-only 破了：旧文本不是新文本的前缀")
    text = new.decode("utf-8")
    heads = [l for l in text.splitlines() if l.startswith("## ")]
    if len(heads) != BASE["h2"] + 1:
        raise SystemExit(f"ABORT `^## ` 段数不是 {BASE['h2']}+1：{len(heads)}")
    print("追加前|", fmt(s_old))
    print("正文 |", f"行={len(body.decode('utf-8').splitlines())} 字节={len(body)}")
    print("DRY  追加后|", fmt(stats(new)))
    if not apply:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_ledger_batch11_1002.py --apply）")
        return
    LED.write_bytes(new)
    back = LED.read_bytes()
    if hashlib.md5(back).hexdigest() != hashlib.md5(new).hexdigest():
        raise SystemExit("ABORT 回读 md5 不符")
    print("APPLIED|", fmt(stats(back)))


if __name__ == "__main__":
    main()
