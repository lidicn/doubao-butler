"""给台账 §9 追加「落盘戳」那一行（本仓无 `receipt_stamp_check.py` ⇒ 该格按 §8 同口径判「人工-无尺」）。

规矩：正文先全写完，戳在**这次写入的时刻**现取（不在脚本里写死时间字符串）；
本行不声明「文件当前 md5」——本行本身就在文件里，写完即变（加行后的 md5 交 commit 侧记）。
守卫＝改前指纹必须等于 §9 正文落盘时的现读值（md5／行数／字节／CR／标题数），不符则不落盘。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys
from datetime import datetime, timezone

LED = pathlib.Path("doc/审计报告分诊台账-20261002.md")
BODY_MD5 = "fe293d6abc00bf1561c993c2698c1c14"
BODY_LINES = 415
BODY_BYTES = 57714
BODY_MTIME_EPOCH = 1790938270
H2 = 9
H3 = 6
CRB = b"\r"


def main() -> int:
    data = LED.read_bytes()
    got_md5 = hashlib.md5(data).hexdigest()
    lines = data.decode("utf-8").splitlines(True)
    h2 = sum(1 for ln in lines if ln.startswith("## "))
    h3 = sum(1 for ln in lines if ln.startswith("### "))
    if got_md5 != BODY_MD5 or len(lines) != BODY_LINES or len(data) != BODY_BYTES or data.count(CRB):
        raise SystemExit(
            f"BODY-FINGERPRINT-MISMATCH md5={got_md5} lines={len(lines)} bytes={len(data)} cr={data.count(CRB)}"
        )
    if (h2, h3) != (H2, H3):
        raise SystemExit(f"HEADINGS h2={h2} h3={h3}，期望 {H2}/{H3}")
    mtime = int(LED.stat().st_mtime)
    if mtime != BODY_MTIME_EPOCH:
        raise SystemExit(f"正文 mtime epoch={mtime}，期望 {BODY_MTIME_EPOCH}（说明正文在本行之前又被写过，戳作废）")
    if lines[-1].strip():
        raise SystemExit(f"末行非空，插入点异常：{lines[-1][:40]!r}")

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    epoch = int(now.timestamp())
    if epoch < mtime:
        raise SystemExit(f"STAMP-BEFORE-BODY epoch={epoch} < 正文 mtime={mtime}")

    block = (
        "（§9 正文落盘指纹＝md5 `{md5}`／{lines} 行／{bytes:,} B／CR 0／`^## ` {h2} 段＋`^### ` {h3} 段；"
        "四把尺各自现读＝`md5sum`、`wc -lc`、二进制 CR 计数、AST 标题计数。\n"
        "⛔ 本行不写「文件当前 md5」：本行本身就在文件里，写完即变——加本行之后的 md5 由 commit 侧记录，"
        "⛔ 拿它当正文指纹用。\n"
        "落盘戳 `{stamp}`＝epoch {epoch}，取于本行写入的这一刻；§9 正文 mtime epoch {mtime}（`stat -c %Y` 现读，"
        "戳在其后 {delta} 秒）。\n"
        "⛔ 戳尺未跑：`receipt_stamp_check.py` 不在本仓（DB 树无 `workorders/tools/`）⇒ 本格判「人工-无尺」，与 §8 那格同口径。\n"
        "```text\n"
        "# 戳行复跑（现读，⛔ 抄本块里的数）\n"
        "md5sum doc/审计报告分诊台账-20261002.md; wc -lc doc/审计报告分诊台账-20261002.md\n"
        "python3 -c 'import pathlib;b=pathlib.Path(\"doc/审计报告分诊台账-20261002.md\").read_bytes();"
        "print(\"cr\",b.count(b\"\\r\"),\"lines\",len(b.decode(\"utf-8\").splitlines(True)))'\n"
        "```\n"
    ).format(md5=BODY_MD5, lines=BODY_LINES, bytes=BODY_BYTES, h2=H2, h3=H3,
             stamp=stamp, epoch=epoch, mtime=mtime, delta=epoch - mtime)
    if not data.endswith(b"\n"):
        block = "\n" + block
    out = data + block.encode("utf-8")
    out_lines = out.decode("utf-8").splitlines(True)
    if out.count(b"\r"):
        raise SystemExit("CR 血统被引入")
    n_h2 = sum(1 for ln in out_lines if ln.startswith("## "))
    n_h3 = sum(1 for ln in out_lines if ln.startswith("### "))
    if (n_h2, n_h3) != (H2, H3):
        raise SystemExit(f"标题数被改：h2={n_h2} h3={n_h3}")
    fences = sum(1 for ln in out_lines if ln.startswith("```"))
    if fences % 2:
        raise SystemExit(f"围栏不成对：{fences}")

    LED.write_bytes(out)
    print(f"STAMP|{stamp} epoch={epoch} body_mtime={mtime} delta={epoch - mtime}")
    print(f"POST|lines {BODY_LINES}->{len(out_lines)} bytes {BODY_BYTES}->{len(out)} "
          f"md5={hashlib.md5(out).hexdigest()} cr=0 h2={n_h2} h3={n_h3} fences={fences}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
