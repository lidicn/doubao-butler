"""批15 落码前基底读数：五个目标文件的 md5/行数/字节/CR + 待改区域的原文（带行号）。"""
import hashlib
import os

FILES = ["butler/config.py", "butler/tts/base.py", "butler/tts/manager.py",
         "butler/tts/edge_tts.py", "butler/tts/kokoro.py", "butler/tts/nowvoice_tts.py"]
REGIONS = {
    "butler/config.py": [(34, 44)],
    "butler/tts/manager.py": [(96, 106), (144, 152)],
    "butler/tts/edge_tts.py": [(1, 20), (52, 62)],
    "butler/tts/kokoro.py": [(1, 16), (29, 38)],
    "butler/tts/nowvoice_tts.py": [(1, 22), (78, 88)],
}

for rel in FILES:
    raw = open(rel, "rb").read()
    text = raw.decode("utf-8")
    lines = text.splitlines()
    print("FILE|%s|md5=%s|lines=%d|bytes=%d|CR=%d|endnl=%s" % (
        rel, hashlib.md5(raw).hexdigest(), len(lines), len(raw), raw.count(b"\r"),
        raw.endswith(b"\n")))
print("CWD|%s" % os.getcwd())
for rel, spans in REGIONS.items():
    lines = open(rel, encoding="utf-8").read().split("\n")
    for a, b in spans:
        print("--- %s:%d-%d ---" % (rel, a, b))
        for i in range(a, min(b, len(lines)) + 1):
            print("%4d| %s" % (i, lines[i - 1]))
