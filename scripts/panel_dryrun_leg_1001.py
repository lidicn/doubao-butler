# 面板 dry_run 前端腿（路线图 §14.6 后续项 1，2026-10-01）
# 干什么：把「模拟触发」按钮整条换成试运行——body 带 dry_run: true、确认文案改口、
#         文件头注释跟着改、index.html 忙碌态 '执行中…'→'试跑中…'。两枚文件：
#         butler/static/js/pages/debug.js（PIN 3c035a6d28555d68c02883fdd9c48195）
#         butler/static/index.html（PIN 455041dd03f99c7f58b40e6f98f4a8e5）
# ⛔ 单独上：静态面是 bind mount，落盘即生效；而后端 dry_run 闸要 restart 才进活进程。
#    只上前端＝面板承诺「不播报不通知」、旧路由照样真执行＝对着真人说反话。
#    ⇒ 只能在部署窗内、后端 restart 之后跑：先 plan（不带 --apply），再 --apply。
# 判据：出口两行 md5/字节数，再 GET /js/pages/debug.js 与 / 各取一次，md5 必须与盘上同值。
import hashlib, sys

APPLY = "--apply" in sys.argv

JS = "butler/static/js/pages/debug.js"
JS_PIN = "3c035a6d28555d68c02883fdd9c48195"
HTML = "butler/static/index.html"
HTML_PIN = "455041dd03f99c7f58b40e6f98f4a8e5"

# (1-based lineno, expected prefix of that line, new line text)
JS_EDITS = [
    (8, " * 过程中没有可回放的落点。触发模拟会真执行",
     " * 过程中没有可回放的落点。触发模拟自 2026-10-01 起走引擎的 dry_run 闸（DCD 裁定 C，提交 5fd23df）："),
    (9, " *（trigger_routes.py:249-250",
     " * 三道闸照过、动作不跑（⛔ 播报通知、⛔ 写 trigger_runs）⇒ 本按钮固定发 dry_run: true。"),
    (93, '    if (!window.confirm("「触发模拟」会真执行',
     '    if (!window.confirm("「模拟触发」＝试运行：并发/资源/冷却三道闸照常判定，动作不跑"'),
    (94, '      + "（引擎还没接 dry_run）。确定继续？"',
     '      + "不播报、不通知、不写执行流水。确定试跑？")) return;'),
    (101, "        body: JSON.stringify({ payload: {} }),",
     "        body: JSON.stringify({ payload: {}, dry_run: true }),"),
]
HTML_EDITS = [
    (2335, '                      x-text="$store.app.dbgTestBusy===t.id',
     ('\'执行中…\'', '\'试跑中…\'')),
]


def load(path, pin):
    b = open(path, "rb").read()
    if hashlib.md5(b).hexdigest() != pin:
        print("PIN_FAIL", path, hashlib.md5(b).hexdigest())
        sys.exit(1)
    if b.count(b"\r") != 0:
        print("CR_FAIL", path, b.count(b"\r"))
        sys.exit(1)
    return b.decode("utf-8")


def apply_line_edits(text, edits, tag, whole_line=True):
    lines = text.split("\n")
    for lineno, prefix, payload in edits:
        cur = lines[lineno - 1]
        if not cur.startswith(prefix):
            print("PREFIX_FAIL", tag, lineno, repr(cur[:48]))
            sys.exit(1)
        if whole_line:
            new = payload
        else:
            # 行内替换：全文件可能有同名片段（'执行中…' 另有 1 处），唯一性只在本行判
            if cur.count(payload[0]) != 1:
                print("SUB_LINE_COUNT_FAIL", tag, lineno, cur.count(payload[0]))
                sys.exit(1)
            new = cur.replace(payload[0], payload[1])
        if new == cur:
            print("NOOP_FAIL", tag, lineno)
            sys.exit(1)
        lines[lineno - 1] = new
    return "\n".join(lines)


