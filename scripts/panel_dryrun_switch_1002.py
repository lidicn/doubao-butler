# -*- coding: utf-8 -*-
"""面板 dry_run **开关** 前端腿（DCD 裁定 20261001 §二＝裁 B1；2026-10-02）。

本节口径覆盖 `scripts/panel_dryrun_leg_1001.py` §方案：那一版把按钮固定成试运行，
B1 要的是**开关**——缺省试运行、用户可拨到真执行，且文案必须跟着开关说话。
1001 那张单的前提（"须等后端 restart 之后才可上"）已被裁定判定失效（dry_run 闸 `5fd23df`
早容器启动 9 小时 9 分就在活进程里），故本腿与 restart 窗解耦，任何时候上都是真的。

为什么必须动这两枚文件：
  1. `butler/static/js/pages/debug.js`：请求体根本没带 `dry_run`，而后端读的是
     `body.get("dry_run", True)`——缺省虽已 fail-closed（`065a654`），但面板没得选，
     而文案还写"会真执行"＝按钮对着真人说反话（裁定：方向安全但文案必须修）。
  2. `butler/static/index.html`：卡片里那块红字警告 + 忙碌态文案 + **`debug.js?v=1`**。
     最后一条容易漏：`sw.js` 对静态资源是 cache-first、键＝完整 URL（含 query），
     ⛔ 改了 debug.js 而不 bump `?v=`，用户手机上拿到的还是缓存里那份旧码＝盘上新、屏幕上旧。

跑法：
  python3 scripts/panel_dryrun_switch_1002.py            # plan：只验 PIN 与逐行前缀，⛔ 写盘
  python3 scripts/panel_dryrun_switch_1002.py --verify   # 出口闸（改前必须红，改后必须绿）
  python3 scripts/panel_dryrun_switch_1002.py --apply    # 过闸才写；写后立刻再跑一遍闸
判据（写在码里，不靠嘴）：两枚基底 md5 PIN、逐行前缀锚（行号+startwith）、行数增量＝0、
CR 数不变、index.html 的 BOM 保住、新串精确计数、旧反话串 0 命中、⛔ 把心跳账字面串引进静态面。
"""
import hashlib
import io
import sys

APPLY = "--apply" in sys.argv
VERIFY = "--verify" in sys.argv

JS = "butler/static/js/pages/debug.js"
JS_PIN = "3c035a6d28555d68c02883fdd9c48195"
JS_PIN_AFTER = "ffddb124c3da0781efbbb7331a393a99"      # 本腿 --apply 落盘后的指纹（10-02 现量）
HTML = "butler/static/index.html"
HTML_PIN = "455041dd03f99c7f58b40e6f98f4a8e5"
HTML_PIN_AFTER = "dc157c15ffff7b0047fae60c871908fa"    # 同上

# (1-based 行号, 该行必须以这个前缀开头, 新行全文)
JS_EDITS = [
    (8, ' * 过程中没有可回放的落点。触发模拟会真执行：payload["test"]=True 还没接引擎的 dry_run',
     ' * 过程中没有可回放的落点。触发模拟走面板上的「试运行／真执行」开关（DCD 裁定 20261001 §二 B1）：'),
    (9, ' *（trigger_routes.py:249-250 那行日志自己写着 NOTE），所以这里强制二次确认。',
     ' * 缺省＝试运行（body 带 dry_run: true，引擎闸门 butler/triggers/engine.py:337 只评估不执行）。'),
    (16, '  s.dbgTriggers = []; s.dbgTestBusy = ""; s.dbgTestResult = null;',
     '  s.dbgTriggers = []; s.dbgTestBusy = ""; s.dbgTestResult = null; s.dbgDryRun = true;'),
    (93, '    if (!window.confirm("「触发模拟」会真执行这个触发器的动作——播报和通知都会真的发出去"',
     '    const dry = this.dbgDryRun !== false;   // 取不到开关＝试运行（fail-closed，⛔ 抽个 undefined 就真播报）'),
    (94, '      + "（引擎还没接 dry_run）。确定继续？")) return;',
     '    if (!window.confirm(dry ? "「模拟触发」＝试运行：并发/资源/冷却三道闸照常判定，动作不跑——不播报、不发通知、不写执行流水。确定试跑？"'
     ' : "「模拟触发」＝真执行：播报和通知会真的发出去，并写 trigger_runs 执行流水。确定继续？")) return;'),
    (101, '        body: JSON.stringify({ payload: {} }),',
     '        body: JSON.stringify({ payload: {}, dry_run: dry }),'),
    (106, '      if (d && d.ok) this.showToast("已模拟触发：" + id);',
     '      if (d && d.ok) this.showToast((dry ? "试运行完成（动作未执行）：" : "已模拟触发：") + id);'),
]

