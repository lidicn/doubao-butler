#!/usr/bin/env bash
# 批18 台账文书腿的证据汇总：把 workorders/readings/1003b18/ 里每份读数的**关键行**原样打出来，
# 供 §19 逐格引用（⛔ 我手填数：每个数都要能在下面某行找到出处）。
# 为什么落成脚本：ssh 双引号里的 `$f` 会被上层替换规则吃掉（2026-10-02 19:45Z 实测：
# 循环变量变成 `[/extract_itex]f[/extract_itex]`，grep 全部 No such file ⇒ "取不到" 被登记成 "0 命中"）。
# 用法：bash scripts/audit_1002/summarize_b18_readings_1002.sh
set -u
cd "$(dirname "$0")/../.." || exit 9
DIR=workorders/readings/1003b18
OUT="$DIR/ledger_evidence_summary_run1.txt"
{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "dir=$DIR"
  ls -1 "$DIR" | sed 's/^/entry|/'
  echo "listing_lines=$(ls -1 "$DIR" | wc -l)"
  for f in "$DIR"/*.txt; do
    # 本件自己就在 DIR 里：⛔ 一边写一边 grep（输出文件即输入文件 → 读数失真）
    if [ "$f" = "$OUT" ]; then echo; echo "===== SKIP_SELF|$f"; continue; fi
    echo
    echo "===== $f  ($(wc -c <"$f") B, $(wc -l <"$f") lines, md5 $(md5sum "$f" | cut -d' ' -f1))"
    grep -nE 'STAMP|stamp_utc|^Ran |^OK$|^FAILED|_rc=|PLAN\||WRITTEN\||BASELINE|ALREADY_APPLIED|DRY|md5=|MD5|real=|bitten|survived|misbite|restore|REDLIST|TMP_|CONFIRM|ANCHOR|census_rc|BASE_[0-9]|\[A\]|\[B\]|leg1|leg2|rc=' "$f" | head -22
  done
} >"$OUT" 2>&1
rc=$?
echo "summarize_rc=$rc out=$OUT ($(wc -c <"$OUT") B)"
cat "$OUT"
exit $rc
