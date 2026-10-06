#!/usr/bin/env python3
"""批19 一次性落码件：`butler/store/db.py` 的 `get_conn` 改成「锁内建连＋先 `_init` 后发布」。

修的第 12 份来源报告 P0-2 现象①②（＝合并表 表行 3 前两枚／表行 24 前半）：
  · 判定在锁外 ⇒ 并发首调各建一条，先建的那条被覆盖后永不关闭；
  · `_conn = c` 在 `_init(_conn)` **之前** ⇒ 表还没建完的连接已对外可见，且 `_init` 一抛错
    那条坏连接就永久留在模块全局上（此后⛔ 再跑过一次建表）。
改形：快路径无锁早退；建连／建表／发布全在 `_lock` 内，双检在后；`_init` 抛错关掉连接、
全局保持 None。⛔ 动调用点、⛔ 换 `threading.local`、⛔ 设 `isolation_level`（③ 那半挂既有账）。

一次性：重跑必须 raise（ALREADY_SCAN 抓新块标记），不接受「再打一遍」。
用法：cd 权威树根 && python3 -B scripts/audit_1002/patch_b19_db_getconn_1002.py
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

TARGET = pathlib.Path("butler/store/db.py")
BASE_MD5 = "5754194f3548a795d04fdb89c04e042d"
BASE_LINES = 593
BASE_BYTES = 17558
START_LINE = 35                      # 1-based：`def get_conn() ...`
OLD = [
    "def get_conn() -> sqlite3.Connection:",
    "",
    "    global _conn",
    "",
    "    if _conn is None:",
    "",
    "        s = get_settings()",
    "",
    "        Path(s.data_dir).mkdir(parents=True, exist_ok=True)",
    "",
    "        db_path = Path(s.data_dir) / \"butler.db\"",
    "",
    "        c = sqlite3.connect(str(db_path), check_same_thread=False)",
    "",
    "        c.execute(\"PRAGMA journal_mode=WAL\")",
    "",
    "        c.execute(\"PRAGMA synchronous=NORMAL\")",
    "",
    "        c.execute(\"PRAGMA busy_timeout=5000\")",
    "",
    "        c.row_factory = sqlite3.Row",
    "",
    "        with _lock:",
    "",
    "            _conn = c",
    "",
    "        _init(_conn)",
    "",
    "        logger.info(\"sqlite opened at %s (WAL)\", db_path)",
    "",
    "    return _conn",
]
NEW = [
    "def get_conn() -> sqlite3.Connection:",
    "    \"\"\"懒初始化：建连＋建表全在 `_lock` 内，且先跑完 `_init` 再发布 `_conn`。",
    "",
    "    判定放在锁外时并发首调会各建一条，先建的那条被覆盖后就没人关；先发布再建表时别的线程",
    "    能拿到「表还没建完」的连接，而 `_init` 一抛错那条坏连接还会被永久缓存。所以双检、建连、",
    "    建表、发布全收进 `_lock`，`_init` 失败则关掉连接并保持 None（下次重跑建表）。连接已发布",
    "    后走无锁快路径——热路径⛔ 排进全局锁（现网多模态 P95 已 6,259ms）。",
    "    \"\"\"",
    "    global _conn",
    "    if _conn is not None:",
    "        return _conn",
    "    with _lock:",
    "        if _conn is not None:                # 双检：等锁期间可能已由别人发布",
    "            return _conn",
    "        s = get_settings()",
    "        Path(s.data_dir).mkdir(parents=True, exist_ok=True)",
    "        db_path = Path(s.data_dir) / \"butler.db\"",
    "        c = sqlite3.connect(str(db_path), check_same_thread=False)",
    "        c.execute(\"PRAGMA journal_mode=WAL\")",
    "        c.execute(\"PRAGMA synchronous=NORMAL\")",
    "        c.execute(\"PRAGMA busy_timeout=5000\")",
    "        c.row_factory = sqlite3.Row",
    "        try:",
    "            _init(c)                         # ★ 建表／ALTER 迁移跑完之前这条连接不对外可见",
    "        except Exception:",
    "            c.close()                        # ★ 坏连接⛔ 留在全局上、也⛔ 漏句柄",
    "            raise",
    "        _conn = c",
    "        logger.info(\"sqlite opened at %s (WAL)\", db_path)",
    "        return _conn",
]
MARKER = "# 双检：等锁期间可能已由别人发布"


def die(label: str, detail: str = "") -> None:
    print("PATCH_FAIL|%s %s" % (label, detail))
    sys.exit(1)


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def main() -> None:
    raw = TARGET.read_bytes()
    text = raw.decode("utf-8")
    lines = text.split("\n")

    # 1) 一次性闸：先扫「新码已在场」，避免把第二次跑当成第二次成功
    if MARKER in text:
        die("ALREADY_SCAN", "新块标记已在 %s（本件一次性，重跑＝未定义行为）" % TARGET)
    if raw.count(b"\r\n") != 0:
        die("EOL", "%s CRLF=%d 期望 0（本批只吃 LF 血统）" % (TARGET, raw.count(b"\r\n")))
    if not text.endswith("\n"):
        die("ENDNL", "%s 末行无换行" % TARGET)
    if len(lines) - 1 != BASE_LINES:
        die("BASE_LINES", "%s 现读 %d 行，期望 %d" % (TARGET, len(lines) - 1, BASE_LINES))
    if len(raw) != BASE_BYTES:
        die("BASE_BYTES", "%s 现读 %d B，期望 %d" % (TARGET, len(raw), BASE_BYTES))
    if md5(raw) != BASE_MD5:
        die("BASE_MD5", "%s 现读 %s，期望 %s" % (TARGET, md5(raw), BASE_MD5))

    # 2) 锚点：整块逐行等值（含空行），⛔ 靠「第一处命中」定位
    idx = START_LINE - 1
    got = lines[idx:idx + len(OLD)]
    if got != OLD:
        for k, (g, e) in enumerate(zip(got, OLD)):
            if g != e:
                die("ANCHOR", "第 %d 行不符：现读 %r 期望 %r" % (idx + k + 1, g, e))
        die("ANCHOR", "行数不符：现读 %d 期望 %d" % (len(got), len(OLD)))

    new_lines = lines[:idx] + NEW + lines[idx + len(OLD):]
    new_text = "\n".join(new_lines)

    # 3) 编译闸：语法坏了⛔ 落盘
    try:
        compile(new_text, str(TARGET), "exec")
    except SyntaxError as e:
        die("COMPILE", "%s:%s %s" % (e.filename, e.lineno, e.msg))

    # 4) 结构闸：顺序（`_init` 在发布之前）＋锁内⛔ 调 get_conn＋失败腿关掉连接
    tree = ast.parse(new_text, str(TARGET))
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "get_conn"), None)
    if fn is None:
        die("AST", "get_conn 不在了")
    lock_body = [n for n in ast.walk(fn)
                 if isinstance(n, ast.With) and any(
                     isinstance(m.context_expr, ast.Name) and m.context_expr.id == "_lock"
                     for m in n.items)]
    if len(lock_body) != 1:
        die("AST", "get_conn 里 `with _lock` 现读 %d 处，期望 1" % len(lock_body))
    body = lock_body[0].body

    def walk_block(block):
        # ⛔ 直接 ast.walk(list)：`ast.walk` 对非 AST 对象静默产出 0 个节点＝假绿通道
        for stmt in block:
            for n in ast.walk(stmt):
                yield n

    init_lines, publish_lines, close_lines, double_checks = [], [], [], []
    for n in walk_block(body):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_init":
            init_lines.append(n.lineno)
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "_conn":
                    publish_lines.append(n.lineno)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "close":
            close_lines.append(n.lineno)
        if isinstance(n, ast.If) and any(
                isinstance(c, ast.Compare) and isinstance(c.left, ast.Name)
                and c.left.id == "_conn" for c in ast.walk(n.test)):
            double_checks.append(n.lineno)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "get_conn":
            die("AST", "锁内调 get_conn（:%d）＝非重入 Lock 上自死锁" % n.lineno)
    if not init_lines or not publish_lines:
        die("AST", "锁内缺 _init(%r)／发布(%r)" % (init_lines, publish_lines))
    if len(publish_lines) != 1:
        die("AST", "锁内 `_conn =` 赋值现读 %d 处 %r，期望 1" % (len(publish_lines), publish_lines))
    if init_lines[0] > publish_lines[0]:
        die("AST", "_init(:%d) 仍晚于发布(:%d)＝半初始化连接照样对外可见"
            % (init_lines[0], publish_lines[0]))
    if not close_lines:
        die("AST", "锁内⛔ 找到 `c.close()`＝_init 抛错会漏句柄")
    if len(double_checks) != 1:
        die("AST", "锁内双检现读 %d 处 %r，期望 1" % (len(double_checks), double_checks))
    fast_path = [n.lineno for n in fn.body
                 if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                 and isinstance(n.test.left, ast.Name) and n.test.left.id == "_conn"]
    if len(fast_path) != 1 or fast_path[0] > lock_body[0].lineno:
        die("AST", "无锁快路径不在位（现读 %r，with 在 :%d）"
            % (fast_path, lock_body[0].lineno))

    # 5) 落盘＋回读反证
    TARGET.write_bytes(new_text.encode("utf-8"))
    back = TARGET.read_bytes()
    if back.count(b"\r\n") != 0:
        die("WRITE_VERIFY", "写回后 CRLF=%d" % back.count(b"\r\n"))
    if md5(back) == BASE_MD5:
        die("WRITE_VERIFY", "写回 md5 与基线同号＝压根没写")
    if not back.endswith(b"\n"):
        die("WRITE_VERIFY", "写回后末行无换行")
    if back.decode("utf-8").count(MARKER) != 1:
        die("WRITE_VERIFY", "标记出现 %d 次，期望 1" % back.decode("utf-8").count(MARKER))
    want_lines = BASE_LINES - len(OLD) + len(NEW)
    got_lines = back.decode("utf-8").count("\n")
    if got_lines != want_lines:
        die("WRITE_VERIFY", "写回 %d 行，期望 %d" % (got_lines, want_lines))
    ast.parse(back.decode("utf-8"), str(TARGET))

    print("PATCH_OK|file=%s md5=%s lines=%d bytes=%d crlf=0 old_lines=%d new_lines=%d"
          % (TARGET, md5(back), got_lines, len(back), len(OLD), len(NEW)))
    print("ANCHOR|%s:%d-%d replaced" % (TARGET, START_LINE, START_LINE + len(OLD) - 1))
    print("ORDER|_init@%d < publish@%d | close@%s | double_check@%s | fast_path@%s"
          % (init_lines[0], publish_lines[0], close_lines, double_checks, fast_path))
    print("GUARD|lock_body_calls_get_conn=0 | with_lock_blocks=%d" % len(lock_body))


if __name__ == "__main__":
    main()
