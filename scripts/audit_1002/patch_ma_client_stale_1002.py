"""一次性落码器：把 tests/test_ma_client_args.py 里那例与契约表 §三 相反的断言对齐，
并补一条变异腿，证明「没外发」那条断言不是恒真。

锚点＝行号＋整行 startswith 双验，任何一格对不上就 raise（不落盘）。
写回走 bytes＋splitlines(True)，保住 LF 血统（改前后各数一次 CR）。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

TARGET = pathlib.Path("tests/test_ma_client_args.py")
BASE_LINES = 94
BASE_BYTES = 3585
BASE_MD5 = "d6f13de3964e60bb937b649ff6cc6c35"
CRB = b"\r"

# 现读行号（1 起）：56..60 是被替换的那五例正文，61/62 是空行，63 是下一个 def
OLD_START = 55           # 0 起
OLD_END = 60             # 0 起，开区间上界＝替换掉 56..60

NEW_BLOCK = [
    'def test_retrieve_without_member_sends_nothing():',
    '    """契约表 §三（ADM联动主题注册表与消息契约.md:90）：member_id 必填、fail-closed。',
    '    缺主体时连请求都不发；本例此前断的是外发姿势，与 §三 相反，10-02 与码（724b7ff）一同对齐。',
    '    authority 腿在 tests/contract/test_db_ma_contract.py::test_02。"""',
    '    c = Spy(REFUSAL)',
    '    out = asyncio.run(c.retrieve("牛奶"))',
    '    assert c.calls == [], "没 member_id 却外发了 retrieve_agent_memories"',
    '    assert out == [], "被拒必须返回空，不能抛给对话链"',
    '',
    '',
    'def test_fail_closed_guard_makes_the_difference():',
    '    """变异腿：同一份 Spy、同一条空 member，带门与拆门必须读出不同结果。',
    '    拆门只在函数内的局部姿势里复刻，⛔ 碰生产类、⛔ 改盘上文件。"""',
    '    guarded = Spy(REFUSAL)',
    '    asyncio.run(guarded.retrieve("牛奶"))',
    '',
    '    leaked = Spy(REFUSAL)',
    '',
    '    async def no_guard():        # §三 之前的姿势：缺主体照样把请求发出去',
    '        args = {"query": "牛奶", "top_k": 5}',
    '        await leaked.call_tool("retrieve_agent_memories", args)',
    '',
    '    asyncio.run(no_guard())',
    '',
    '    assert guarded.calls == [], "带门那条却外发了"',
    '    assert [name for name, _ in leaked.calls] == ["retrieve_agent_memories"], "拆门腿没真外发＝变异没咬"',
    '    assert "member_id" not in leaked.calls[0][1], "拆门腿必须复刻缺主体外发"',
    '',
    '',
]


def main(argv: list[str]) -> int:
    apply_flag = "--apply" in argv
    data = TARGET.read_bytes()
    if hashlib.md5(data).hexdigest() != BASE_MD5 or len(data) != BASE_BYTES:
        raise SystemExit(f"BASE-MISMATCH md5={hashlib.md5(data).hexdigest()} bytes={len(data)}")
    cr = data.count(CRB)
    if cr:
        raise SystemExit(f"BASE-CR {cr}，期望 0")
    lines = data.decode("utf-8").splitlines(True)
    if len(lines) != BASE_LINES:
        raise SystemExit(f"BASE-LINES {len(lines)}，期望 {BASE_LINES}")

    checks = [
        (OLD_START, "def test_retrieve_without_member_still_sends_no_member_id"),
        (OLD_START + 1, "    c = Spy(REFUSAL)"),
        (OLD_START + 2, "    out = asyncio.run(c.retrieve("),
        (OLD_START + 3, "    assert c.calls[0][1]"),
        (OLD_START + 4, '    assert out == []'),
        (OLD_END, "\n"),
        (OLD_END + 1, "\n"),
        (OLD_END + 2, "def test_refusal_and_empty_are_distinguishable_at_payload_level"),
    ]
    for idx, prefix in checks:
        if not lines[idx].startswith(prefix):
            raise SystemExit(f"锚点 {idx + 1} 行不符：期望前缀 {prefix!r} 实得 {lines[idx]!r}")

    block = [ln + "\n" for ln in NEW_BLOCK]
    dropped = OLD_END + 2 - OLD_START          # 五例正文＋其后两枚空行
    new = lines[:OLD_START] + block + lines[OLD_END + 2:]
    out = "".join(new).encode("utf-8")

    out_lines = out.decode("utf-8").splitlines(True)
    if out.count(CRB):
        raise SystemExit("CR 血统被引入")
    exp_lines = BASE_LINES - dropped + len(NEW_BLOCK)
    if len(out_lines) != exp_lines:
        raise SystemExit(f"LINE-COUNT 期望 {exp_lines}（{BASE_LINES}-{dropped}+{len(NEW_BLOCK)}）"
                         f" 实得 {len(out_lines)}")
    text = out.decode("utf-8")
    for probe in ("def test_retrieve_without_member_sends_nothing",
                  "def test_fail_closed_guard_makes_the_difference",
                  "def test_refusal_and_empty_are_distinguishable_at_payload_level",
                  "def test_retrieve_sends_schema_param_names",
                  "def test_recall_skips_revoked_rows"):
        if text.count(probe) != 1:
            raise SystemExit(f"PROBE 次数异常：{probe} ×{text.count(probe)}")
    n_defs = sum(1 for ln in out_lines if ln.startswith("def test"))
    if n_defs != 8:          # 原 7 例：改名 1 例＋新增变异腿 1 例
        raise SystemExit(f"顶层例数期望 8 实得 {n_defs}")
    if "still_sends_no_member_id" in text:
        raise SystemExit("旧例名残留")

    print(f"DRY|lines {BASE_LINES}->{len(out_lines)} bytes {BASE_BYTES}->{len(out)} "
          f"cr=0 defs={n_defs}")
    if apply_flag:
        TARGET.write_bytes(out)
        print("APPLIED|" + hashlib.md5(out).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
