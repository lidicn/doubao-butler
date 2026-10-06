r"""批15 一次性落码器：`_env_bool` 恒 False 三元 + TTS 缓存 key 缺 speed（四落点收敛为一条公式）。

设计要点（踩过的坑都在这儿）：
  1. **整行切片 + 行号 + 原文子串三重定位**：任何一条锚点在本文件里必须唯一命中，且行号对得上；
     命不中或命中多处直接 raise，不写盘。
  2. **逐行保换行符血统**：base.py/kokoro.py 全 CRLF、edge_tts.py 86 行里只有 1 行带 CR，
     动过的行沿用该行原本的 ending，未动的行按字节原样拷贝（⛔ 整文件抹成 LF）。
  3. **ALREADY_APPLIED 门放在最前**（批14 的教训：守卫排在校验门后面＝永远走不到的死分支）。
  4. 期望产物先算出来再验（ast 可解析、函数数守恒、hashlib 残留归零、探针短语），全部过了才写。

用法：python3 scripts/audit_1002/patch_b15_env_bool_1002.py            # 干跑
      python3 scripts/audit_1002/patch_b15_env_bool_1002.py --apply   # 落盘（重跑必 raise）
"""
import argparse
import ast
import hashlib
import os
import sys

SENTINEL = {
    "butler/config.py": '    if v in ("0", "false", "no", "off"):\n',
    "butler/tts/base.py": "def cache_filename(",
    "butler/tts/manager.py": "cache_filename(text, voice, engine, speed)",
    "butler/tts/edge_tts.py": "cache_filename(text, voice, \"edge-tts\", speed)",
    "butler/tts/kokoro.py": "cache_filename(text, voice, \"kokoro\", speed)",
    "butler/tts/nowvoice_tts.py": "cache_filename(text, voice, \"nowvoice\", speed)",
}

SPEC = {
    "butler/config.py": {
        "md5": "ac9ef6150a6a3897aa4185c03a199d07", "lines": 395, "bytes": 18231, "cr": 0,
        "edits": [
            ("sub", 40, 'if v in ("0", "false", "no", "off", ""):',
             '    if v in ("0", "false", "no", "off"):'),
            ("sub", 41, 'return False if v == "" else False', '        return False'),
        ],
        "must_absent": ['return False if v == "" else False', '"off", "")'],
        "must_present": ['    if v in ("0", "false", "no", "off"):\n'],
    },
    "butler/tts/base.py": {
        "md5": "662a8a60b3be365472f0b29c9b448ade", "lines": 24, "bytes": 663, "cr": 24,
        "edits": [
            ("ins_after", 3, "", ["import hashlib"]),
            ("append", None, "", [
                "",
                "",
                "def cache_filename(text: str, voice: str, engine: str, speed: float) -> str:",
                '    """缓存文件名的唯一公式：text|voice|engine|speed（speed 缺位＝改语速仍播旧音频）。"""',
                '    return hashlib.sha1(f"{text}|{voice}|{engine}|{speed}".encode("utf-8")).hexdigest() + ".mp3"',
            ]),
        ],
        "must_present": ["import hashlib", "def cache_filename("],
        "must_absent": [],
    },
    "butler/tts/manager.py": {
        "md5": "7e5881537134c0b8715f011808bbc60b", "lines": 272, "bytes": 11427, "cr": 0,
        "edits": [
            ("del", 10, "import hashlib", None),
            ("sub", 17, "from butler.tts.base import TTSResult",
             "from butler.tts.base import TTSResult, cache_filename"),
            ("sub", 98, "def _cache_hit(self, engine: str, text: str, voice: str)",
             "    def _cache_hit(self, engine: str, text: str, voice: str, speed: float) "
             "-> TTSResult | None:"),
            ("sub", 101, "key = hashlib.sha1(",
             "        filename = cache_filename(text, voice, engine, speed)"),
            ("del", 102, 'filename = f"{key}.mp3"', None),
            ("sub", 148, "cached = self._cache_hit(backend, text, voice)",
             "        cached = self._cache_hit(backend, text, voice, speed)"),
        ],
        "must_absent": ["import hashlib", "hashlib.sha1", "voice) -> TTSResult | None:"],
        "must_present": ["cache_filename(text, voice, engine, speed)",
                         "_cache_hit(backend, text, voice, speed)"],
    },
    "butler/tts/edge_tts.py": {
        "md5": "5e084bb0263ce2ca68b7f684f5987dad", "lines": 86, "bytes": 3108, "cr": 1,
        "edits": [
            ("del", 5, "import hashlib", None),
            ("sub", 13, "from butler.tts.base import TTSResult",
             "from butler.tts.base import TTSResult, cache_filename"),
            ("sub", 58, "key = hashlib.sha1(",
             '        filename = cache_filename(text, voice, "edge-tts", speed)'),
            ("del", 59, 'filename = f"{key}.mp3"', None),
        ],
        "must_absent": ["import hashlib", "hashlib.sha1"],
        "must_present": ['cache_filename(text, voice, "edge-tts", speed)'],
    },
    "butler/tts/kokoro.py": {
        "md5": "4eaee607828e3b2245a455c781bc9022", "lines": 59, "bytes": 2029, "cr": 59,
        "edits": [
            ("del", 4, "import hashlib", None),
            ("sub", 12, "from butler.tts.base import TTSResult",
             "from butler.tts.base import TTSResult, cache_filename"),
            ("sub", 33, "key = hashlib.sha1(",
             '        filename = cache_filename(text, voice, "kokoro", speed)'),
            ("del", 34, 'filename = f"{key}.mp3"', None),
        ],
        "must_absent": ["import hashlib", "hashlib.sha1"],
        "must_present": ['cache_filename(text, voice, "kokoro", speed)'],
    },
    "butler/tts/nowvoice_tts.py": {
        "md5": "e7ef9472f83f7865f761e74b83ba076b", "lines": 96, "bytes": 3652, "cr": 0,
        "edits": [
            ("del", 5, "import hashlib", None),
            ("sub", 12, "from butler.tts.base import TTSResult",
             "from butler.tts.base import TTSResult, cache_filename"),
            ("sub", 83, "key = hashlib.sha1(",
             '        filename = cache_filename(text, voice, "nowvoice", speed)'),
            ("del", 84, 'filename = f"{key}.mp3"', None),
        ],
        "must_absent": ["import hashlib", "hashlib.sha1"],
        "must_present": ['cache_filename(text, voice, "nowvoice", speed)'],
    },
}


