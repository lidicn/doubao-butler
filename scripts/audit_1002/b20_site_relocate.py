"""把批20 之后行号漂移的在册点位**重定位**：不猜，两头都从 AST 现读。

对每一枚 (file, old_line, name)：
  旧侧 = `git show HEAD:butler/<file>`（HEAD `64919c5e` 是批20 落码前的基线，盘上 M 态＝批20）
  新侧 = 当前工作树同一枚文件的 AST 里所有该名字的 Call，带包围函数／async 性／接收者原文
判据＝旧行原文 == 新行原文，且包围函数名与 async/sync 性一致，且**旧侧同名同文本也只有一枚**
（多义＝这枚不能只靠行号认，红着交人看）。
锚点腿同理，但比对的是 def 本体：批20 只许加行，⛔ 把同步 def 翻成 async。
"""
import ast
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
DRIFT = [
    ("app.py", 267, "run"),
    ("app.py", 464, "start"),
    ("app.py", 539, "start"),
    ("app.py", 740, "start"),
    ("app.py", 755, "start"),
    ("app.py", 763, "start"),
    ("app.py", 781, "stop"),
    ("app.py", 784, "stop"),
    ("app.py", 788, "shutdown"),
]
ANCHORS = [
    ("bus/mqtt_client.py", 117, "start", "MQTTClient"),
    ("bus/mqtt_client.py", 121, "stop", "MQTTClient"),
]


def git_head_text(rel: str) -> str:
    return subprocess.run(["git", "--no-optional-locks", "show", f"HEAD:butler/{rel}"],
                          cwd=REPO, capture_output=True, text=True, check=True).stdout


def call_rows(text: str, name: str):
    tree = ast.parse(text)
    lines = text.splitlines()
    encl_of = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ch in ast.walk(node):
                encl_of.setdefault(id(ch), node)
    rows = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == name:
            e = encl_of.get(id(node))
            rows.append({
                "line": node.lineno,
                "text": lines[node.lineno - 1].strip(),
                "recv": ast.unparse(node.func.value) if node.func.value else "",
                "fn": e.name if e else "<module>",
                "async": isinstance(e, ast.AsyncFunctionDef) if e else False,
            })
    return rows


def def_row(text: str, name: str, cls: str):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == name:
                    return {"line": m.lineno,
                            "kind": "async" if isinstance(m, ast.AsyncFunctionDef) else "sync",
                            "head": ast.unparse(m)[:90]}
    return None


def main():
    unambiguous = 0
    for rel, old_line, name in DRIFT:
        head = git_head_text(rel)
        cur = (REPO / "butler" / rel).read_text(encoding="utf-8")
        hlines = head.splitlines()
        old_text = hlines[old_line - 1].strip()
        h_rows = [r for r in call_rows(head, name) if r["text"] == old_text]
        c_rows = [r for r in call_rows(cur, name) if r["text"] == old_text]
        ok = len(h_rows) == 1 and len(c_rows) == 1 and \
            h_rows[0]["fn"] == c_rows[0]["fn"] and h_rows[0]["async"] == c_rows[0]["async"]
        unambiguous += 1 if ok else 0
        print(f"SITE {rel}:{old_line} name={name} head_same_text={len(h_rows)} cur_same_text={len(c_rows)} ok={ok}")
        print(f"  old_text|{old_text}")
        for r in h_rows:
            print(f"  HEAD line={r['line']} fn={r['fn']} async={r['async']} recv={r['recv']}")
        for r in c_rows:
            print(f"  CUR  line={r['line']} delta={r['line'] - old_line:+d} fn={r['fn']} async={r['async']} recv={r['recv']}")
        if len(c_rows) != 1:
            for r in [x for x in call_rows(cur, name) if x["line"] >= old_line - 3 and x["line"] <= old_line + 40]:
                print(f"  NEARBY line={r['line']} text|{r['text']} fn={r['fn']}")
    for rel, old_line, name, cls in ANCHORS:
        head = git_head_text(rel)
        cur = (REPO / "butler" / rel).read_text(encoding="utf-8")
        h, c = def_row(head, name, cls), def_row(cur, name, cls)
        print(f"ANCHOR {rel} {cls}.{name} head={h['line'] if h else 'NO-DEF'}({h['kind'] if h else '-'})"
              f" cur={c['line'] if c else 'NO-DEF'}({c['kind'] if c else '-'})"
              f" kind_held={bool(h and c and h['kind'] == c['kind'] == 'sync')}")
        print(f"  cur_head|{c['head'] if c else '-'}")
    print(f"RELOC|sites={len(DRIFT)} unambiguous={unambiguous} ambiguous={len(DRIFT) - unambiguous}")
    return 0 if unambiguous == len(DRIFT) else 1


if __name__ == "__main__":
    sys.exit(main())