js_before = load(JS, JS_PIN)
html_before = load(HTML, HTML_PIN)
js_after = apply_line_edits(js_before, JS_EDITS, "js")
html_after = apply_line_edits(html_before, HTML_EDITS, "html", whole_line=False)

# invariants: line counts unchanged, CR stays 0, no old false phrase survives
for name, before, after in (("js", js_before, js_after), ("html", html_before, html_after)):
    if before.count("\n") != after.count("\n"):
        print("NL_FAIL", name, before.count("\n"), after.count("\n"))
        sys.exit(1)
    changed = sum(1 for a, b in zip(before.split("\n"), after.split("\n")) if a != b)
    if changed != (len(JS_EDITS) if name == "js" else len(HTML_EDITS)):
        print("CHANGED_FAIL", name, changed)
        sys.exit(1)
for phrase in ("还没接引擎的 dry_run", "引擎还没接 dry_run", "会真执行这个触发器的动作",
               "JSON.stringify({ payload: {} })"):
    if phrase in js_after:
        print("PHRASE_SURVIVES", phrase)
        sys.exit(1)
# '注释 1 处 + 代码 1 处'；代码那行的本体由整行替换保证
if js_after.count("dry_run: true") != 2:
    print("DRYRUN_COUNT_FAIL", js_after.count("dry_run: true"))
    sys.exit(1)
if js_after.split("\n")[100] != JS_EDITS[4][2]:
    print("BODY_LINE_FAIL", repr(js_after.split("\n")[100]))
    sys.exit(1)
# 本行是唯一带单引号的 '执行中…'（另一处 `执行中…` 是别的引号形制，不受本次影响）
if html_before.count("'执行中…'") != 1 or html_after.count("'执行中…'") != 0:
    print("HTML_BUSY_FAIL", html_before.count("'执行中…'"), html_after.count("'执行中…'"))
    sys.exit(1)
# '试跑中…' 全站已有一处（:2087 技能测试按钮），本行是第二处 ⇒ 派生期望，不写死
if html_after.count("试跑中…") != html_before.count("试跑中…") + 1:
    print("HTML_NEW_FAIL", html_before.count("试跑中…"), html_after.count("试跑中…"))
    sys.exit(1)
# 调用点数量前后必须一字不变（我只是改文案与入参，⛔ 增删调用）
for pat in ("window.confirm(", "this.showToast(", "await fetch(", "JSON.stringify("):
    if js_after.count(pat) != js_before.count(pat):
        print("CALL_COUNT_FAIL", pat, js_before.count(pat), js_after.count(pat))
        sys.exit(1)
# the two confirm lines must still be a balanced JS string concatenation
conf = js_after.split("\n")[92] + "\n" + js_after.split("\n")[93]
if conf.count('"') != 4 or not conf.rstrip().endswith("return;"):
    print("CONFIRM_SHAPE_FAIL", repr(conf))
    sys.exit(1)

print("plan ok: js %d->%d B, html %d->%d B, lines js %d html %d" % (
    len(js_before.encode()), len(js_after.encode()),
    len(html_before.encode()), len(html_after.encode()),
    js_after.count("\n"), html_after.count("\n")))
if not APPLY:
    sys.exit(0)

open(JS, "wb").write(js_after.encode("utf-8"))
open(HTML, "wb").write(html_after.encode("utf-8"))
jb = open(JS, "rb").read()
hb = open(HTML, "rb").read()
if hashlib.md5(jb).hexdigest() == JS_PIN or hashlib.md5(hb).hexdigest() == HTML_PIN:
    print("APPLY_READBACK_FAIL")
    sys.exit(1)
if jb.decode("utf-8") != js_after or hb.decode("utf-8") != html_after:
    print("APPLY_DIFF_FAIL")
    sys.exit(1)
if jb.count(b"\r") or hb.count(b"\r"):
    print("APPLY_CR_FAIL")
    sys.exit(1)
print("APPLIED js", hashlib.md5(jb).hexdigest(), len(jb),
      "| html", hashlib.md5(hb).hexdigest(), len(hb))
