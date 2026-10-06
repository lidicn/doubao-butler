#!/usr/bin/env bash
# 批17 收口读数：全量回归 ＋ 红名单身份 ＋ AST 三数 ＋ 被测七件回基线。
# 为什么落成脚本（⛔ 手敲内联）：批16 那次我把这些步骤写成 ssh 内联串，嵌套引号被 bash 吃掉、
# 整块没跑起来——「读数件不存在」＝没测，⛔ 读成 0 命中。
# 用法：bash scripts/audit_1002/run_b17_regression_1002.sh
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OUT=workorders/readings/1003b17
mkdir -p "$OUT"
DISC="$OUT/discover_full.txt"
CEN="$OUT/census.txt"
LOG="$OUT/regression_and_census.txt"

# 六件生产码的 GREEN 基线＝patch_b17_atomic_json_1002.py --apply 打出的 WRITTEN 行原样
DEV_WANT=15c2516f5a548240623da051fb64c5f7
RSTORE_WANT=9e9670b37cbe30b6c30c211805f4cd24
RSTATE_WANT=3a08a3bc372eeb3cec832dba35061968
FEEDER_WANT=dc3bd6cbb8265018817580a08e120d5d
FROUTE_WANT=2fb75829c02fe7021bbc04600e40d589
HELP_WANT=8c4744dbcf3b91e2702ff71073e4effa
# 批14/15/16 记进台账的红名单身份（pattern 见下面 RED_RE 原文）
REDLIST_WANT=9490b2e3085174ec213d6b34d0e0d01d
RED_RE='^(FAIL|ERROR): '

{
  echo "=== 批17 收口读数 ==="
  date -u '+stamp_utc=%Y-%m-%dT%H:%M:%SZ'
  echo "cmd: bash scripts/audit_1002/run_b17_regression_1002.sh"

  echo "--- 跑前：被测六件必须是 GREEN 基线（变异探针还原后的自证）；验收件 md5 现读不手填 ---"
  md5sum butler/devices.py butler/roles/store.py butler/roles/state.py \
         butler/memory/feeder.py butler/core/fast_routes.py butler/core/atomic_json.py \
         tests/test_audit_1002_batch17_atomic_json_writes.py
  for pair in "butler/devices.py=$DEV_WANT" \
              "butler/roles/store.py=$RSTORE_WANT" \
              "butler/roles/state.py=$RSTATE_WANT" \
              "butler/memory/feeder.py=$FEEDER_WANT" \
              "butler/core/fast_routes.py=$FROUTE_WANT" \
              "butler/core/atomic_json.py=$HELP_WANT"; do
    f=${pair%%=*}; want=${pair##*=}; got=$(md5sum "$f" | cut -d' ' -f1)
    if [ "$got" = "$want" ]; then echo "BASELINE_OK|$f|$got"; else echo "BASELINE_BAD|$f|got=$got|want=$want"; fi
  done
  TEST_FILE=tests/test_audit_1002_batch17_atomic_json_writes.py
  echo "TEST_MD5_LIVE|$TEST_FILE|$(md5sum "$TEST_FILE" | cut -d' ' -f1)"

  echo
  echo "--- 全量回归 ---"
  echo "cmd: python3 -B -m unittest discover -s tests -t ."
  start=$(date +%s)
  python3 -B -m unittest discover -s tests -t . >"$DISC" 2>&1
  disc_rc=$?
  echo "discover_rc=$disc_rc wall=$(( $(date +%s) - start ))s"
  grep -E '^Ran |^OK|^FAILED' "$DISC"
  echo "count^FAIL: = $(grep -c '^FAIL: ' "$DISC")"
  echo "count^ERROR: = $(grep -c '^ERROR: ' "$DISC")"
  echo "count RED_RE=[$RED_RE] = $(grep -Ec "$RED_RE" "$DISC")"

  echo
  echo "--- 红名单身份（排序后清单的 md5，与批14/15/16 同值才算⛔ 新增行为回归）---"
  TMPLIST=/tmp/b17_redlist_$$.txt
  grep -E "$RED_RE" "$DISC" | sort >"$TMPLIST"
  echo "redlist_lines=$(wc -l <"$TMPLIST")"
  RED_MD5=$(md5sum "$TMPLIST" | cut -d' ' -f1)
  echo "redlist_md5=$RED_MD5 want=$REDLIST_WANT"
  if [ "$RED_MD5" = "$REDLIST_WANT" ]; then echo "REDLIST_SAME"; else echo "REDLIST_CHANGED"; fi
  sed 's/^/  /' "$TMPLIST"
  rm -f "$TMPLIST"
  if [ -e "$TMPLIST" ]; then echo "TMP_RESIDUE|$TMPLIST"; else echo "TMP_REMOVED|$TMPLIST"; fi

  echo
  echo "--- AST 三数 ---"
  echo "cmd: python3 -B scripts/audit_1002/ast_test_census_1002.py"
  python3 -B scripts/audit_1002/ast_test_census_1002.py >"$CEN" 2>&1
  cen_rc=$?
  head -4 "$CEN"
  echo "census_rc=$cen_rc"
  echo "读数件：$(wc -c <"$DISC") B $DISC / $(wc -c <"$CEN") B $CEN"
} >"$LOG" 2>&1
cat "$LOG"
