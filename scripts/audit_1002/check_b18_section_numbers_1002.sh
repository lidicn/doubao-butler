#!/usr/bin/env bash
# 批18 §19 文书出口前的自检：本节里每个「我抄来的数」必须能指到一条已跑命令的输出行。
# 用法：bash scripts/audit_1002/check_b18_section_numbers_1002.sh
set -u
cd "$(dirname "$0")/../.." || exit 9
R=workorders/readings/1003b18
{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "--- [1] run1 收口读数的 stamp（§19-8 表第一行引它）---"
  grep -nE 'stamp_utc|^Ran |^FAILED|REDLIST' "$R/run1_regression_redlistchanged.txt" | head -6
  echo "--- [2] b17→b18 红名单差分原文（added/removed 两个数）---"
  cat "$R/run1_redlist_diff_b17_to_b18.txt"
  echo "--- [3] 验收件现读：行数／字节／CR／md5 ---"
  f=tests/test_audit_1002_batch18_silent_raise_paths.py
  wc -lc <"$f"
  python3 -c "import hashlib,sys;b=open('$f','rb').read();print('CR=%d md5=%s'%(b.count(b'\r'),hashlib.md5(b).hexdigest()))"
  echo "--- [4] 先红 1 里 ModuleNotFoundError 命中数（§19-3 那句「几条码腿红在 import」）---"
  echo "grep -c ModuleNotFoundError red_batch18_run1.txt = $(grep -c ModuleNotFoundError "$R/red_batch18_run1.txt")"
  grep -nE 'ModuleNotFoundError' "$R/red_batch18_run1.txt" | head -6
  echo "--- [5] census 里 8 个 MODULE_LEVEL_FILE 相加（§19-8 那句对账）---"
  awk -F'module_level=' '/^MODULE_LEVEL_FILE/{s+=$2; n++} END{print "files="n" sum="s}' "$R/census.txt"
  echo "--- [6] 新到的第 12 份来源报告在 E 盘的体积由 PM 侧另量；本侧只确认权威树⛔ 有 doc/审计报告 ---"
  ls -d doc/审计报告 2>&1 | sed 's/^/  /'
  echo "  git_head=\$(git rev-parse --short HEAD) => $(git rev-parse --short HEAD)"
} 2>&1
