r"""批16 收尾：把批6 锚点尺里 `core/aliases.py AliasStore.delete_alias` 的记录行号从 84 重刷到 88。

为什么这条腿归本批：批16 在 aliases.py 加了 4 行（`import os` 1 行 + `_save` 内 3 行），
把 delete_alias 从 :84 顶到 :88。冻结尺按设计咬住了这次漂移——
`FAIL: test_anchors_are_still_sync_at_recorded_lines ... MOVED core/aliases.py AliasStore.delete_alias 84->88`
（见 `workorders/readings/1003b16/discover_full.txt:241`）。

两把独立尺同值才算数（行号⛔ 手推）：
  1. 尺子本体 `tests.test_audit_1002_batch6_gate` 的 `mod.def_at()`（AST 取 lineno，并报 kind=sync）；
  2. `python3 -B scripts/audit_1002/print_lines_1002.py butler/core/aliases.py 86-94` ⇒ `88:    def delete_alias(self, alias: str):`

⛔ 本脚本改生产码：只动 `tests/` 里那张坐标表的一行；kind 仍是 sync，所以判定的语义⛔ 变，变的只是行号。
重跑必 raise（ALREADY_APPLIED）。
"""
import argparse
import hashlib
import sys

REL = "tests/test_audit_1002_batch6_gate.py"
# 现读（ssh 上 `md5sum tests/test_audit_1002_batch6_gate.py` ⇒ 3f2278b7d6c3b33c3775c54925eea4b8，
# 同批 `CR 0 / BYTES 9152`）。⚠ 我第一版在这儿手填了一枚**没有来路**的 md5＝无中生有，
# 落盘前被自己这道基线门拦下的运气不算数，改成现读值。
BASE_MD5 = "3f2278b7d6c3b33c3775c54925eea4b8"
OLD = '    ("core/aliases.py", "AliasStore", "delete_alias", 84),'
NEW = '    ("core/aliases.py", "AliasStore", "delete_alias", 88),   # 批16 aliases.py +4 行（84->88，两把尺同值）'

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()

raw = open(REL, "rb").read()
text = raw.decode("utf-8")
if NEW in text:
    raise SystemExit("ALREADY_APPLIED|%s" % REL)
got = hashlib.md5(raw).hexdigest()
if got != BASE_MD5:
    raise SystemExit("BASELINE_DRIFT|%s|got=%s|want=%s" % (REL, got, BASE_MD5))
head = text.split("\n", 1)[0]
eol = "\r\n" if head.endswith("\r") else "\n"
if text.count(OLD + eol) != 1:
    raise SystemExit("ANCHOR|hitting=%d" % text.count(OLD + eol))
out_text = text.replace(OLD + eol, NEW + eol, 1)
# OLD 与 NEW 里 "delete_alias" 各出现一次 ⇒ 计数必须**相等**（⛔ 写成 +1：那是我把"新增一行"
# 错当成"新增一次命中"，会把这条 COLLATERAL 门变成恒不成立的死门）。
if out_text.count("delete_alias") != text.count("delete_alias"):
    raise SystemExit("COLLATERAL|delete_alias|%d->%d"
                     % (text.count("delete_alias"), out_text.count("delete_alias")))
if out_text.count('"delete_alias", 88') != 1 or out_text.count('"delete_alias", 84') != 0:
    raise SystemExit("MUST_PRESENT|88=%d 84=%d"
                     % (out_text.count('"delete_alias", 88'), out_text.count('"delete_alias", 84')))
lines_before = len(text.splitlines())
lines_after = len(out_text.splitlines())
if lines_before != lines_after:
    raise SystemExit("LINE_COUNT|%d->%d" % (lines_before, lines_after))
print("PLAN|%s|%d lines|md5=%s -> 待算" % (REL, lines_before, got))
if not args.apply:
    sys.exit(0)
out_bytes = out_text.encode("utf-8")
open(REL, "wb").write(out_bytes)
back = open(REL, "rb").read()
if back != out_bytes:
    raise SystemExit("WRITE_VERIFY|%s" % REL)
print("APPLIED|%s|md5=%s|lines=%d|bytes=%d|cr=%d"
      % (REL, hashlib.md5(back).hexdigest(), lines_after, len(back), back.count(b"\r")))
