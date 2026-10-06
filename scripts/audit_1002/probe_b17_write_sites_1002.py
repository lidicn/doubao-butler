r"""批17 的量尺（只读）：把 `\.write_text\(json\.` 的每一处点开成「写侧＋读侧」两面，⛔ 我按文件名猜后果。

为什么需要它：§17-9 只登记了分母（17 行／16 文件，其中 5 行是 tmp→rename 的正解本体）。
「就地截断写」这件事**只有当读侧损坏时会静默兜底**才等于「丢数据」——读侧 raise 的，坏文件当场炸出来，后果完全不同。
所以判据是成对的：
  W 写侧：这一行是不是 `.write_text(json.dumps(...))`、所在函数里⛔ 出现 rename 型提交（os.replace / Path.replace）
  R 读侧：同文件里读这个 JSON 的函数（含 `json.loads`）→ 有没有 try、handler 抓什么类型、handler 体里有没有「赋值空容器 / .clear() / 覆盖成默认」
输出⛔ 下结论（分类归人看），只把两面摆齐 + 打 flag。

用法（权威树根）：python3 -B scripts/audit_1002/probe_b17_write_sites_1002.py [子串过滤]
"""
import ast
import sys
from pathlib import Path

WRITE_CALL = "write_text"
READ_CALL = "loads"


def src(node, lines):
    a = node.lineno
    b = node.end_lineno or a
    return "\n".join(lines[a - 1:b])


def try_info(func):
    """函数里包住 json.loads 的 try：handler 抓什么、handler 体里有没有「清零型」兜底。"""
    out = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Try):
            continue
        body_has_load = any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == READ_CALL
                            for n in ast.walk(node))
        if not body_has_load:
            continue
        for h in node.handlers:
            t = "bare" if h.type is None else ast.unparse(h.type)
            hsrc = "\n".join(ast.unparse(s) for s in h.body)
            reset = any(k in hsrc for k in ("= {}", "= []", "= dict()", "= list()", ".clear()", "or {}", "else {}", "= None"))
            out.append(f"    TRY@{node.lineno} handler={t} reset={reset} body={hsrc[:160]!r}")
    return out


def main():
    flt = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted(Path("butler").rglob("*.py"))
    hits = 0
    for f in files:
        if flt and flt not in str(f):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if WRITE_CALL not in text:
            continue
        lines = text.splitlines()
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            print(f"SKIP|{f}|SyntaxError {e}")
            continue
        # 写侧：.write_text(json.dumps(...)…)
        w_sites = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == WRITE_CALL and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Call) and getattr(arg.func, "attr", "") == "dumps":
                    w_sites.append(node.lineno)
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if not w_sites:
            continue
        for wl in sorted(w_sites):
            hits += 1
            holder = [n for n in funcs if n.lineno <= wl <= (n.end_lineno or n.lineno)]
            holder = max(holder, key=lambda n: n.lineno) if holder else None
            fsrc = src(holder, lines) if holder else text
            atomic = ("os.replace(" in fsrc) or (".replace(" in fsrc)
            print(f"### SITE|{f}:{wl}|func={holder.name if holder else 'MODULE'}|ATOMIC_RENAME_IN_FUNC={atomic}")
            for ln, body in enumerate(fsrc.splitlines(), start=(holder.lineno if holder else 1)):
                if WRITE_CALL in body or "json.dumps" in body or atomic and ".replace(" in body or "os.replace" in body:
                    print(f"    W:{ln}: {body.strip()[:150]}")
            if holder is None:
                print("    (module-level 写侧，函数归属＝MODULE)")
            for fn in funcs:
                if fn is holder:
                    continue
                info = try_info(fn)
                if not info:
                    continue
                has_load = any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == READ_CALL
                               for n in ast.walk(fn))
                if not has_load:
                    continue
                print(f"    R|func={fn.name}@{fn.lineno} try_wrapping_loads={len(info)}")
                for row in info:
                    print(row)
    print(f"SUMMARY|sites={hits}|py_files_scanned={len(files)}|root=butler|⛔ 结论在读数件之外的逐行点开")


main()
