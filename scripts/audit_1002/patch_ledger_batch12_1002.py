"""批12 收册：把 §13 追加进 `doc/审计报告分诊台账-20261002.md`（append-only，⛔ 改已定稿的字）。

正文本体＝同目录 `ledger_1002_batch12_section.md`（⛔ 把文书塞进 python 字面量：字符串里的
`\\r`/`\\f` 会被静默吃掉，而 shell 双引号又把反引号当命令替换——§13 正文里满是反引号与 `E:\\NAS` 路径）。

护栏（每一条都在 main 里真跑，任何一格不符 ⇒ raise，⛔ 落盘）：
 1. 施加前 md5／行数／字节／CR／末行换行／`^## `／`^### ` 全等 §13 文末登记的那组「追加前」定值
    （现读于 2026-10-02 14:53:48Z 的权威树，⛔ 拿 E 盘镜像的数当基线）；
 2. 正文首行必须是 `## §13 ...`（防我贴错段）；正文内 CR＝0；正文末行必须有换行（⛔ 啃掉下一节）；
 3. **旧文本是新文本的前缀**＝append-only 的最强形式（新文本＝旧文本 + 分隔符 + 正文）；
 4. `^## ` 只许 +1（本节只开一节），`^### ` 只许 +正文里那几枚（按正文现数，⛔ 手数）；
 5. 写盘后回读 md5 与内存一致，并打印最终字节（最终那组数⛔ 回填进 §13 正文当 claim——
    §12-5-2 那 7 字节的教训；只指本笔 commit 的 `APPLIED|` 行与 `workorders/readings/1002b12/ledger_after.txt`）。

用法：
    python3 scripts/audit_1002/patch_ledger_batch12_1002.py          # DRY
    python3 scripts/audit_1002/patch_ledger_batch12_1002.py --apply  # 写盘
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
LED = REPO / "doc" / "审计报告分诊台账-20261002.md"
BODY = REPO / "scripts" / "audit_1002" / "ledger_1002_batch12_section.md"

BASE_MD5 = "0c6f6cc5c6cb9436afabd1125ec4aa0a"
BASE = dict(lines=813, bytes=97371, crlf=0, endnl=True, h2=12, h3=27)
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
        raise SystemExit(f"ABORT 基线 md5 不符：现={s_old['md5']} 期={BASE_MD5}"
                         f"（已施加过 ⇒ 重跑必红，这是闸不是故障；{fmt(s_old)}）")
    for k, v in BASE.items():
        if s_old[k] != v:
            raise SystemExit(f"ABORT 基线 {k} 不符：现={s_old[k]} 期={v}（{fmt(s_old)}）")

    body = BODY.read_bytes()
    if body.count(b"\r\n") != 0:
        raise SystemExit("ABORT 正文含 CRLF（这台机器的老坑：跨面换行）")
    if not body.startswith(b"## \xc2\xa713 "):
        raise SystemExit(f"ABORT 正文首行不是 `## \u00a713 ...`：{body[:40]!r}")
    if not body.endswith(b"\n"):
        raise SystemExit("ABORT 正文末行无换行（追加会啃掉下一节）")

    new = old + SEP + body
    if not new.startswith(old):
        raise SystemExit("ABORT append-only 破了：旧文本不是新文本的前缀")
    s_new = stats(new)
    body_h3 = sum(1 for l in body.decode("utf-8").splitlines() if l.startswith("### "))
    if s_new["h2"] != BASE["h2"] + 1:
        raise SystemExit(f"ABORT `^## ` 不是 {BASE['h2']}+1：{s_new['h2']}")
    if s_new["h3"] != BASE["h3"] + body_h3:
        raise SystemExit(f"ABORT `^### ` 不是 {BASE['h3']}+正文{body_h3}：{s_new['h3']}")
    print("追加前|", fmt(s_old))
    print("正文 |", f"行={len(body.decode('utf-8').splitlines())} 字节={len(body)} ^###={body_h3}")
    print("DRY  追加后|", fmt(s_new))
    if not apply:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_ledger_batch12_1002.py --apply）")
        return
    LED.write_bytes(new)
    back = LED.read_bytes()
    if hashlib.md5(back).hexdigest() != hashlib.md5(new).hexdigest():
        raise SystemExit("ABORT 回读 md5 不符")
    print("APPLIED|", fmt(stats(back)))


if __name__ == "__main__":
    main()
