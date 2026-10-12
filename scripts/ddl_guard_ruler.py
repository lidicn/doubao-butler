"""格2 纪律闸验收尺（2026-09-30，不依赖 pytest）。

四腿：
  A 行为用例——add_column_if_absent 在 /tmp 自建库上跑 6 格，含今天的现网形态（库被锁）。
  B 反证——同一格「库被锁」用旧写法（try/except OperationalError: pass）必须静默过关，
    否则说明我这把尺咬不到真缺陷。
  C 静态——AST 走三张被改文件，纯 pass 的 OperationalError handler 必须为 0；
    --repo-swallows 另呈全仓分母（只呈报，不当判据）。
  D 现网 schema——--db 指向现网库时，先整份拷到 /tmp（含 -wal/-shm）再读 PRAGMA，
    绝不打开现网路径本身、绝不做任何写。
"""
from __future__ import annotations

import argparse
import ast
import os
import shutil
import sqlite3
import sys
import tempfile

# 容器里跑时脚本在 /tmp，仓库根从环境变量给；盘上 scripts/ 跑时按 __file__ 推。
ROOT = os.environ.get("BUTLER_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from butler.store.ddl import add_column_if_absent  # noqa: E402

PATCHED = ["butler/store/db.py", "butler/core/decision_store.py", "butler/proactive/engine.py"]
MIGRATED = [("trigger_runs", "trace_id"), ("skill_runs", "trace_id"),
            ("decisions", "callback_url"), ("decisions", "channel"),
            ("proactive_inquiries", "role_id"), ("proactive_inquiries", "feedback")]

fails = []
results = {}


def case(name, fn):
    results[name] = 0
    try:
        fn()
    except AssertionError as exc:
        fails.append("%s: %s" % (name, exc))
    except Exception as exc:  # noqa: BLE001
        fails.append("%s: unexpected %s: %s" % (name, type(exc).__name__, exc))
    else:
        results[name] = 1


def tmpdb():
    fd, path = tempfile.mkstemp(prefix="g2ruler_", suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA busy_timeout=200")
    conn.execute("CREATE TABLE t (a TEXT)")
    conn.execute("INSERT INTO t VALUES ('seed')")
    conn.commit()
    return path, conn


def cleanup(*paths):
    for p in paths:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(p + suffix)
            except OSError:
                pass


def c1_new_column():
    path, conn = tmpdb()
    try:
        assert add_column_if_absent(conn, "t", "b", "TEXT") is True, "should report True"
        conn.commit()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(t)")]
        assert "b" in cols, "column missing after add: %s" % cols
    finally:
        conn.close()
        cleanup(path)


def c2_existing_is_false_no_raise():
    path, conn = tmpdb()
    try:
        add_column_if_absent(conn, "t", "b", "TEXT")
        conn.commit()
        assert add_column_if_absent(conn, "t", "b", "TEXT") is False, "second add should be False"
        assert add_column_if_absent(conn, "t", "b", "INTEGER") is False, \
            "same name, different ddl is still the idempotent case"
    finally:
        conn.close()
        cleanup(path)


def c3_missing_table_raises():
    path, conn = tmpdb()
    try:
        try:
            add_column_if_absent(conn, "no_such_table", "b", "TEXT")
        except sqlite3.OperationalError as exc:
            assert "no such table" in str(exc), "wrong message: %s" % exc
            return
        raise AssertionError("missing table did not raise")
    finally:
        conn.close()
        cleanup(path)


def c4_locked_db_raises():
    """今天的现网形态：库被别的连接占住写锁。必须 raise，不许读成「列已存在」。"""
    path, holder = tmpdb()
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO t VALUES ('held, never committed')")
        victim = sqlite3.connect(path)
        victim.execute("PRAGMA busy_timeout=200")
        try:
            try:
                add_column_if_absent(victim, "t", "b", "TEXT")
            except sqlite3.OperationalError as exc:
                assert "locked" in str(exc), "wrong message: %s" % exc
                return
            raise AssertionError("locked database did not raise (silently accepted)")
        finally:
            victim.close()
    finally:
        holder.rollback()
        holder.close()
        cleanup(path)


def c4b_old_style_silently_passes():
    """反证 B：旧写法在同一情境下必须静默过关 —— 证明这把尺咬的是真缺陷。"""
    path, holder = tmpdb()
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO t VALUES ('held, never committed')")
        victim = sqlite3.connect(path)
        victim.execute("PRAGMA busy_timeout=200")
        try:
            try:
                victim.execute("ALTER TABLE t ADD COLUMN b TEXT")
            except sqlite3.OperationalError:
                pass  # 旧写法原样
            cols = [r[1] for r in victim.execute("PRAGMA table_info(t)")]
            assert "b" not in cols, "lock did not actually block the ALTER: %s" % cols
            return
        finally:
            victim.close()
    finally:
        holder.rollback()
        holder.close()
        cleanup(path)


def c5_readonly_raises():
    path, conn = tmpdb()
    conn.close()
    ro = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        try:
            add_column_if_absent(ro, "t", "b", "TEXT")
        except sqlite3.OperationalError as exc:
            assert "readonly" in str(exc), "wrong message: %s" % exc
            return
        raise AssertionError("readonly database did not raise")
    finally:
        ro.close()
        cleanup(path)


def c6_genuine_ddl_error_raises():
    """非幂等、非锁、非只读的真 DDL 失败（NOT NULL 无默认值）也必须 raise。"""
    path, conn = tmpdb()
    try:
        try:
            add_column_if_absent(conn, "t", "b", "INTEGER NOT NULL")
        except sqlite3.OperationalError as exc:
            assert "duplicate column" not in str(exc), "that would be the idempotent path"
            return
        raise AssertionError("non-idempotent DDL error did not raise")
    finally:
        conn.close()
        cleanup(path)


def read_source(path):
    """utf-8-sig：仓里有带 BOM 的 .py，用 utf-8 读会把 BOM 当第一个字符喂给 compile。"""
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def swallow_handlers(path):
    out = []
    for node in ast.walk(ast.parse(read_source(path))):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue
        name = getattr(node.type, "attr", None) or getattr(node.type, "id", None)
        if name != "OperationalError":
            continue
        if all(isinstance(st, ast.Pass) for st in node.body):
            out.append(node.lineno)
    return out


def c_static(rel):
    lines = swallow_handlers(os.path.join(ROOT, rel))
    print("STATIC %s pure_pass_operationalerror=%d" % (rel, len(lines)))
    assert not lines, "%s still has pure-pass OperationalError at %s" % (rel, lines)


def prod_schema(db):
    d = tempfile.mkdtemp(prefix="g2prod_")
    dest = os.path.join(d, "copy.db")
    try:
        shutil.copy2(db, dest)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(db + suffix):
                shutil.copy2(db + suffix, dest + suffix)
        conn = sqlite3.connect(dest)
        conn.execute("PRAGMA busy_timeout=1000")
        try:
            missing = []
            seen = set()
            for table, col in MIGRATED:
                seen.add(table)
                cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]
                if not cols:
                    missing.append("%s.table_absent" % table)
                elif col not in cols:
                    missing.append("%s.%s" % (table, col))
            print("PROD_SCHEMA_SOURCE=copy-not-live")
            print("PROD_SCHEMA_TABLES=%d" % len(seen))
            print("PROD_SCHEMA_MISSING=%d %s" % (len(missing), ",".join(missing) or "-"))
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print("PROD_SCHEMA=error %s: %s" % (type(exc).__name__, exc))
    finally:
        for name in os.listdir(d):
            os.remove(os.path.join(d, name))
        os.rmdir(d)
        print("PROD_SCHEMA_TMPDIR_REMOVED=%s" % (not os.path.exists(d)))


ap = argparse.ArgumentParser()
ap.add_argument("--db", default="", help="prod sqlite path; copied to /tmp, never opened in place")
ap.add_argument("--repo-swallows", action="store_true")
args = ap.parse_args()

for name in ["c1_new_column", "c2_existing_is_false_no_raise", "c3_missing_table_raises",
             "c4_locked_db_raises", "c4b_old_style_silently_passes", "c5_readonly_raises",
             "c6_genuine_ddl_error_raises"]:
    case(name, globals()[name])

for rel in PATCHED:
    case("static_" + rel.replace("/", ".").replace(".py", ""), lambda rel=rel: c_static(rel))

if args.db:
    prod_schema(args.db)

if args.repo_swallows:
    total = 0
    py_total = 0
    hits = []
    unparseable = []
    boms = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "butler")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            py_total += 1
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT).replace(os.sep, "/")
            raw = open(os.path.join(dirpath, fn), "rb").read()
            if raw.startswith(b"\xef\xbb\xbf"):
                boms.append(rel)
            try:
                n = len(swallow_handlers(os.path.join(dirpath, fn)))
            except SyntaxError as exc:
                unparseable.append("%s(%s)" % (rel, exc.msg))
                continue
            if n:
                hits.append("%s:%d" % (rel, n))
            total += n
    print("REPO_PY_SCANNED=%d" % py_total)
    print("REPO_BOM_FILES=%d %s" % (len(boms), " ".join(boms) or "-"))
    print("REPO_UNPARSEABLE=%d %s" % (len(unparseable), " ".join(unparseable) or "-"))
    print("REPO_PURE_PASS_SWALLOWS=%d files=%s" % (total, " ".join(hits) or "-"))

print("CASES_RUN=%d" % len(results))
print("CASES_PASS=%d" % sum(results.values()))
print("FAIL=%d" % len(fails))
for f in fails:
    print("  FAIL %s" % f)
print("VERDICT=%s" % ("GREEN" if not fails else "RED"))
sys.exit(1 if fails else 0)
