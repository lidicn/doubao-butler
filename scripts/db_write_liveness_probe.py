#!/usr/bin/env python3
"""写面存活探针：DB 是否**还在收写**。P0（2026-09-30）缺的就是这一格读数。

为什么需要它：`/api/health` 200、容器 `healthy`、`RestartCount=0`、日志窗无 Traceback——
这四格在"SQLite 写面停摆"的全过程中**全程绿灯**。日志里在喊 `database is locked`，
但没有任何一格读数叫"最后一次成功写入是几时"。本探针补上这格，并挂进
`scripts/pre_deploy_check.sh` 作第 4 条腿：⛔ 在写面停摆时部署新版本（部署会把主卧 24h 窗
的起算点再次推后，而新写入照样进不去）。

口径：
- 只读连接（`mode=ro`），⛔ 写现网库、⛔ checkpoint、⛔ 有副作用的 PRAGMA。
- **全部**被探表都停才判 RED，⛔ 单表停就判红（`wakeup_log` 之类低频表单表判据＝假红通道）。
- 自检（`--self-test`）走的是 `probe()` 本体的同一条码路（URI 只读打开 + sqlite_master + 判据），
  ⛔ 另开一条算术复制品来自证——那样 `probe()` 的打开腿坏了自检照样判绿。
  夹具库写在临时目录里，⛔ 碰现网 `data/butler.db`。

退出码：0 PASS / 1 RED / 2 无法判定（库读不到、表缺、表空＝⛔ 当成 PASS）。
"""

import argparse
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# 这些表在正常时段都应有近期写入；全停才算停摆
DEFAULT_TABLES = ("presence_history", "ts_device_anomalies", "trigger_runs", "wakeup_log")
DEFAULT_MAX_AGE_S = 900  # 15 分钟


def probe(db_path, tables, max_age_s, now):
    uri = "file:%s?mode=ro" % db_path
    try:
        c = sqlite3.connect(uri, uri=True, timeout=5.0)
    except Exception as exc:
        print("VERDICT=UNREADABLE db=%s err=%s" % (db_path, type(exc).__name__))
        return 2
    ages = {}
    try:
        have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in tables:
            if t not in have:
                print("MISSING_TABLE %s" % t)
                return 2
            m = list(c.execute("SELECT max(ts) FROM %s" % t))[0][0]
            if m is None:
                print("EMPTY_TABLE %s" % t)
                return 2
            ages[t] = now - int(float(m))
    except Exception as exc:
        print("VERDICT=UNREADABLE err=%s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 2
    finally:
        c.close()

    print("DB_PATH=%s NOW_EPOCH=%d" % (db_path, now))
    for t in tables:
        print("TABLE %-22s max_ts_age_s=%d" % (t, ages[t]))
    freshest = min(ages.values())
    worst = max(ages.values())
    print("FRESHEST_AGE_S=%d WORST_AGE_S=%d THRESHOLD_S=%d TABLES=%d" % (
        freshest, worst, max_age_s, len(tables)))
    # AND 闸：连**最新鲜**那张表都超过阈值才 RED。
    # 用 max() 会把低频表（实测 wakeup_log 比 presence_history 老 8,692s）变成永久假红。
    if freshest > max_age_s:
        print("VERDICT=RED  全部 %d 张表都超过 %ds 没有新写入 ⇒ 写面停摆" % (len(tables), max_age_s))
        return 1
    print("VERDICT=PASS  (至少一张表在 %ds 内收过写)" % max_age_s)
    return 0


def _fixture(path, tables, ts_by_table):
    c = sqlite3.connect(str(path))
    try:
        for t in tables:
            c.execute("CREATE TABLE %s (ts REAL)" % t)
            if ts_by_table is not None:
                c.execute("INSERT INTO %s (ts) VALUES (?)" % t, (ts_by_table,))
        c.commit()
    finally:
        c.close()


def self_test():
    """四例都跑 `probe()` 本体：新鲜 PASS / 陈旧 RED / 缺表 2 / 空表 2。"""
    now = int(time.time())
    cases = (
        ("fresh", {t: now - 5 for t in DEFAULT_TABLES}, 0),
        ("stale", {t: now - 7200 for t in DEFAULT_TABLES}, 1),
        ("mixed", dict({DEFAULT_TABLES[0]: now - 5},
                       **{t: now - 7200 for t in DEFAULT_TABLES[1:]}), 0),
    )
    rc = 0
    with tempfile.TemporaryDirectory(prefix="dbwlp_selftest_") as td:
        for label, ts_by_table, expect in cases:
            p = os.path.join(td, "%s.db" % label)
            for t, ts in ts_by_table.items():
                _fixture(p, [t], ts)
            got = probe(p, DEFAULT_TABLES, DEFAULT_MAX_AGE_S, now)
            mark = "ok" if got == expect else "WRONG"
            print("SELFTEST_%s expect=%s got=%s %s" % (label, expect, got, mark))
            if got != expect:
                rc = 1
        # 缺表 / 空表：判据必须是"无法判定"(2)，⛔ 悄悄算成 PASS
        p = os.path.join(td, "missing.db")
        _fixture(p, list(DEFAULT_TABLES[:-1]), now - 5)
        got = probe(p, DEFAULT_TABLES, DEFAULT_MAX_AGE_S, now)
        print("SELFTEST_missing_table expect=2 got=%s %s" % (got, "ok" if got == 2 else "WRONG"))
        rc = rc if got == 2 else 1
        p = os.path.join(td, "empty.db")
        _fixture(p, list(DEFAULT_TABLES), None)
        got = probe(p, DEFAULT_TABLES, DEFAULT_MAX_AGE_S, now)
        print("SELFTEST_empty_table expect=2 got=%s %s" % (got, "ok" if got == 2 else "WRONG"))
        rc = rc if got == 2 else 1
        leftovers = sorted(os.listdir(td))
        print("SELFTEST_CASES=%d FIXTURE_LEFTOVERS=%d" % (len(cases) + 2, len(leftovers)))
    return rc


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/butler.db")
    ap.add_argument("--tables", default=",".join(DEFAULT_TABLES))
    ap.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_S)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    p = str(Path(a.db).resolve())
    if not os.path.exists(p):
        print("DB_NOT_FOUND %s" % p)
        return 2
    return probe(p, tuple(x.strip() for x in a.tables.split(",") if x.strip()),
                 a.max_age, int(time.time()))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
