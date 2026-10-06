#!/usr/bin/env python3
"""批20 一次性落码件 C：`butler/app.py` 定位轮询的退出闩＋停机收尾，SSE 丢件计数；`runtime.py` 加计数位。

修的两处来源：
  · 第 12 份报告 P1-5：`_presence_poll_loop`（:967-1026）是 `while True` ＋ 结尾
    `await _asyncio.sleep(interval)`——停机时它既不响应退出信号，lifespan 收尾（:780-793）
    又只 cancel 了 consumer，不 cancel/await 这枚任务 ⇒ 带 pending 跨过停机窗口，
    而 `close()` 已经跑过＝往已关闭的连接里写；间隔 30 秒时还要空跑一整轮。
  · 合并表 表行 21 后半：`_consume`（:109-110）对慢订阅者 `except asyncio.QueueFull: pass`
    ＝丢件无声，⛔ 事后查不到掉了几件。

改形：lifespan 先造 `rt._stop_event = asyncio.Event()` 再 `create_task`；循环改成
`while not stop.is_set()`，等待换成 `wait_for(stop.wait(), timeout=interval)`（置位当场醒）；
收尾先置闩、再 cancel＋await 收回任务，全排在 `close()` 之前；SSE 丢件改记 `rt.sse_dropped`
并打 WARNING（token `SSE_SUBSCRIBER_QUEUE_FULL`）。⛔ 主动踢掉慢订阅者（那是加固，另账）、
⛔ 改队列容量。

结构闸全部跑在**写盘之前**（批19 的形制：闸红＝一件都没写，可安全重跑）。
一次性：重跑必须 raise（ALREADY_SCAN 抓两处新块标记），不接受「再打一遍」。
用法：cd 权威树根 && python3 -B scripts/audit_1002/patch_b20_app_lifecycle_sse_1002.py
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

APP = pathlib.Path("butler/app.py")
APP_BASE_MD5 = "79ed01759202efb02062e86a382cff4b"
APP_BASE_LINES = 1026
APP_BASE_BYTES = 49529
RT = pathlib.Path("butler/runtime.py")
RT_BASE_MD5 = "45787c2f03f89d99d458a09b3ebd36a0"
RT_BASE_LINES = 59
RT_BASE_BYTES = 2893
MARKERS = ("rt._stop_event = asyncio.Event()", "SSE_SUBSCRIBER_QUEUE_FULL")

APP_EDITS = [
    (1026,
     ['        await _asyncio.sleep(interval)'],
     ['        # 等的是退出闩（带超时），⛔ 等满周期：置位之后哪怕间隔 30 秒也要当场醒',
      '        try:',
      '            await _asyncio.wait_for(stop.wait(), timeout=interval)',
      '        except _asyncio.TimeoutError:',
      '            pass']),
    (979,
     ['    interval = engine.config.get("poll_interval_seconds", 10)',
      '    logger.info("presence poll loop started (interval=%ds)", interval)',
      '',
      '    while True:'],
     ['    interval = engine.config.get("poll_interval_seconds", 10)',
      '    # P1-5：闩由 lifespan 装配；缺则当场补一枚并挂回 rt（⛔ 读不到就一路裸跑到停机窗口之后）',
      '    stop = getattr(rt, "_stop_event", None)',
      '    if stop is None:',
      '        stop = _asyncio.Event()',
      '        rt._stop_event = stop',
      '    logger.info("presence poll loop started (interval=%ds)", interval)',
      '',
      '    while not stop.is_set():']),
    (780,
     ['            consumer.cancel()',
      '            mqtt.stop()'],
     ['            consumer.cancel()',
      '            # P1-5 后半：定位轮询⛔ 是 consumer 的下属，它有独立的退出闩与收尾，',
      '            # 且必须排在 close() 之前（晚一步就是往已关闭的连接里写）。',
      '            _stop = getattr(rt, "_stop_event", None)',
      '            if _stop is not None:',
      '                _stop.set()',
      '            _pt = getattr(rt, "_presence_poll_task", None)',
      '            if _pt is not None:',
      '                _pt.cancel()',
      '                try:',
      '                    await asyncio.wait_for(_pt, timeout=2.0)',
      '                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):',
      '                    _pt.cancel()          # 超时没收回＝再钉一次取消标志，⛔ 让收尾卡在这里',
      '            mqtt.stop()']),
    (473,
     ['        # 启动定位轮询后台任务（保持引用防止 GC 回收）',
      '        rt._presence_poll_task = asyncio.create_task(_presence_poll_loop(rt))'],
     ['        # P1-5 前半：退出闩必须先于轮询任务出生，否则循环只能去读一个不存在的东西',
      '        rt._stop_event = asyncio.Event()',
      '        # 启动定位轮询后台任务（保持引用防止 GC 回收）',
      '        rt._presence_poll_task = asyncio.create_task(_presence_poll_loop(rt))']),
    (109,
     ['                    except asyncio.QueueFull:',
      '                        pass'],
     ['                    except asyncio.QueueFull:',
      '                        # 表行 21 后半：慢订阅者丢件⛔ 无声吞掉，先记数再留痕',
      '                        rt.sse_dropped = getattr(rt, "sse_dropped", 0) + 1',
      '                        logger.warning("SSE_SUBSCRIBER_QUEUE_FULL 订阅者队列满，已丢弃 1 件：累计=%d",',
      '                                       rt.sse_dropped)']),
]
APP_DELTA = sum(len(n) - len(o) for _, o, n in APP_EDITS)
APP_WANT_LINES = APP_BASE_LINES + APP_DELTA

RT_EDITS = [
    (52,
     ['    sse_subscribers: set = field(default_factory=set)'],
     ['    sse_subscribers: set = field(default_factory=set)',
      '    sse_dropped: int = 0                 # SSE 广播丢件计数（表行 21 后半：⛔ 无声丢弃不可查）']),
]
RT_DELTA = sum(len(n) - len(o) for _, o, n in RT_EDITS)
RT_WANT_LINES = RT_BASE_LINES + RT_DELTA


def die(label: str, detail: str = "") -> None:
    print("PATCH_FAIL|%s %s" % (label, detail))
    sys.exit(1)


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def load(path, base_md5, base_lines, base_bytes):
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    for m in MARKERS:
        if m in text:
            die("ALREADY_SCAN", "新块标记 %r 已在 %s（本件一次性，重跑＝未定义行为）" % (m, path))
    if raw.count(b"\r\n") != 0:
        die("EOL", "%s CRLF=%d 期望 0（本件只吃 LF 血统）" % (path, raw.count(b"\r\n")))
    if not text.endswith("\n"):
        die("ENDNL", "%s 末行无换行" % path)
    lines = text.split("\n")
    if len(lines) - 1 != base_lines:
        die("BASE_LINES", "%s 现读 %d 行，期望 %d" % (path, len(lines) - 1, base_lines))
    if len(raw) != base_bytes:
        die("BASE_BYTES", "%s 现读 %d B，期望 %d" % (path, len(raw), base_bytes))
    if md5(raw) != base_md5:
        die("BASE_MD5", "%s 现读 %s，期望 %s" % (path, md5(raw), base_md5))
    return lines


def apply_edits(lines, edits, path):
    for start, old, new in sorted(edits, key=lambda e: -e[0]):
        idx = start - 1
        got = lines[idx:idx + len(old)]
        if got != old:
            for k, (g, e) in enumerate(zip(got, old)):
                if g != e:
                    die("ANCHOR", "%s 第 %d 行不符：现读 %r 期望 %r" % (path, idx + k + 1, g, e))
            die("ANCHOR", "%s 第 %d 块行数不符：现读 %d 期望 %d" % (path, start, len(got), len(old)))
        lines[idx:idx + len(old)] = new


def find_func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def attr_calls(node, attr):
    """方法调用 foo.attr(...)。"""
    return [n.lineno for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


def bare_calls(node, name):
    """裸函数调用 name(...)。"""
    return [n.lineno for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]


def writes_attr(node, attr):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            out += [n.lineno for t in n.targets
                    if isinstance(t, ast.Attribute) and t.attr == attr]
        elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Attribute) \
                and n.target.attr == attr:
            out.append(n.lineno)
    return out


def check_app(tree, text):
    loop = find_func(tree, "_presence_poll_loop")
    if loop is None:
        die("AST", "_presence_poll_loop 不在了")
    if attr_calls(loop, "sleep"):
        die("AST", "循环里还有 sleep(:%r)＝间隔一到才醒，P1-5 那半没修" % attr_calls(loop, "sleep"))
    if not attr_calls(loop, "wait_for"):
        die("AST", "循环里⛔ wait_for(stop.wait())＝退出闩没接上")
    if not attr_calls(loop, "is_set"):
        die("AST", "循环的 while 条件里⛔ is_set()")
    if not writes_attr(loop, "_stop_event"):
        die("AST", "循环里没有 rt._stop_event 兜底赋值")

    life = find_func(tree, "lifespan")
    if life is None:
        die("AST", "lifespan 不在了")
    ev = writes_attr(life, "_stop_event")
    ct = attr_calls(life, "create_task")
    if len(ev) != 1:
        die("AST", "lifespan 里 `rt._stop_event =` 现读 %d 处 %r，期望 1" % (len(ev), ev))
    if not ct:
        die("AST", "lifespan 里找不到 create_task 锚点")
    if ev[0] > ct[0]:
        die("AST", "退出闩(:%d)晚于轮询任务(:%d)＝循环出生时读不到闩" % (ev[0], ct[0]))
    pt = [n.lineno for n in ast.walk(life)
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
          and any(isinstance(a, ast.Constant) and a.value == "_presence_poll_task" for a in n.args)]
    if not pt:
        die("AST", "收尾里⛔ 出现 rt._presence_poll_task＝轮询任务仍带 pending 跨停机窗口")
    if not attr_calls(life, "cancel"):
        die("AST", "收尾缺 cancel 腿")
    if not attr_calls(life, "wait_for"):
        die("AST", "收尾缺 wait_for 腿（只标取消标志⛔ 算收回）")
    close_lines = sorted(attr_calls(life, "close") + bare_calls(life, "close"))
    if not close_lines:
        die("AST", "收尾里找不到 close()（尺子先失效，⛔ 判绿）")
    if pt[0] > close_lines[0]:
        die("AST", "轮询收尾(:%d)排在 close()(:%d)之后＝往已关闭的连接里写"
            % (pt[0], close_lines[0]))
    if text.count("                _stop.set()") != 1:
        die("AST", "收尾里 `_stop.set()` 现读 %d 处，期望 1＝循环那条腿永不醒"
            % text.count("                _stop.set()"))

    consume = find_func(tree, "_consume")
    if consume is None:
        die("AST", "_consume 不在了")
    if not writes_attr(consume, "sse_dropped"):
        die("AST", "_consume 里没有 rt.sse_dropped 计数＝丢件仍无声")
    if text.count("SSE_SUBSCRIBER_QUEUE_FULL") != 1:
        die("AST", "SSE 告警 token 现读 %d 处，期望 1" % text.count("SSE_SUBSCRIBER_QUEUE_FULL"))


def check_rt(tree, text):
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Runtime"), None)
    if cls is None:
        die("AST", "Runtime 类不在了")
    ann = [n.lineno for n in cls.body
           if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
           and n.target.id == "sse_dropped"]
    if len(ann) != 1:
        die("AST", "Runtime 里 `sse_dropped` 声明现读 %d 处 %r，期望 1" % (len(ann), ann))
    if text.count("sse_subscribers: set = field(default_factory=set)") != 1:
        die("AST", "sse_subscribers 那行被我改坏了")


def write_and_verify(path, new_text, base_md5, want_lines):
    path.write_bytes(new_text.encode("utf-8"))
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    if raw.count(b"\r\n") != 0:
        die("WRITE_VERIFY", "%s 写回后 CRLF=%d" % (path, raw.count(b"\r\n")))
    if md5(raw) == base_md5:
        die("WRITE_VERIFY", "%s 写回 md5 与基线同号＝压根没写" % path)
    if not text.endswith("\n"):
        die("WRITE_VERIFY", "%s 写回后末行无换行" % path)
    got = text.count("\n")
    if got != want_lines:
        die("WRITE_VERIFY", "%s 写回 %d 行，期望 %d" % (path, got, want_lines))
    ast.parse(text, str(path))
    print("PATCH_OK|file=%s md5=%s lines=%d bytes=%d crlf=0" % (path, md5(raw), got, len(raw)))


def report_anchors(path, edits):
    for start, old, new in sorted(edits, key=lambda e: e[0]):
        print("ANCHOR|%s:%d-%d replaced(%d->%d)"
              % (path, start, start + len(old) - 1, len(old), len(new)))


def main() -> None:
    # ── app.py：闸全在写盘前
    lines = load(APP, APP_BASE_MD5, APP_BASE_LINES, APP_BASE_BYTES)
    apply_edits(lines, APP_EDITS, APP)
    new_text = "\n".join(lines)
    try:
        compile(new_text, str(APP), "exec")
    except SyntaxError as e:
        die("COMPILE", "%s:%s %s" % (e.filename, e.lineno, e.msg))
    tree = ast.parse(new_text, str(APP))
    check_app(tree, new_text)
    write_and_verify(APP, new_text, APP_BASE_MD5, APP_WANT_LINES)
    report_anchors(APP, APP_EDITS)

    # ── runtime.py
    rlines = load(RT, RT_BASE_MD5, RT_BASE_LINES, RT_BASE_BYTES)
    apply_edits(rlines, RT_EDITS, RT)
    rtext = "\n".join(rlines)
    try:
        compile(rtext, str(RT), "exec")
    except SyntaxError as e:
        die("COMPILE_RT", "%s:%s %s" % (e.filename, e.lineno, e.msg))
    rtree = ast.parse(rtext, str(RT))
    check_rt(rtree, rtext)
    write_and_verify(RT, rtext, RT_BASE_MD5, RT_WANT_LINES)
    report_anchors(RT, RT_EDITS)


if __name__ == "__main__":
    main()
