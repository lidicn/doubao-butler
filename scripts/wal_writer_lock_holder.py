#!/usr/bin/env python3
"""Name the process that holds the SQLite WAL write lock (P0 2026-09-30).

A stalled write face and a frozen WAL look identical from max(ts) alone. This
ruler reads /proc/locks for the -shm inode and reports who owns byte 120, the
WAL write lock, so a stall is attributed instead of guessed.

Read-only: it never opens the database, only stats the file and reads /proc.
"""
from __future__ import annotations

import argparse
import os
import sys

# SQLite shm byte-range locks (wal.c): 120=write, 121=ckpt, 122=recover, 123..=read marks
WAL_WRITE_LOCK_OFFSET = 120


def parse_locks(lines, shm_ino):
    """Yield (offset, lock_type, pid) for rows on inode shm_ino.

    /proc/locks row: "93: POSIX ADVISORY WRITE 1894872 fd:01:11668733 120 120"
    The device+inode is one field, colon-separated, inode last.
    """
    out = []
    want = ":" + str(shm_ino)
    for line in lines:
        parts = line.split()
        if len(parts) < 7:
            continue
        if not parts[5].endswith(want):
            continue
        try:
            offset = int(parts[6])
        except ValueError:
            continue
        out.append((offset, parts[3], parts[4]))
    return out


def writer_holder(shm_path, locks_path="/proc/locks"):
    """Return (holder_pid, rows) for the WAL write lock on shm_path."""
    try:
        shm_ino = os.stat(shm_path).st_ino
    except OSError:
        return None, None
    try:
        with open(locks_path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None, None
    rows = parse_locks(lines, shm_ino)
    for offset, lock_type, pid in rows:
        if offset == WAL_WRITE_LOCK_OFFSET and lock_type == "WRITE":
            return pid, rows
    return None, rows


def self_test():
    """Drive parse_locks/writer_holder directly, with controls that must bite."""
    fixture = [
        "93: POSIX  ADVISORY  WRITE 1894872 fd:01:11668733 120 120",
        "94: POSIX  ADVISORY  READ  1894872 fd:01:11668733 123 123",
        "95: POSIX  ADVISORY  READ  1894872 fd:01:11668733 128 128",
        "96: POSIX  ADVISORY  READ  1894872 fd:01:11614839 1073741826 1073742335",
        "97: POSIX  ADVISORY  WRITE 9999999 fd:01:19999999 120 120",
        "1: POSIX  ADVISORY  READ 1896690 00:36:19926479 128 128",
    ]
    ours = 11668733
    cases = []

    rows = parse_locks(fixture, ours)
    cases.append(("rows_on_our_inode", len(rows) == 3, len(rows)))

    pid, _ = writer_holder_from_rows(fixture, ours)
    cases.append(("holder_is_1894872", pid == "1894872", pid))

    # negative control 1: a writer on a DIFFERENT inode must not be blamed
    pid_other, _ = writer_holder_from_rows(fixture, 19999999)
    cases.append(("other_inode_not_blamed", pid_other == "9999999", pid_other))

    # negative control 2: remove the 120/WRITE row -> no holder, not a wrong one
    no_writer = [ln for ln in fixture if not (ln.split()[3] == "WRITE" and ln.split()[6] == "120")]
    pid_none, _ = writer_holder_from_rows(no_writer, ours)
    cases.append(("absent_writer_lock_is_none", pid_none is None, pid_none))

    # positive control: a READ at 120 is not a writer
    flipped = ["93: POSIX  ADVISORY  READ 1894872 fd:01:11668733 120 120"] + fixture[1:]
    pid_read, _ = writer_holder_from_rows(flipped, ours)
    cases.append(("read_at_120_is_not_writer", pid_read is None, pid_read))

    failed = [name for name, ok, got in cases if not ok]
    for name, ok, got in cases:
        print("SELFTEST %-26s %s (got=%s)" % (name, "PASS" if ok else "FAIL", got))
    print("SELFTEST_CASES=%d FAIL=%d" % (len(cases), len(failed)))
    return 1 if failed else 0


def writer_holder_from_rows(lines, shm_ino):
    rows = parse_locks(lines, shm_ino)
    for offset, lock_type, pid in rows:
        if offset == WAL_WRITE_LOCK_OFFSET and lock_type == "WRITE":
            return pid, rows
    return None, rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/butler.db")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return self_test()

    shm = args.db + "-shm"
    pid, rows = writer_holder(shm)
    print("DB=%s SHM=%s" % (args.db, shm))
    if rows is None:
        print("VERDICT=UNKNOWN  shm 或 /proc/locks 取不到（不代表没锁）")
        return 2
    print("SHM_LOCK_ROWS=%d" % len(rows))
    for offset, lock_type, owner in rows:
        role = {120: "wal-writer", 121: "ckpt", 122: "recover"}.get(offset)
        if role is None and 123 <= offset <= 127:
            role = "read-mark-%d" % (offset - 123)
        print("  offset=%d %s pid=%s %s" % (offset, lock_type, owner, role or ""))
    if pid is None:
        print("WAL_WRITER_PID=none")
        print("VERDICT=PASS  没有进程持有 WAL 写锁")
        return 0
    try:
        comm = open("/proc/%s/comm" % pid, encoding="utf-8").read().strip()
    except OSError:
        comm = "unknown"
    print("WAL_WRITER_PID=%s COMM=%s" % (pid, comm))
    print("VERDICT=RED  WAL 写锁被 %s(%s) 持有 ⇒ 其它连接的写入会一直 database is locked" % (comm, pid))
    return 1


if __name__ == "__main__":
    sys.exit(main())