def split_keep(text):
    lines = text.splitlines(keepends=True)
    return lines


def parts(line):
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def build(rel, lines, edits):
    """按行号倒序应用编辑；未动的行按原字节拷贝。返回 (new_lines, 动过的行号集合)。"""
    touched = set()
    out = list(lines)
    ordered = sorted([e for e in edits], key=lambda e: (e[1] if e[1] is not None else 10 ** 9),
                     reverse=True)
    for kind, lineno, expect, content in ordered:
        if kind == "append":
            ending = parts(out[-1])[1] or "\n"
            for item in content:
                out.append(item + ending)
            continue
        body, ending = parts(out[lineno - 1])
        if expect and expect not in body:
            raise SystemExit("ANCHOR_MISS|%s:%d|expect=%r|actual=%r" % (rel, lineno, expect, body))
        touched.add(lineno)
        if kind == "del":
            del out[lineno - 1]
        elif kind == "sub":
            out[lineno - 1] = content + ending
        elif kind == "ins_after":
            out[lineno:lineno] = [c + ending for c in content]
        else:
            raise SystemExit("BAD_KIND|%s" % kind)
    return out, touched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    if not os.path.isdir(os.path.join(args.root, "butler")):
        raise SystemExit("NO_REPO|%s" % os.path.abspath(args.root))

    plan = []
    for rel, spec in SPEC.items():
        path = os.path.join(args.root, rel)
        raw = open(path, "rb").read()
        text = raw.decode("utf-8")
        sent = SENTINEL[rel]
        if sent in text:
            raise SystemExit("ALREADY_APPLIED|%s" % rel)
        md5 = hashlib.md5(raw).hexdigest()
        lines = split_keep(text)
        if md5 != spec["md5"] or len(lines) != spec["lines"] or len(raw) != spec["bytes"] \
                or raw.count(b"\r") != spec["cr"]:
            raise SystemExit("BASELINE|%s|md5=%s/%s lines=%d/%d bytes=%d/%d cr=%d/%d"
                             % (rel, md5, spec["md5"], len(lines), spec["lines"],
                                len(raw), spec["bytes"], raw.count(b"\r"), spec["cr"]))
        for kind, lineno, expect, _c in spec["edits"]:
            if expect and lineno is not None:
                hit = sum(1 for ln in lines if expect in ln)
                if hit != 1:
                    raise SystemExit("ANCHOR_NOT_UNIQUE|%s|%r|hits=%d" % (rel, expect, hit))
        new_lines, touched = build(rel, lines, spec["edits"])
        new_text = "".join(new_lines)
        try:
            tree = ast.parse(new_text)
        except SyntaxError as e:
            raise SystemExit("SYNTAX|%s|%s" % (rel, e))
        for ph in spec["must_present"]:
            if ph not in new_text:
                raise SystemExit("MUST_PRESENT|%s|%r" % (rel, ph))
        for ph in spec["must_absent"]:
            if ph in new_text:
                raise SystemExit("MUST_ABSENT|%s|%r" % (rel, ph))
        fcount = sum(1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        obcount = sum(1 for n in ast.walk(ast.parse(text))
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        expect_delta = 1 if rel == "butler/tts/base.py" else 0
        if fcount - obcount != expect_delta:
            raise SystemExit("FUNCS|%s|before=%d after=%d expect_delta=%d"
                             % (rel, obcount, fcount, expect_delta))
        for idx, ln in enumerate(lines, 1):
            if idx not in touched and new_text.count(ln) < 1:
                raise SystemExit("COLLATERAL|%s:%d|未动的行在产物里找不到" % (rel, idx))
        nraw = new_text.encode("utf-8")
        plan.append((rel, path, nraw, {
            "md5_old": md5, "md5_new": hashlib.md5(nraw).hexdigest(),
            "lines": "%d->%d" % (len(lines), len(new_lines)),
            "bytes": "%d->%d" % (len(raw), len(nraw)),
            "cr": "%d->%d" % (raw.count(b"\r"), nraw.count(b"\r")),
            "funcs": "%d->%d" % (obcount, fcount),
        }))

    for rel, _p, _b, info in plan:
        print("PLAN|%s|%s" % (rel, info))
    if not args.apply:
        print("DRY|files=%d|未写盘" % len(plan))
        return 0
    for rel, path, blob, info in plan:
        with open(path, "wb") as fh:
            fh.write(blob)
        if open(path, "rb").read() != blob:
            raise SystemExit("WRITE_VERIFY|%s" % rel)
        print("APPLIED|%s|%s" % (rel, info))
    # 重跑必须 raise（ALREADY_APPLIED 在最前）
    return 0


if __name__ == "__main__":
    sys.exit(main())
