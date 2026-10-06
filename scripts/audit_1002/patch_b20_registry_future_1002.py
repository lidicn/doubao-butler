#!/usr/bin/env python3
"""批20 一次性落码件 B：`butler/triggers/registry.py` 的 wrap_scheduler_job 把 Future 结局接回台账。

修的第 12 份来源报告 P1-4：`func()` 返回的常常是 `run_coroutine_threadsafe` 的 Future，
wrapper 立刻 `mark_success` 并 +1，而那张 Future 的 `.result()` 从没被人取过 ⇒ 协程里抛出的
异常永远不被取回，触发源状态停在 running、台账写着 success（假绿＝「投出去就以为成了」）。

改形：投递成功后给返回值挂一张 done-callback；返回值不是 Future（同步 job）当场早退，口径不变。
done-callback 里 `fut.result()` 取回异常 → `logger.exception("SCHED_JOB_FAILED …")`（ERROR）
＋ `mark_error`。**保留** mark_success 那一行——它是「每轮一行」的投递心跳口径
（triggers/audit.py:22、store/ledger_freshness.py:6 都按这个口径读），⛔ 在这里改成「只记真成功」；
代价是失败的那一轮会写两行（success＋error），这条残余登记在台账批20 段。

一次性：重跑必须 raise（ALREADY_SCAN 抓新块标记），不接受「再打一遍」。
用法：cd 权威树根 && python3 -B scripts/audit_1002/patch_b20_registry_future_1002.py
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

TARGET = pathlib.Path("butler/triggers/registry.py")
BASE_MD5 = "c11327de7e178c1d7c01e1a4fae1f9dd"
BASE_LINES = 242
BASE_BYTES = 9712
BASE_CRLF = 242
MARKER = "def _watch_future("

EDITS = [
    (236,
     ['                self.mark_success(source_id, duration_ms)',
      '                return result'],
     ['                self.mark_success(source_id, duration_ms)',
      '                # P1-4：投递成功之后把 Future 挂上 done-callback，协程里的异常才会落到台账',
      '                self._watch_future(source_id, result)',
      '                return result']),
    (222,
     ['    def wrap_scheduler_job(self, source_id: str, func: Callable) -> Callable:'],
     ['    def _watch_future(self, source_id: str, result) -> None:',
      '        """把投出去那枚 Future 的结局接回台账（P1-4：投递成功⛔ 等于协程跑成功）。',
      '',
      '        mark_success 记的是**投递**这一格——「每轮一行」的心跳口径，⛔ 在这里改口径；',
      '        协程里炸的异常原本没人 .result()，于是状态永远停在 running。',
      '        """',
      '        if not (hasattr(result, "add_done_callback") and hasattr(result, "result")):',
      '            return                       # 同步 job：返回值不是 Future，投递即结果，口径不变',
      '',
      '        def _done(fut):',
      '            try:',
      '                fut.result()',
      '            except BaseException as e:   # 含 CancelledError：取消也是「没跑成」',
      '                logger.exception("SCHED_JOB_FAILED %s：投递成功但任务里炸了（此前已记 dispatch success）",',
      '                                 source_id)',
      '                try:',
      '                    self.mark_error(source_id, "%s: %s" % (type(e).__name__, e))',
      '                except Exception:',
      '                    logger.debug("mark_error 二次失败 source=%s", source_id, exc_info=True)',
      '',
      '        try:',
      '            result.add_done_callback(_done)',
      '        except Exception as e:',
      '            logger.debug("attach future done-callback 失败 source=%s err=%s", source_id, e)',
      '',
      '    def wrap_scheduler_job(self, source_id: str, func: Callable) -> Callable:']),
]
DELTA = sum(len(n) - len(o) for _, o, n in EDITS)
WANT_LINES = BASE_LINES + DELTA


