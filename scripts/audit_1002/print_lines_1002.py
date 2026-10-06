"""通用「打印行号」尺：给文件＋区间，输出带原始行号的正文。

用法：
    python3 -B scripts/audit_1002/print_lines_1002.py <file> <A[-B]> [<A[-B]> ...]

为什么要有它：台账里每个行号都必须来自一把会打印行号的尺（⛔ 手数偏移、⛔ 凭记忆）。
它测不到什么：⛔ 判内容对错，只把那一行原样摊开；区间越界只打 LINE_RANGE_OUT_OF_FILE 不报错退出。
"""
import sys


def main():
    if len(sys.argv) < 3:
        print("USAGE|print_lines_1002.py <file> <A[-B]> ...")
        return 2
    path = sys.argv[1]
    lines = open(path, "rb").read().decode("utf-8", "replace").splitlines()
    print("FILE|%s|LINES=%d|BYTES=%d|CRLF=%d"
          % (path, len(lines),
             open(path, "rb").read().__len__(),
             open(path, "rb").read().count(b"\r\n")))
    for spec in sys.argv[2:]:
        a, _, b = spec.partition("-")
        lo = int(a)
        hi = int(b or a)
        print("=== %s [%d-%d]" % (path, lo, hi))
        if lo < 1 or hi > len(lines) or lo > hi:
            print("LINE_RANGE_OUT_OF_FILE|%s|%d-%d" % (path, lo, hi))
            continue
        for n in range(lo, hi + 1):
            print("%d:%s" % (n, lines[n - 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
