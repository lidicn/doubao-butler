"""批210 补格：把「凭据语义但值短」那两枚盲区键**定性**（10-13）。

现读病根：`scripts/leak_gate.py` 的 A 臂只吃长度≥10 的值（`MIN_VALUE_LEN`），理由是短值做子串匹配
必假阳（`MQTT_USER=butler` 那种一次撞出 1845 处，把真信号埋掉）。代价是两枚键**从来没被比对过**：
读数原文（每次跑闸都打印）：`[blind-spot] 凭据语义但值短（子串必假阳）2 枚：['BUTLER_WEB_PASSWORD', 'DOUBAO_API_KEY']`。

本档不猜它们"应该没写在码里"——用**整词**匹配（值前后不许再接词字符）撞一遍跟踪文件，并配一条
**能咬的正对照**：把值插进内存里造的合成文本，匹配器必须命中它（否则这把尺本身是坏的，0 命中＝假绿）。
输出⛔ 回显值本体，只出键名＋长度＋`file:lineno`＋该行掩码后的形状（值替换成 «V»）。
命中≠泄露：短值可能撞上散文里的普通词 ⇒ 逐条读上下文再定性（判例在册：假阳与假阴同罪）。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

TREE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TREE / "scripts"))
import leak_gate  # noqa: E402


def git(*args: str) -> str:
    r = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=TREE,
                       capture_output=True, text=True, errors="replace")
    return r.stdout


def word_re(value: str) -> re.Pattern[str]:
    """整词：值前后不许再接 [A-Za-z0-9_]，⛔ 裸子串。"""
    return re.compile(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])")


def main() -> int:
    parsed = leak_gate.parse_env((TREE / ".env").read_text(encoding="utf-8", errors="ignore"))
    long_vals, short, nonsecret = leak_gate.split_env_values(parsed)
    short_names = [k for k, _ in short]
    print(f"[denominator] 盲区键 {len(short)} 枚：{short_names}（长度={[n for _, n in short]}，⛔ 值出口）")
    if not short_names:
        print("READ: 此刻无盲区键 ⇒ 本档无事")
        return 0

    targets = {k: parsed[k] for k in short_names if parsed.get(k)}
    for k, v in targets.items():
        synth = f"Authorization: Bearer {v}\n"
        hit = bool(word_re(v).search(synth))
        print(f"CONTROL key={k} len={len(v)} 正对照（合成一行）={'能咬' if hit else '**咬不住⇒本尺坏**'}")
        if not hit:
            return 2

    files = [f for f in git("ls-files").splitlines() if f.strip()]
    print(f"[scope] 跟踪文件 {len(files)} 枚")
    total = 0
    for rel in files:
        p = TREE / rel
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="ignore")
        for no, line in enumerate(text.splitlines(), 1):
            for k, v in targets.items():
                if word_re(v).search(line):
                    total += 1
                    masked = line.strip().replace(v, "«V»")[:120]
                    print(f"  HIT key={k} {rel}:{no}  {masked}")
    print(f"[verdict] 盲区键整词命中 {total} 处（命中≠泄露，逐条读上下文再定性）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
