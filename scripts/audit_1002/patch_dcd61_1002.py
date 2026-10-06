"""一次性落码器：DCD 裁定①（`memory/extractor.py` 限额截断改取**最新**一截，返回仍升序）。

裁定文书＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md:22-28`。
验收＝`tests/test_audit_1002_batch8_dcd6.py::MemoryRecencyTest`（改前 `Ran 4 / failures=3`，
其中两条红就是这枚方向问题）。裁定⑥（删 `butler/engine.py`）走 `git rm`，⛔ 本脚本职责。

锚点＝整行 startswith ＋ 唯一命中（行号只作打印核对，⛔ 当写入依据）；
写回走 bytes＋splitlines(True)，CR／末行换行符／行数／定义数改前改后各验一次。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

TGT = pathlib.Path("butler/memory/extractor.py")
BASE_MD5 = "93353406af1459aa86e3993ca62dd39c"
BASE_BYTES = 13461
BASE_LINES = 328
BASE_DEFS = 7
CRB = b"\r"
LF = b"\n"

OLD_DOC = '        """从 chat_logs 拉最近 N 天对话（升序），过滤自动化推送噪音。"""\n'
NEW_DOC = ('        """从 chat_logs 拉最近 N 天对话，过滤自动化推送噪音。\n'
           '\n'
           '        限额截的是**最新**一截（DCD 裁定①：旧写法「ts 正序 ＋ LIMIT」切走的是最旧一截，\n'
           '        提取会永远在学陈年旧事），取满后翻回升序再喂模型。\n'
           '        """\n')
OLD_SQL = "               ORDER BY ts ASC\n"
NEW_SQL = "               ORDER BY ts DESC\n"
LOG_LINE = '        logger.info("extract: fetched %d raw rows, filtered to %d real dialogs",\n'
RETURN_LINE = "        return result\n"
REVERSE_BLOCK = (
    "        # DCD 裁定①：SQL 倒序拿到的最新一截在这里翻回升序，⛔ 把倒序直接送进 prompt\n"
    "        result.reverse()\n"
)


def _unique(exact: str, lines, where: str) -> int:
    hits = [i for i, ln in enumerate(lines) if ln == exact]
    if len(hits) != 1:
        raise SystemExit(f"{where}：整行等值命中 {len(hits)} 次，期望 1 → {exact[:60]!r}")
    return hits[0]


def main(argv: list[str]) -> int:
    do_apply = "--apply" in argv
    data = TGT.read_bytes()
    if hashlib.md5(data).hexdigest() != BASE_MD5 or len(data) != BASE_BYTES or data.count(CRB):
        raise SystemExit(f"BASE-MISMATCH md5={hashlib.md5(data).hexdigest()} bytes={len(data)} cr={data.count(CRB)}")
    lines = data.decode("utf-8").splitlines(True)
    if len(lines) != BASE_LINES:
        raise SystemExit(f"BASE-LINES {len(lines)} 期望 {BASE_LINES}")

    i_doc = _unique(OLD_DOC, lines, "docstring")
    i_sql = _unique(OLD_SQL, lines, "SQL 行")
    i_log = _unique(LOG_LINE, lines, "日志行")
    # return result 必须紧跟在日志行之后（日志调用两行：形参 + 实参）
    if lines[i_log + 2] != RETURN_LINE:
        raise SystemExit(f"return result 不在日志块之后：{lines[i_log + 2]!r}")
    print(f"ANCHOR|doc={i_doc + 1} sql={i_sql + 1} log={i_log + 1} return={i_log + 3}")

    new = list(lines)
    new[i_log + 2:i_log + 2] = [REVERSE_BLOCK]          # 先改后面的位置，再改前面的（行号不影响前段）
    new[i_sql] = NEW_SQL
    new[i_doc:i_doc + 1] = [NEW_DOC]
    out = "".join(new).encode("utf-8")
    out_lines = out.decode("utf-8").splitlines(True)

    text = out.decode("utf-8")
    if out.count(CRB) or out.endswith(LF) != data.endswith(LF):
        raise SystemExit("血统被改（CR 或末行换行符）")
    n_defs = sum(1 for ln in out_lines if ln.startswith(("def ", "async def ", "    def ", "    async def ")))
    if n_defs != BASE_DEFS:
        raise SystemExit(f"定义数变了：{n_defs} 期望 {BASE_DEFS}")
    # 期望行数＝现读基数 ＋ 两块插入物各自的真实行数（⛔ 手算「+5」——那是我下笔时的口算，会静默错）
    delta = (len(NEW_DOC.splitlines(True)) - 1) + len(REVERSE_BLOCK.splitlines(True))
    if len(out_lines) != BASE_LINES + delta:
        raise SystemExit(f"行数期望 {BASE_LINES + delta}（doc+{len(NEW_DOC.splitlines(True)) - 1}／rev+{len(REVERSE_BLOCK.splitlines(True))}）实得 {len(out_lines)}")
    for probe in ("ORDER BY ts DESC", "result.reverse()", "DCD 裁定①",
                  "class MemoryExtractor:", "if len(result) >= max_turns:",
                  "WHERE ts >= ? AND user_msg != ''"):
        if probe not in text:
            raise SystemExit(f"新文本探针丢失：{probe!r}")
    for gone in ("ORDER BY ts ASC", "（升序），过滤自动化推送噪音。"):
        if gone in text:
            raise SystemExit(f"旧文本仍在：{gone!r}")
    if text.count("result.reverse()") != 1:
        raise SystemExit("reverse 不是一枚")

    print(f"DRY|lines {BASE_LINES}->{len(out_lines)} bytes {BASE_BYTES}->{len(out)} cr=0 defs={n_defs}")
    if do_apply:
        TGT.write_bytes(out)
        print("APPLIED|" + hashlib.md5(out).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
