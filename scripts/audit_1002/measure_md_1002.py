"""独立量尺：量一份 markdown 的段数/行数/字节数/CR/围栏数（⛔ 把正文塞进 shell 双引号）。

用法：python scripts/audit_1002/measure_md_1002.py <file.md> [<file2.md> ...]
"""
import sys

for path in sys.argv[1:]:
    raw = open(path, "rb").read()
    text = raw.decode("utf-8")
    lines = text.splitlines()
    h2 = len([l for l in lines if l.startswith("## ")])
    h3 = len([l for l in lines if l.startswith("### ")])
    fence = len([l for l in lines if l.startswith("~~~") or l.startswith("```")])
    print("%s|lines=%d|bytes=%d|CR=%d|H2=%d|H3=%d|fences=%d|dollarparen=%d|endnl=%s"
          % (path, len(lines), len(raw), raw.count(bytearray([13])), h2, h3, fence,
             text.count("$("), text.endswith(bytearray([10]).decode())))
