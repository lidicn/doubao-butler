#!/usr/bin/env bash
# 批18 台账文书腿：把 skipped 9→3 那 6 枚的**名字**找出来（⛔ 用「大概是 env」糊过去）。
# 用法：bash scripts/audit_1002/diff_skipped_b18_1002.sh
set -u
cd "$(dirname "$0")/../.." || exit 9
OLD=workorders/readings/1003b17/discover_full.txt
NEW=workorders/readings/1003b18/run2_discover_full.txt
OUT=workorders/readings/1003b18/skipped_diff_run1.txt
{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "OLD=$OLD ($(wc -c <"$OLD") B)"
  echo "NEW=$NEW ($(wc -c <"$NEW") B)"
  for tag in OLD NEW; do
    f=$(eval echo \$$tag)
    echo "--- $tag: 含 skip 的行（原样，行号来自 grep -n）---"
    grep -niE 'skip' "$f" | sed 's/^/  /'
    echo "$tag skip_lines=$(grep -ciE 'skip' "$f")"
  done
  echo "--- 差集（只在 OLD 出现 / 只在 NEW 出现）---"
  TMPO=/tmp/skip_old_$$.txt; TMPN=/tmp/skip_new_$$.txt
  grep -oiE "test_[a-z0-9_]+" <(grep -iE 'skip' "$OLD") | sort -u >"$TMPO"
  grep -oiE "test_[a-z0-9_]+" <(grep -iE 'skip' "$NEW") | sort -u >"$TMPN"
  echo "only_in_OLD:"; comm -23 "$TMPO" "$TMPN" | sed 's/^/  /'
  echo "only_in_NEW:"; comm -13 "$TMPO" "$TMPN" | sed 's/^/  /'
  rm -f "$TMPO" "$TMPN"
  echo "TMP_CLEAN=$([ -e "$TMPO" ] && echo RESIDUE || echo REMOVED)"
} >"$OUT" 2>&1
rc=$?
echo "diff_skipped_rc=$rc out=$OUT ($(wc -c <"$OUT") B)"
cat "$OUT"
exit $rc