def die(label: str, detail: str = "") -> None:
    print("PATCH_FAIL|%s %s" % (label, detail))
    sys.exit(1)


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def main() -> None:
    raw = TARGET.read_bytes()
    text = raw.decode("utf-8")
    if MARKER in text:
        die("ALREADY_SCAN", "新块标记已在 %s（本件一次性，重跑＝未定义行为）" % TARGET)
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    if crlf != BASE_CRLF or lf != crlf:
        die("EOL", "%s 换行血统不符：CRLF=%d 期望 %d，LF=%d（本件只吃全 CRLF 血统，⛔ 整文件改写）"
            % (TARGET, crlf, BASE_CRLF, lf))
    if not text.endswith("\r\n"):
        die("ENDNL", "%s 末行无 CRLF 换行" % TARGET)
    lines = text.split("\r\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if len(lines) != BASE_LINES:
        die("BASE_LINES", "%s 现读 %d 行，期望 %d" % (TARGET, len(lines), BASE_LINES))
    if len(raw) != BASE_BYTES:
        die("BASE_BYTES", "%s 现读 %d B，期望 %d" % (TARGET, len(raw), BASE_BYTES))
    if md5(raw) != BASE_MD5:
        die("BASE_MD5", "%s 现读 %s，期望 %s" % (TARGET, md5(raw), BASE_MD5))

    for start, old, new in sorted(EDITS, key=lambda e: -e[0]):
        idx = start - 1
        got = lines[idx:idx + len(old)]
        if got != old:
            for k, (g, e) in enumerate(zip(got, old)):
                if g != e:
                    die("ANCHOR", "第 %d 行不符：现读 %r 期望 %r" % (idx + k + 1, g, e))
            die("ANCHOR", "第 %d 块行数不符：现读 %d 期望 %d" % (start, len(got), len(old)))
        lines[idx:idx + len(old)] = new

    new_text = "\r\n".join(lines) + "\r\n"
    try:
        compile(new_text, str(TARGET), "exec")
    except SyntaxError as e:
        die("COMPILE", "%s:%s %s" % (e.filename, e.lineno, e.msg))

    tree = ast.parse(new_text, str(TARGET))
    cls = next((n for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == "TriggerRegistry"), None)
    if cls is None:
        die("AST", "TriggerRegistry 类不在了")
    watch = next((m for m in cls.body
                  if isinstance(m, ast.FunctionDef) and m.name == "_watch_future"), None)
    if watch is None:
        die("AST", "_watch_future 不在了")
    ac = [n.lineno for n in ast.walk(watch)
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
          and n.func.attr == "add_done_callback"]
    if len(ac) != 1:
        die("AST", "_watch_future 里 add_done_callback 现读 %d 处 %r，期望 1" % (len(ac), ac))
    res = [n.lineno for n in ast.walk(watch)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "result"]
    if not res:
        die("AST", "_watch_future 里没有 fut.result()＝异常还是没人取回")
    exc = [n.lineno for n in ast.walk(watch)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "exception"]
    if not exc:
        die("AST", "_watch_future 里没有 logger.exception＝失败不落 ERROR 级")
    me = [n.lineno for n in ast.walk(watch)
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "mark_error"]
    if len(me) != 1:
        die("AST", "_watch_future 里 mark_error 现读 %d 处 %r，期望 1" % (len(me), me))
    if "SCHED_JOB_FAILED" not in new_text:
        die("AST", "SCHED_JOB_FAILED token 不在（台账反查会断链）")
    early = [n.lineno for n in ast.walk(watch) if isinstance(n, ast.Return) and n.value is None]
    if not early:
        die("AST", "_watch_future 缺同步 job 早退腿＝非 Future 返回值会被当 Future 挂回调")

    wrap = next((m for m in cls.body
                 if isinstance(m, ast.FunctionDef) and m.name == "wrap_scheduler_job"), None)
    if wrap is None:
        die("AST", "wrap_scheduler_job 不在了")
    inner = next((m for m in wrap.body if isinstance(m, ast.FunctionDef) and m.name == "wrapper"), None)
    if inner is None:
        die("AST", "wrapper 内层函数不在了")
    succ = [n.lineno for n in ast.walk(inner)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "mark_success"]
    watch_calls = [n.lineno for n in ast.walk(inner)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "_watch_future"]
    if len(succ) != 1 or len(watch_calls) != 1:
        die("AST", "wrapper 里 mark_success=%r _watch_future=%r，各期望 1 处" % (succ, watch_calls))
    if succ[0] > watch_calls[0]:
        die("AST", "_watch_future(:%d) 早于 mark_success(:%d)＝心跳口径被我改动了"
            % (watch_calls[0], succ[0]))
    rets = [n.lineno for n in ast.walk(inner) if isinstance(n, ast.Return) and n.value is not None]
    if not rets:
        die("AST", "wrapper 不再返回 result＝同步 job 的返回值被我吃掉")

    TARGET.write_bytes(new_text.encode("utf-8"))
    back = TARGET.read_bytes()
    bt = back.decode("utf-8")
    if back.count(b"\r\n") != WANT_LINES:
        die("WRITE_VERIFY", "写回后 CRLF=%d，期望 %d（换行血统必须保持全 CRLF）"
            % (back.count(b"\r\n"), WANT_LINES))
    if back.count(b"\n") != back.count(b"\r\n"):
        die("WRITE_VERIFY", "写回后混血：LF=%d CRLF=%d" % (back.count(b"\n"), back.count(b"\r\n")))
    if md5(back) == BASE_MD5:
        die("WRITE_VERIFY", "写回 md5 与基线同号＝压根没写")
    if not bt.endswith("\r\n"):
        die("WRITE_VERIFY", "写回后末行无 CRLF")
    if bt.count(MARKER) != 1:
        die("WRITE_VERIFY", "标记出现 %d 次，期望 1" % bt.count(MARKER))
    got_lines = bt.count("\r\n")
    if got_lines != WANT_LINES:
        die("WRITE_VERIFY", "写回 %d 行，期望 %d（基线 %d + 净增 %d）"
            % (got_lines, WANT_LINES, BASE_LINES, DELTA))
    ast.parse(bt, str(TARGET))

    print("PATCH_OK|file=%s md5=%s lines=%d bytes=%d crlf=%d delta=%d want_lines=%d"
          % (TARGET, md5(back), got_lines, len(back), back.count(b"\r\n"), DELTA, WANT_LINES))
    for start, old, new in sorted(EDITS, key=lambda e: e[0]):
        print("ANCHOR|%s:%d-%d replaced(%d->%d)"
              % (TARGET, start, start + len(old) - 1, len(old), len(new)))
    print("ORDER|mark_success@%d < _watch_future@%d（心跳口径⛔ 改，只在其后挂回调）"
          % (succ[0], watch_calls[0]))
    print("GUARD|add_done_callback@%s result@%s logger.exception@%s mark_error@%s early_return@%s"
          % (ac, res, exc, me, early))


if __name__ == "__main__":
    main()