HTML_EDITS = [
    (2320, '        <div class="text-xs text-[var(--err)] mb-2">',
     '        <label class="flex items-start gap-2 text-xs mb-2 cursor-pointer select-none">'),
    (2321, '          ⚠ 会真执行：端点把 <code>payload["test"]=True</code> 传进去了，但引擎还没接 <code>dry_run</code>',
     '          <input type="checkbox" class="mt-0.5 w-4 h-4 accent-amber-500" x-model="$store.app.dbgDryRun">'),
    (2322, '          （<code>butler/api/trigger_routes.py:249-250</code> 那行日志自己写着 NOTE）⇒ 点一下可能真的播报、真的发通知。',
     '          <span><b class="text-amber-400" x-text="$store.app.dbgDryRun===false ? \'真执行\' : \'试运行（缺省）\'"></b>'
     '：<span x-text="$store.app.dbgDryRun===false ? \'播报和通知会真的发出去，并写 trigger_runs 执行流水\''
     ' : \'并发/资源/冷却三道闸照常判定，动作不跑——不播报、不发通知、不写执行流水\'"></span>'
     '（后端入口缺省已 fail-closed＝不执行，<code>butler/api/trigger_routes.py:233-239</code>；本开关把选择显式发给后端）</span>'),
    (2323, '        </div>',
     '        </label>'),
    (2335, '                      x-text="$store.app.dbgTestBusy===t.id ? \'执行中…\' : \'模拟触发\'"></button>',
     '                      x-text="$store.app.dbgTestBusy===t.id ? ($store.app.dbgDryRun===false ? \'执行中…\' : \'试跑中…\') : \'模拟触发\'"></button>'),
    (2403, '  <script src="/js/pages/debug.js?v=1"></script>',
     '  <script src="/js/pages/debug.js?v=2"></script>'),
]

# 出口闸：(串, 期望出现次数)；⛔ 只要求 ">0"——那会把"出现过一次别处的旧串"也算成改成功
HTML_PRESENT = [('x-model="$store.app.dbgDryRun"', 1),
                ('<script src="/js/pages/debug.js?v=2"></script>', 1),
                ('试运行（缺省）', 1),
                ('($store.app.dbgDryRun===false ? \'执行中…\' : \'试跑中…\')', 1)]
HTML_ABSENT = ['会真执行', 'trigger_routes.py:249-250', '<script src="/js/pages/debug.js?v=1"></script>',
               'trigger_audit', 'triggers/audit']
JS_PRESENT = [('s.dbgDryRun = true;', 1),
              ('const dry = this.dbgDryRun !== false;', 1),
              ('body: JSON.stringify({ payload: {}, dry_run: dry }),', 1),
              ('(dry ? "试运行完成（动作未执行）：" : "已模拟触发：")', 1)]
JS_ABSENT = ['还没接引擎的 dry_run', 'trigger_routes.py:249-250',
             'body: JSON.stringify({ payload: {} }),', 'trigger_audit', 'triggers/audit']


def load(path, pin):
    b = io.open(path, "rb").read()
    md5 = hashlib.md5(b).hexdigest()
    if md5 != pin:
        print("PIN_FAIL %s now=%s want=%s（有人动过这枚文件＝本腿的基底作废，先复核再改）"
              % (path, md5, pin))
        sys.exit(1)
    return b


def face(path, b, pin, pin_after):
    md5 = hashlib.md5(b).hexdigest()
    if md5 == pin_after:
        return "after（本腿已落盘）"
    if md5 == pin:
        return "base（改前基线）"
    return "unknown(%s)（既不是基线也不是本腿产物＝第三方动过）" % md5


def line_edits(b, edits, tag):
    lines = b.split(b"\n")
    for lineno, prefix, new in edits:
        cur = lines[lineno - 1]
        if not cur.startswith(prefix.encode()):
            print("PREFIX_FAIL %s L%d want=%r got=%r" % (tag, lineno, prefix[:40], cur[:60]))
            sys.exit(1)
        lines[lineno - 1] = new.encode()
    return b"\n".join(lines)


