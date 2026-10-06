#!/usr/bin/env bash
# 比对两次收口读数的红名单：新增红 / 消失红 各自点名（不只比 md5）。
# 用法：bash scripts/audit_1002/diff_redlist_1002.sh <旧读数> <新读数>
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OLD=${1:?旧读数件}
NEW=${2:?新读数件}
TS=/tmp/redlist_diff_$$
mkdir -p "$TS"
awk '/^  (FAIL|ERROR):/{print}' "$OLD" | sort >"$TS/old"
awk '/^  (FAIL|ERROR):/{print}' "$NEW" | sort >"$TS/new"
date -u '+stamp_utc=%Y-%m-%dT%H:%M:%SZ'
echo "cmd: bash scripts/audit_1002/diff_redlist_1002.sh $OLD $NEW"
echo "old_lines=$(wc -l <"$TS/old") new_lines=$(wc -l <"$TS/new")"
echo "old_md5=$(md5sum <"$TS/old") "
echo "new_md5=$(md5sum <"$TS/new") "
echo "--- 只在新表（新增红）---"
comm -13 "$TS/old" "$TS/new"
echo "added=$(comm -13 "$TS/old" "$TS/new" | grep -c .)"
echo "--- 只在旧表（消失的红）---"
comm -23 "$TS/old" "$TS/new"
echo "removed=$(comm -23 "$TS/old" "$TS/new" | grep -c .)"
rm -rf "$TS"
if [ -e "$TS" ]; then echo "TMP_RESIDUE|$TS"; else echo "TMP_REMOVED|$TS"; fi
