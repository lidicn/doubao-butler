#!/usr/bin/env python3
"""批18 连带维护（第二枚）：摘掉 pytest 替身档 ENV_ONLY 里那枚已过期的环境豁免。

现象：全量 discover 后 `test_trace_chain.py 环境名单不咬合：blocked=[] 在册=[...]`。
差分两腿取证（workorders/readings/1003b18/envonly_differential/）：
  只跑 tests.test_v25_pytest_shim            → Ran 20 / OK（blocked 与在册都是那一枚）
  批18 档 + shim 同进程                       → Ran 29 / FAILED(failures=1)，红因＝名单不咬合
⇒ 红因不是我改的判据，而是批18 验收件在场：它 import butler.core.dialog 前把
  vendor/homesdk/src 挂上 sys.path，`butler.core.dialog` 与 `homesdk` 就此留在 sys.modules。
  discover 是单进程，先导入全部测试模块 ⇒ 那枚腿在 canonical 跑法里**真的会跑**了
  （failed 为空＝它跑过且通过）。名单里留着一枚「它跑不到」的豁免＝过期豁免，正是闸要咬的东西。

⛔ 把闸改宽（例如允许 blocked ⊆ 在册）：那是拿假绿换安静。
反咬也留着的：哪天批18 那档删了／改名／不再 import dialog，blocked 会变回非空，
同一道闸当场判红，届时按现读把名单放回。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

REPO = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else pathlib.Path.cwd()
TARGET = REPO / "tests" / "test_v25_pytest_shim.py"
BASE_MD5 = "a4d21da0581879f64fdbf1b954063508"
BASE_LINES = 307
BASE_BYTES = 14660

START = 62           # 「# 环境缺包点名名单：…」
END = 70             # 「}」
NEW_BLOCK = [
    "# 环境缺包点名名单：只有这里点到的 (档, 例) 允许因 ModuleNotFoundError 不计入 failed，",
    "# 且它必须真的咬合（见 _run_file 末尾那条不咬合就判红的闸），⛔ 当跳过通道用。",
    "# 现读：宿主 python3 无 starlette、无 homesdk；容器内 starlette=True、fastapi 缺。",
    "# 哪天包装上了／用例改名了，名单不咬合＝本档判红，逼当场摘名单（⛔ 留一份过期的豁免）。",
    "# 2026-10-03 批18：摘掉 test_trace_chain.py 那枚豁免——全量 discover 是单进程，",
    "# tests/test_audit_1002_batch18_silent_raise_paths.py 在场即把 vendor/homesdk/src 挂上",
    "# sys.path 并把 butler.core.dialog 留在 sys.modules ⇒ 那枚腿在 canonical 跑法里真跑且通过",
    "# （差分取证：workorders/readings/1003b18/envonly_differential/）。",
    "# 反咬保留：若它重新变成 blocked，同一道闸照样判红，届时按现读把名单放回。",
    "ENV_ONLY = {",
    '    "test_proactive_budget.py": ("test_budget_routes_registered",),',
    '    "test_trigger_trace_log.py": ("test_trigger_logs_route_registered",),',
    "}",
]
OLD_HEAD = "# 环境缺包点名名单：只有这里点到的 (档, 例) 允许因 ModuleNotFoundError 不计入 failed，"
OLD_TAIL = "}"
OLD_ENTRY = '    "test_trace_chain.py": ("test_dialog_passes_trace_into_agent_at_both_entries",),'


def main() -> int:
    raw = TARGET.read_bytes()
    cr = raw.count(b"\r")
    md5 = hashlib.md5(raw).hexdigest()
    print(f"BASELINE|{TARGET.name}|md5={md5}|bytes={len(raw)}|CR={cr}")
    if cr:
        raise SystemExit(f"ABORT|CR={cr}（本脚本按 LF 逐行改）")
    text = raw.decode("utf-8")
    if OLD_ENTRY not in text:
        if "2026-10-03 批18：摘掉" in text:
            raise SystemExit("ALREADY_APPLIED|名单里已无 test_trace_chain 那枚，⛔ 二次打补丁")
        raise SystemExit("ABORT|既找不到在册条目也找不到已改标记")
    lines = text.split("\n")
    assert lines[-1] == "", "尾部缺换行"
    lines = lines[:-1]
    if len(lines) != BASE_LINES or md5 != BASE_MD5 or len(raw) != BASE_BYTES:
        raise SystemExit(f"ABORT|基底漂移 lines={len(lines)} 期望 {BASE_LINES} "
                         f"bytes={len(raw)} 期望 {BASE_BYTES} md5={md5} 期望 {BASE_MD5}")
    got_head = lines[START - 1]
    got_tail = lines[END - 1]
    if got_head != OLD_HEAD or got_tail != OLD_TAIL:
        raise SystemExit(f"ANCHOR|第 {START} 行={got_head!r} 第 {END} 行={got_tail!r}")
    if lines[68] != OLD_ENTRY:
        raise SystemExit(f"ANCHOR|第 69 行期望在册条目 实读 {lines[68]!r}")
    new = lines[:START - 1] + NEW_BLOCK + lines[END:]
    payload = ("\n".join(new) + "\n").encode("utf-8")
    try:
        compile(payload.decode("utf-8"), str(TARGET), "exec")
    except SyntaxError as exc:
        raise SystemExit(f"SYNTAX|{exc}")
    print(f"PLAN|{len(lines)}->{len(new)} lines|{len(raw)}->{len(payload)} bytes|"
          f"md5={md5[:12]}->{hashlib.md5(payload).hexdigest()[:12]}")
    if "--dry-run" in sys.argv:
        print("DRY|未写盘")
        return 0
    TARGET.write_bytes(payload)
    back = TARGET.read_text(encoding="utf-8").split("\n")
    assert OLD_ENTRY not in "\n".join(back), "WRITE_VERIFY|条目还在"
    assert len(back) == len(new) + 1, f"WRITE_VERIFY|行数 {len(back)}"
    assert TARGET.read_bytes().count(b"\r") == 0, "WRITE_VERIFY|引入 CR"
    assert back[START - 1] == NEW_BLOCK[0] and back[START - 2 + len(NEW_BLOCK)] == "}", \
        "WRITE_VERIFY|块边界不对"
    print(f"WRITTEN|lines={len(new)}|bytes={len(payload)}|"
          f"md5={hashlib.md5(payload).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
