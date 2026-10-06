#!/usr/bin/env python3
"""批18 连带维护（第三枚）：给 pytest 替身档自己挂 vendor/homesdk/src，把环境定死。

为什么还要动一次：上一枚把 ENV_ONLY 里 `test_trace_chain.py` 那枚豁免摘了，但「那枚腿能不能 import
homesdk」于是取决于同场还跑了哪些测试档——批18 档在场＝绿，单独跑 shim＝红（ModuleNotFoundError
未在册）。跑法决定环境＝假绿通道的一种，⛔ 靠"只按 canonical 跑法判绿"糊过去。

本文件自己挂上这条路径（仓内自带 vendor/homesdk/src，装的就是它 ⇒ 见 tests/test_homesdk_vendor_gate.py），
两种跑法下环境一致。用 `append` 不用 `insert(0)`：容器里镜像已装 homesdk，⛔ 让仓内副本盖住装好的那份。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

# argv 里只把「不以 - 开头」的第一个当仓库根：上一版拿 sys.argv[1] 直接当根，
# 于是 `--dry-run` 被当成路径，dry 腿 FileNotFoundError（判据没跑，⛔ 读成"没问题"）。
_args = [a for a in sys.argv[1:] if not a.startswith("-")]
REPO = pathlib.Path(_args[0]).resolve() if _args else pathlib.Path.cwd()
TARGET = REPO / "tests" / "test_v25_pytest_shim.py"
ANCHOR = 'TEST_DIR = Path(__file__).resolve().parent'
NEW = [
    ANCHOR,
    "",
    "# 宿主 python3 没装 homesdk（只在镜像里），而 dialog 那枚被转译的腿要 import 它。",
    "# 本档自己把仓内 vendor/homesdk/src 挂上：⛔ 让「那枚腿跑不跑」取决于同场还有哪些测试档",
    "# 在改全局 sys.path（跑法决定环境＝假绿通道）。用 append 不用 insert(0)——容器里镜像已装",
    "# homesdk，仓内副本只兜宿主，⛔ 盖住装好的那份。",
    '_VENDOR = TEST_DIR.parent / "vendor" / "homesdk" / "src"',
    'if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:',
    "    sys.path.append(str(_VENDOR))",
]


def main() -> int:
    raw = TARGET.read_bytes()
    cr = raw.count(b"\r")
    md5 = hashlib.md5(raw).hexdigest()
    print(f"BASELINE|{TARGET.name}|md5={md5}|bytes={len(raw)}|CR={cr}")
    if cr:
        raise SystemExit(f"ABORT|CR={cr}")
    text = raw.decode("utf-8")
    if "_VENDOR = TEST_DIR.parent" in text:
        raise SystemExit("ALREADY_APPLIED|vendor 垫片已在，⛔ 二次打补丁")
    lines = text.split("\n")
    assert lines[-1] == "", "尾部缺换行"
    lines = lines[:-1]
    if lines.count(ANCHOR) != 1:
        raise SystemExit(f"ANCHOR|{ANCHOR!r} 出现 {lines.count(ANCHOR)} 次，期望 1")
    idx = lines.index(ANCHOR)
    print(f"ANCHOR|第 {idx + 1} 行")
    new = lines[:idx] + NEW + lines[idx + 1:]
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
    assert len(back) == len(new) + 1, f"WRITE_VERIFY|行数 {len(back)}"
    assert back[idx] == ANCHOR and back[idx + 6].startswith("_VENDOR =") \
        and back[idx + 8].startswith("    sys.path.append"), \
        "WRITE_VERIFY|插入位置不对"
    assert TARGET.read_bytes().count(b"\r") == 0, "WRITE_VERIFY|引入 CR"
    print(f"WRITTEN|lines={len(new)}|bytes={len(payload)}|md5={hashlib.md5(payload).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
