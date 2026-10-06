"""批11 的第三笔（裁③ 收尾）：拆掉本批自己新造的一枚**空号**——`ttl_for_priority`。

来龙去脉（DCD 判例：空号要么接上，要么拆掉，⛔ 留着当样子货）：
  · 现读尺＝`grep -rn "ttl_for_priority" butler --include=*.py` → 只有 `butler/tts/queue.py:76` 定义行
    ＋ `:78` 那行 return 自身，**定义文件之外 0 引用**；生产真正走的取值路径是
    `TTSQueueConfig.ttl_for()`（`:192`，读 `ttl_by_priority` 那份可改副本），
    `enqueue_item`（`:329`）调的是它，⛔ 调 `ttl_for_priority`。
  · ⇒ 我写这条 helper 时犯了裁定③ 正在治的同一种病（「全树引用只有定义行本身」）。
    处置＝拆（⛔ 为了让它「有读者」而把 `config.ttl_for` 的回落语义改成档位表——那是产品语义变更，要裁）。

验收腿（先红后绿）＝`tests/test_audit_1002_batch11_dcd3.py::NoSecondDefinitionTest::
test_this_batch_ships_no_new_zero_caller_symbol`；红跑证据＝`workorders/readings/1002b11/red_zerocaller_leg.txt`
（`Ran 19 tests / FAILED (failures=1)`，红因点名 `ttl_for_priority`）。

用法：
    python3 scripts/audit_1002/patch_dcd3_del_zerocaller_1002.py          # DRY
    python3 scripts/audit_1002/patch_dcd3_del_zerocaller_1002.py --apply  # 写盘
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
TARGET = "butler/tts/queue.py"

BASE_MD5 = "37afd1302452390e47b23abc4eb11e9b"
BASE_LINES = 640
BASE_BYTES = 27115
BASE_ENDNL = False

OLD = (
    'def ttl_for_priority(priority: int) -> float:\n'
    '    """档位默认 TTL（秒）；0 或负＝不过期。"""\n'
    '    return PRIORITY_TTL_S[_band_key(priority)]\n'
    '\n'
    '\n'
)


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
        raise SystemExit(f"ABORT 锚点命中 {text.count(OLD)} 次（须恰 1）")
    if "ttl_for_priority" in (text.replace(OLD, "", 1)):
        raise SystemExit("ABORT 删完仍留 `ttl_for_priority` 字样——还有第二个落点没点开")
    new_text = text.replace(OLD, "", 1)
    ast.parse(new_text)
    new_raw = new_text.encode("utf-8")
    if new_raw.count(b"\r\n") != 0:
        raise SystemExit("ABORT 引入 CRLF")
    if new_raw.endswith(b"\n") != BASE_ENDNL:
        raise SystemExit("ABORT 末行换行变了")
    if len(new_raw.splitlines(True)) != BASE_LINES - 5:
        raise SystemExit(f"ABORT 行数不是 640-5：{len(new_raw.splitlines(True))}")
    print(f"DRY  改前| {stats(raw)}")
    print(f"DRY  改后| {stats(new_raw)}  (-{len(raw) - len(new_raw)} B)")
    if not apply:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_dcd3_del_zerocaller_1002.py --apply）")
        return
    p.write_bytes(new_raw)
    back = p.read_bytes()
    if hashlib.md5(back).hexdigest() != hashlib.md5(new_raw).hexdigest():
        raise SystemExit("ABORT 回读 md5 不符")
    print(f"APPLIED| {stats(back)}")


if __name__ == "__main__":
    main()
