r"""批17 的**第二把尺**：同一件事（butler/ 里「把 JSON 写到文件」）用两种口径各数一遍，并**逐条点名差集**。

为什么要两把：单行 grep `\.write_text\(json\.` 只看得见「同一行里 write_text( 紧跟 json.dumps(」的写法，
`write_text(\n    json.dumps(...))` 这种换行写法它**静默看不见**（本轮实测：grep 17 行 vs AST 23 处）。
⇒ 分母类判据⛔ 一把尺；差集必须逐条点名，ONLY_IN_GREP>0 时说明 AST 漏了某种写法、要解释。

三件事一次做齐：
  1) AST 尺（吃换行）vs grep 尺（单行）＝差集点名；
  2) 「已是 tmp→rename 正解」⛔ 靠文件名猜＝同一函数体内出现 rename 型调用才算，并打出那一行；
  3) 就地截断写数＝AST 全集 − 已原子（本批要修的范围从这里来，最终仍要逐条点开读侧后果）。

用法（权威树根）：python3 -B scripts/audit_1002/probe_b17_reconcile_1002.py
"""
import ast
import re
import subprocess
from pathlib import Path

ROOT = "butler"
GREP_PATTERN = r"\.write_text\(json\."


def ast_write_sites(text):
    """返回 {lineno: (enclosing_func, rename_line, rename_receiver_src)}。

    rename 判据只认「接收者不是字符串字面量」的 `.replace(...)`——`str.replace` 也算 `.replace(`，
    所以把接收者的源码打出来给人看（`tmp.replace` 才是 rename 型提交）。
    """
    out = {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "write_text" and n.args):
            continue
        if not (isinstance(n.args[0], ast.Call) and getattr(n.args[0].func, "attr", "") == "dumps"):
            continue
        holder = [f for f in funcs if f.lineno <= n.lineno <= (f.end_lineno or f.lineno)]
        holder = max(holder, key=lambda f: f.lineno) if holder else None
        rename_line, rename_recv = None, None
        if holder is not None:
            for m in ast.walk(holder):
                if isinstance(m, ast.Call) and getattr(m.func, "attr", "") == "replace" and m.args:
                    recv = m.func.value if isinstance(m.func, ast.Attribute) else None
                    if isinstance(recv, ast.Constant) and isinstance(recv.value, str):
                        continue  # "xxx".replace(...) ＝字符串替换，⛔ rename
                    rename_line, rename_recv = m.lineno, ast.unparse(recv) if recv is not None else "?"
                    break
        out[n.lineno] = (holder.name if holder else "MODULE", rename_line, rename_recv)
    return out


def main():
    files = sorted(Path(ROOT).rglob("*.py"))
    ast_sites, grep_sites, meta = set(), set(), {}
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(GREP_PATTERN, line):
                grep_sites.add(f"{f}:{i}")
        for ln, (fn, rename_line, rename_recv) in ast_write_sites(text).items():
            key = f"{f}:{ln}"
            ast_sites.add(key)
            meta[key] = (fn, rename_line, rename_recv)
    print(f"SCAN_ROOT|{ROOT}|py_files={len(files)}")
    print(f"AST_RULER|sites={len(ast_sites)}")
    print(f"GREP_RULER|pattern={GREP_PATTERN}|lines={len(grep_sites)}")
    only_ast = sorted(ast_sites - grep_sites)
    only_grep = sorted(grep_sites - ast_sites)
    print(f"ONLY_IN_AST={len(only_ast)}  # 单行模式静默看不见（write_text( 换行才接 json.dumps(）")
    for s in only_ast:
        print(f"  +{s}|func={meta[s][0]}")
    print(f"ONLY_IN_GREP={len(only_grep)}  # >0＝AST 侧漏了写法，必须解释")
    for s in only_grep:
        print(f"  -{s}")
    atomic = sorted(s for s in ast_sites if meta[s][1])
    print(f"ALREADY_ATOMIC_in_same_func={len(atomic)}  # 判据＝同一函数体内有 rename 型调用（已剔 str.replace），⛔ 按文件名猜")
    for s in atomic:
        print(f"  ={s}|func={meta[s][0]}|rename@{meta[s][1]}|recv={meta[s][2]}")
    print(f"IN_PLACE_NONATOMIC={len(ast_sites) - len(atomic)}")
    rc = subprocess.run(["grep", "-rn", "json.dump(", ROOT, "--include=*.py"], capture_output=True, text=True)
    dump_lines = [l for l in rc.stdout.splitlines() if l]
    print(f"OTHER_FAMILY|json.dump(_to_handle|count={len(dump_lines)}|rc={rc.returncode}")
    for l in dump_lines:
        parts = l.split(":", 2)  # grep -rn → "path:lineno:code"
        print(f"  #{parts[0]}:{parts[1]}")


main()