def gates(text, present, absent, tag):
    bad = []
    for needle, want in present:
        got = text.count(needle)
        if got != want:
            bad.append("PRESENT %s %r want=%d got=%d" % (tag, needle[:40], want, got))
    for needle in absent:
        got = text.count(needle)
        if got:
            bad.append("ABSENT %s %r got=%d" % (tag, needle[:40], got))
    return bad


if VERIFY:
    # 出口闸只看现状：⛔ 走改写管线（现状若已是改后形制，逐行前缀锚会红在别处、报错误导人）
    red = []
    for path, present, absent, pin, pin_after in (
            (JS, JS_PRESENT, JS_ABSENT, JS_PIN, JS_PIN_AFTER),
            (HTML, HTML_PRESENT, HTML_ABSENT, HTML_PIN, HTML_PIN_AFTER)):
        b = io.open(path, "rb").read()
        print("FACE %s = %s / bytes=%d CR=%d" % (path, face(path, b, pin, pin_after),
                                                 len(b), b.count(b"\r")))
        red += gates(b.decode("utf-8"), present, absent, path)
    for line in red:
        print("  RED %s" % line)
    print("VERIFY_RESULT red=%d" % len(red))
    sys.exit(0 if not red else 1)

js_b = load(JS, JS_PIN)
html_b = load(HTML, HTML_PIN)
js_txt = js_b.decode("utf-8")
html_txt = html_b.decode("utf-8")   # BOM 是第一个字符，逐行改⛔ 动得到它

js_after = line_edits(js_b, JS_EDITS, JS).decode("utf-8")
html_after = line_edits(html_b, HTML_EDITS, HTML).decode("utf-8")

print("PLAN %s: 行数 %d->%d（增量必须 0）" % (JS, js_txt.count("\n"), js_after.count("\n")))
print("PLAN %s: 行数 %d->%d（增量必须 0）" % (HTML, html_txt.count("\n"), html_after.count("\n")))
for name, before, after in ((JS, js_txt, js_after), (HTML, html_txt, html_after)):
    if before.count("\n") != after.count("\n"):
        print("NL_FAIL %s" % name)
        sys.exit(1)

bad_before = (gates(js_txt, JS_PRESENT, JS_ABSENT, JS)
              + gates(html_txt, HTML_PRESENT, HTML_ABSENT, HTML))
bad_after = (gates(js_after, JS_PRESENT, JS_ABSENT, JS)
             + gates(html_after, HTML_PRESENT, HTML_ABSENT, HTML))

if bad_after:
    print("SELF_CHECK_FAIL 改完仍不绿＝本腿自己的方案有问题：")
    for line in bad_after:
        print("  %s" % line)
    sys.exit(1)
if not bad_before:
    print("PRECHECK_FAIL 改之前闸就全绿——说明这枚文件已经是改后的形状，"
          "本腿要么被重复执行、要么锚点测不到东西（⛔ 再写一遍）")
    sys.exit(1)

if not APPLY:
    print("PLAN_ONLY JS %d->%d B / HTML %d->%d B（未写盘；--apply 才写。"
          "改前闸红 %d 条＝锚点确实测到东西）"
          % (len(js_b), len(js_after.encode()), len(html_b), len(html_after.encode()),
             len(bad_before)))
    sys.exit(0)

for path, before, after in ((JS, js_b, js_after.encode()), (HTML, html_b, html_after.encode())):
    if after.count(b"\r") != before.count(b"\r"):
        print("CR_FAIL %s %d->%d" % (path, before.count(b"\r"), after.count(b"\r")))
        sys.exit(1)
for path, after in ((JS, js_after.encode()), (HTML, html_after.encode())):
    with io.open(path, "wb") as f:
        f.write(after)
    print("WROTE %-34s bytes=%d lines=%d CR=%d md5=%s"
          % (path, len(after), after.count(b"\n"), after.count(b"\r"),
             hashlib.md5(after).hexdigest()))
print("APPLY_DONE 两枚文件落盘（static 面＝bind mount，落盘即对真人生效；"
      "手机上是否看到新码＝另量一次 GET，见单子里的判据行）")
