#!/usr/bin/env python3
"""批97 O-0b：回声窗探针（scripts/echo_window_probe.py）。

验收句（路线图 v2.5）要求证明「闸门上线后连续 24h 内 SELF_PUSH_PREFIX 不再落库」。
本探针⛔ 碰活库：把 `butler.db` 连同 WAL 侧文件（-wal/-shm）一起拷成临时只读物，
读完后当场删除，不留件；若发现已有并发探针副本则直接拒跑。

判据形状（尺子本身不许产出假绿）：
- ts 列是 REAL：按天分组必须先 CAST 成整数天号（逐行 ts/86400 会把同一天打成多行）。
- 活体门：dialog_turns 最新行年龄超过 --liveness-max-min ⇒ 表已停摆 ⇒ 窗内 0 命中
  不可当证据 ⇒ VERDICT=INCONCLUSIVE（rc 4），⛔ 报 PASS。
- 三态：窗内仍有回声 ⇒ FAIL(1)；活体门过但证据未齐（未满 24h 或全表有历史命中）⇒
  PASS-PARTIAL(0)；满窗零回声 ⇒ PASS(0)。
- 只列前 40 个命中日；超过时在文书里点名「截断：共 N 天有命中」。
- 窗不满 24h 时打印距满窗还差多少小时的诚实度注记。
- SELF_PUSH_PREFIX 的字面值⛔ 落文书（只报 PREFIX_LEN）；前缀从源文件读，
  读不到字面量 ⇒ 死尺自报，退出码 3，⛔ 拿记忆里的串当默认值。

跑法：python3 -B scripts/echo_window_probe.py --since 2026-10-04T00:00:00Z
"""
from __future__ import annotations

import argparse
import calendar
import os
import re
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# 源文件位置（测试／变异台可整档替换这三个模块级路径）
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
DB = os.path.join(DATA_DIR, "butler.db")            # 活库（只被复制，⛔ 被读写）
CP = os.path.join(DATA_DIR, "echo_window_probe.db")  # 临时只读物副本
SRC_APP = os.path.join(REPO, "butler", "notifier", "app.py")

# 被巡检的表（dialog_turns 是回声落库处，其余为活体参照）
PROBE_TABLES = (
    "dialog_turns",
    "agent_traces",
    "notify_history",
    "home_events",
    "decisions",
    "chat_logs",
    "trigger_runs",
)

DAY_LIST_MAX = 40       # 命中日最多列 40 行，超出必须在文书里点名
FULL_WINDOW_S = 86400   # 验收句要的满窗：连续 24h

_PREFIX_RE = re.compile(r"^[ \t]*SELF_PUSH_PREFIX[ \t]*=[ \t]*([\"'])(.*?)\1", re.M)
_SIDECARS = ("", "-wal", "-shm")


def iso_z(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def day_label(day_no: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day_no * 86400))


def read_prefix() -> str:
    """从源文件读 SELF_PUSH_PREFIX 字面量；读不到 ⇒ 死尺自报，退出码 3。"""
    try:
        with open(SRC_APP, "rb") as f:
            raw = f.read()
    except OSError as e:
        sys.stderr.write("探针死尺：%s 读不出来：%s\n" % (SRC_APP, e))
        raise SystemExit(3)
    m = _PREFIX_RE.search(raw.decode("utf-8-sig"))  # utf-8-sig：吃掉 BOM
    if not m:
        sys.stderr.write("读不到 SELF_PUSH_PREFIX 字面量（%s）——⛔ 拿记忆里的串当"
                         "默认值，拒判退出\n" % SRC_APP)
        raise SystemExit(3)
    value = m.group(2)
    if "\n" in value or not value:
        sys.stderr.write("读到的 SELF_PUSH_PREFIX 不合法（空或跨行）——死尺自报\n")
        raise SystemExit(3)
    return value


def _scan_table(conn, table, now, liveness_s):
    """返回 None＝该表缺席或没有 ts 列；否则 dict(rows/bad/max_ts/fresh)。"""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
    except sqlite3.Error:
        return None
    if "ts" not in cols:
        return None
    try:
        vals = [r[0] for r in conn.execute("SELECT ts FROM %s" % table)]
    except sqlite3.Error:
        return None
    numeric = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    max_ts = max(numeric) if numeric else None
    return {
        "rows": len(vals),
        "bad": len(vals) - len(numeric),
        "max_ts": max_ts,
        "fresh": max_ts is not None and (now - max_ts) <= liveness_s,
    }


