#!/usr/bin/env python3
"""批20 一次性落码件 A：`butler/bus/mqtt_client.py` 的死闸 QueueFull 换成循环内计数。

修的第 12 份来源报告 P1-3（＝合并表 表行 21 前半的 MQTT 那一腿）：
  · `self.loop.call_soon_threadsafe(self.queue.put_nowait, ...)` 之后那句
    `except asyncio.QueueFull` 挂在 **paho 网络线程** 上，而 put_nowait 是在**事件循环里**跑的
    ⇒ 队列满时异常落在 loop 的回调错误上（"Exception in callback ..."），那句 except 永不到货；
  · 丢件只有日志、没有计数＝「投出去就以为成了」，多大杯量掉过多少件不可查；
  · 循环已关时 `call_soon_threadsafe` 当场 RuntimeError，会冒泡回 paho 网络线程。

改形：投递改走 `self._enqueue`（跑在循环里，闸落在那里）＋ `self._note_dropped` 计数并按
`_note_bad_payload` 的形制节流 60 秒；线程边界当场失败（QueueFull／RuntimeError）在网络线程侧
只计数与告警，⛔ 再冒泡。⛔ 动队列容量、⛔ 改订阅面、⛔ 丢件后重试入队。

一次性：重跑必须 raise（ALREADY_SCAN 抓新块标记），不接受「再打一遍」。
用法：cd 权威树根 && python3 -B scripts/audit_1002/patch_b20_mqtt_deadguard_1002.py
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

TARGET = pathlib.Path("butler/bus/mqtt_client.py")
BASE_MD5 = "c800af5bc55aa7bf2010f28a95a1abbf"
BASE_LINES = 198
BASE_BYTES = 9344
MARKER = "def _note_dropped("

EDITS = [
    # (1-based 起点, 旧块, 新块) —— 自下而上应用，前面的行号不受后面影响
    (97,
     ['            self.loop.call_soon_threadsafe(self.queue.put_nowait, (msg.topic, payload))',
      '        except asyncio.QueueFull:',
      '            logger.warning("MQTT queue full, drop %s", msg.topic)'],
     ['            # P1-3：队列满是在**事件循环里**咬的，闸也就必须落在那里——旧形制的 except 挂在',
      '            # 网络线程那句 call_soon_threadsafe 上，异常永不到货＝死闸（丢件只进 loop 日志）。',
      '            self.loop.call_soon_threadsafe(self._enqueue, msg.topic, payload)',
      '        except (asyncio.QueueFull, RuntimeError) as e:',
      '            # 循环已关／线程边界当场失败：⛔ 冒泡回 paho 网络线程（它会带走整条收消息链路）',
      '            self._note_dropped(msg.topic)',
      '            logger.warning("MQTT submit failed topic=%s err=%s", msg.topic, e)']),
    (76,
     ['    def _on_message(self, client, userdata, msg):'],
     ['    def _enqueue(self, topic: str, payload: dict) -> None:',
      '        """跑在事件循环里：队列满在这里咬，计数与告警也就落在这里。"""',
      '        try:',
      '            self.queue.put_nowait((topic, payload))',
      '        except asyncio.QueueFull:',
      '            self._note_dropped(topic)',
      '',
      '    def _note_dropped(self, topic: str) -> None:',
      '        """丢件先变成数，再变成 60 秒一条的痕迹（节流形制同 _note_bad_payload）。"""',
      '        self.dropped += 1',
      '        now = time.time()',
      '        if now - self.__dict__.get("_drop_logged_at", 0.0) < 60.0:',
      '            return',
      '        self.__dict__["_drop_logged_at"] = now',
      '        logger.warning("MQTT_QUEUE_FULL 队列满丢弃：累计=%d 最近主题=%s（60 秒内不重复播报）",',
      '                       self.dropped, topic)',
      '',
      '    def _on_message(self, client, userdata, msg):']),
    (31,
     ['        self.connected = asyncio.Event()'],
     ['        self.connected = asyncio.Event()',
      '        self.dropped = 0                  # P1-3：队列满丢件计数（⛔ 只打日志＝丢了多少不可查）']),
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
    if raw.count(b"\r\n") != 0:
        die("EOL", "%s CRLF=%d 期望 0（本件只吃 LF 血统）" % (TARGET, raw.count(b"\r\n")))
    if not text.endswith("\n"):
        die("ENDNL", "%s 末行无换行" % TARGET)
    lines = text.split("\n")
    if len(lines) - 1 != BASE_LINES:
        die("BASE_LINES", "%s 现读 %d 行，期望 %d" % (TARGET, len(lines) - 1, BASE_LINES))
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

    new_text = "\n".join(lines)
    try:
        compile(new_text, str(TARGET), "exec")
    except SyntaxError as e:
        die("COMPILE", "%s:%s %s" % (e.filename, e.lineno, e.msg))

    tree = ast.parse(new_text, str(TARGET))
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MQTTClient"), None)
    if cls is None:
        die("AST", "MQTTClient 类不在了")
    names = [m.name for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for need in ("_enqueue", "_note_dropped", "_on_message"):
        if need not in names:
            die("AST", "类里缺 %s（现有方法 %r）" % (need, names))
    init = next((m for m in cls.body
                 if isinstance(m, ast.FunctionDef) and m.name == "__init__"), None)
    if init is None:
        die("AST", "__init__ 不在了")
    drop_init = [n.lineno for n in ast.walk(init)
                 if isinstance(n, ast.Assign) and any(
                     isinstance(t, ast.Attribute) and t.attr == "dropped" for t in n.targets)]
    if len(drop_init) != 1:
        die("AST", "__init__ 里 `self.dropped =` 现读 %d 处 %r，期望 1" % (len(drop_init), drop_init))
    enqueue = next(m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name == "_enqueue")
    qf = [n for n in ast.walk(enqueue)
          if isinstance(n, ast.ExceptHandler)
          and any(isinstance(x, ast.Attribute) and x.attr == "QueueFull" for x in ast.walk(n.type))]
    if len(qf) != 1:
        die("AST", "_enqueue 里 QueueFull 闸现读 %d 处，期望 1（闸必须落在循环内那一层）" % len(qf))
    note = next(m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name == "_note_dropped")
    inc = [n.lineno for n in ast.walk(note)
           if isinstance(n, (ast.AugAssign,)) and isinstance(n.target, ast.Attribute)
           and n.target.attr == "dropped"]
    if len(inc) != 1:
        die("AST", "_note_dropped 里 `self.dropped +=` 现读 %d 处，期望 1" % len(inc))
    if new_text.count("self.loop.call_soon_threadsafe(self.queue.put_nowait") != 0:
        die("AST", "旧投递腿（put_nowait 直接进 call_soon_threadsafe）还在＝闸仍是死的")
    if new_text.count("except (asyncio.QueueFull, RuntimeError)") != 1:
        die("AST", "线程边界那条腿的 except 形制不符（应恰 1 处）")
    if new_text.count("MQTT_QUEUE_FULL") != 1:
        die("AST", "丢件告警 token 现读非 1 处")

    TARGET.write_bytes(new_text.encode("utf-8"))
    back = TARGET.read_bytes()
    if back.count(b"\r\n") != 0:
        die("WRITE_VERIFY", "写回后 CRLF=%d" % back.count(b"\r\n"))
    if md5(back) == BASE_MD5:
        die("WRITE_VERIFY", "写回 md5 与基线同号＝压根没写")
    if not back.endswith(b"\n"):
        die("WRITE_VERIFY", "写回后末行无换行")
    bt = back.decode("utf-8")
    if bt.count(MARKER) != 1:
        die("WRITE_VERIFY", "标记出现 %d 次，期望 1" % bt.count(MARKER))
    got_lines = bt.count("\n")
    if got_lines != WANT_LINES:
        die("WRITE_VERIFY", "写回 %d 行，期望 %d（基线 %d + 净增 %d）"
            % (got_lines, WANT_LINES, BASE_LINES, DELTA))
    ast.parse(bt, str(TARGET))

    print("PATCH_OK|file=%s md5=%s lines=%d bytes=%d crlf=0 delta=%d want_lines=%d"
          % (TARGET, md5(back), got_lines, len(back), DELTA, WANT_LINES))
    for start, old, new in sorted(EDITS, key=lambda e: e[0]):
        print("ANCHOR|%s:%d-%d replaced(%d->%d)"
              % (TARGET, start, start + len(old) - 1, len(old), len(new)))
    print("GUARD|dropped_init@%s | enqueue_queuefull=%d | dropped_inc@%s | old_put_nowait_leg=0"
          % (drop_init, len(qf), inc))


if __name__ == "__main__":
    main()