def run(conn, prefix, since, now, args) -> int:
    """打印判决文书并返回 rc。三态：PASS(0)/FAIL(1)/INCONCLUSIVE(4)。"""
    liveness_s = float(args.liveness_max_min) * 60.0

    print("ECHO-WINDOW-PROBE since=%s now=%s window=%.1f h 活体门<=%.0f min"
          % (iso_z(since), iso_z(now), (now - since) / 3600.0, args.liveness_max_min))
    print("PREFIX_LEN=%d（值不落文书）" % len(prefix))

    # ---- 逐表活体扫描 ----
    fresh_count = 0
    dt_info = None
    for table in PROBE_TABLES:
        info = _scan_table(conn, table, now, liveness_s)
        if info is None:
            print("  %-16s skip(no ts)" % table)
            continue
        if info["fresh"]:
            fresh_count += 1
        if table == "dialog_turns":
            dt_info = info
        if info["rows"] == 0:
            print("  %-16s rows=0   空表" % table)
        elif info["max_ts"] is None:
            print("  %-16s rows=%d   ts 非 epoch（%d 行全读不出）"
                  % (table, info["rows"], info["bad"]))
        else:
            extra = "   ts 非 epoch %d 行" % info["bad"] if info["bad"] else ""
            print("  %-16s rows=%d   latest=%s   %s%s"
                  % (table, info["rows"], iso_z(info["max_ts"]),
                     "活" if info["fresh"] else "停", extra))
    print("FRESH_TABLES=%d/%d" % (fresh_count, len(PROBE_TABLES)))

    # ---- 命中扫描（⛔ 打印任何命中正文）----
    hits_total = in_win = out_win = bad_hits = 0
    hit_days: dict[int, int] = {}
    first_ts = last_ts = None
    try:
        cur = conn.execute("SELECT ts, text FROM dialog_turns WHERE substr(text,1,?) = ?",
                           (len(prefix), prefix))
        for ts, _text in cur:
            hits_total += 1
            if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                bad_hits += 1
                continue
            if since <= ts <= now:
                in_win += 1
            else:
                out_win += 1
            day_no = int(ts // 86400)  # ts 是 REAL：先落成整数天号再分组
            hit_days[day_no] = hit_days.get(day_no, 0) + 1
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
    except sqlite3.Error:
        pass

    summary = "TOTAL=%d 闸门后=%d 闸门前=%d" % (hits_total, in_win, out_win)
    if bad_hits:
        summary += "  ts 非 epoch=%d" % bad_hits
    print(summary)

    if hit_days:
        days = sorted(hit_days.items())
        if len(days) > DAY_LIST_MAX:
            print("(截断：共 %d 天有命中)" % len(days))
            days = days[:DAY_LIST_MAX]
        for day_no, n in days:
            print("  %s  %d" % (day_label(day_no), n))

    if hits_total:
        if bad_hits:
            print("命中跨度 ts 非 epoch：%d 行读不出，跨度不计" % bad_hits)
        else:
            print("命中跨度 first=%s last=%s" % (iso_z(first_ts), iso_z(last_ts)))

    # ---- 满窗诚实度注记 ----
    window_s = now - since
    if window_s < FULL_WINDOW_S:
        remaining_h = (FULL_WINDOW_S - window_s) / 3600.0
        print("NOTE 验收句要的是连续 24h：本窗 %.1f h，距满窗还差 %.1f h"
              % (window_s / 3600.0, remaining_h))

    # ---- 三态判决 ----
    live = dt_info is not None and dt_info["max_ts"] is not None and dt_info["fresh"]
    if in_win > 0:
        print("VERDICT=FAIL 窗内仍有 %d 条回声落库" % in_win)
        return 1
    if not live:
        print("VERDICT=INCONCLUSIVE-dialog_turns 无活体证据，0 命中不可当证据"
              "（latest=%s）" % (iso_z(dt_info["max_ts"]) if dt_info and dt_info["max_ts"] is not None else "读不出"))
        return 4
    if hits_total == 0 and window_s >= FULL_WINDOW_S:
        print("VERDICT=PASS 满窗零回声")
        return 0
    print("VERDICT=PASS-PARTIAL 活体门过，窗内 0 命中")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="回声窗探针（只读物副本，⛔ 碰活库）")
    ap.add_argument("--since", required=True,
                    help="窗起点，UTC ISO（如 2026-10-04T00:00:00Z）")
    ap.add_argument("--liveness-max-min", type=int, default=60,
                    help="活体门：dialog_turns 最新行超过该分钟数则拒判")
    args = ap.parse_args()

    try:
        since = calendar.timegm(time.strptime(args.since, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        sys.stderr.write("--since 解析失败：%r（要 UTC ISO：%%Y-%%m-%%dT%%H:%%M:%%SZ）\n"
                         % args.since)
        return 3

    prefix = read_prefix()

    if os.path.exists(CP):
        sys.stderr.write("检测到并发探针：%s 已存在——同一时刻只许一枚探针，拒跑\n" % CP)
        return 3

    copied = []
    for suffix in _SIDECARS:
        src = DB + suffix
        if os.path.exists(src):
            shutil.copy2(src, CP + suffix)
            copied.append(suffix)
    print("COPY_PAIRS=%d" % len(copied))

    if "" not in copied:
        sys.stderr.write("找不到源库 %s，无法开检\n" % DB)
        rc = 3
    else:
        conn = sqlite3.connect(CP)
        try:
            rc = run(conn, prefix, since, time.time(), args)
        finally:
            conn.close()

    # 只读物当场删除，⛔ 留件
    for suffix in _SIDECARS:
        try:
            os.remove(CP + suffix)
        except FileNotFoundError:
            pass
        except OSError as e:
            sys.stderr.write("清理副本失败 %s：%s\n" % (CP + suffix, e))
    left = sum(1 for suffix in _SIDECARS if os.path.exists(CP + suffix))
    print("COPY_LEFT=%d" % left)
    return rc


if __name__ == "__main__":
    sys.exit(main())
