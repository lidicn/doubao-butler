#!/usr/bin/env bash
# 批16 收口读数：全量回归 ＋ 红名单身份 ＋ AST 三数 ＋ 被测四件回基线。
# 为什么要落成脚本（⛔ 每次手敲一遍）：上一版我把这些步骤写成 ssh 里的内联串，
# 嵌套双引号被 bash 吃掉、整块没跑起来（读数件不存在＝"没测"，⛔ 读成"0 命中"）。
# 用法：bash scripts/audit_1002/run_b16_regression_1002.sh
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OUT=workorders/readings/1003b16
mkdir -p "$OUT"
DISC="$OUT/discover_full.txt"
CEN="$OUT/census.txt"
LOG="$OUT/regression_and_census.txt"

BARK_WANT=33805cd879c124f904003638b13982d7
MA_WANT=22e90f1bd83ce6276085e5952ddc7a30
ALI_WANT=ba541ed3d69aa7479629e07592d28009
TEST_WANT=6d17003edc022b5b2f6c9fbeaba17aa8
# 批14/批15 记进台账的红名单身份（pattern 见下面 RED_RE 原文）
REDLIST_WANT=9490b2e3085174ec213d6b34d0e0d01d
RED_RE='^(FAIL|ERROR): '

{
  echo "=== 批16 收口读数 ==="
  date -u '+stamp_utc=%Y-%m-%dT%H:%M:%SZ'
  echo "cmd: bash scripts/audit_1002/run_b16_regression_1002.sh"
  echo "--- 跑前：被测四件必须回到 GREEN 基线（变异探针还原后的自证）---"
  md5sum butler/integrations/bark.py butler/integrations/memory_agent.py \
         butler/core/aliases.py tests/test_audit_1002_batch16_order_and_clear.py
  for pair in "butler/integrations/bark.py=$BARK_WANT" \
              "butler/integrations/memory_agent.py=$MA_WANT" \
              "butler/core/aliases.py=$ALI_WANT" \
              "tests/test_audit_1002_batch16_order_and_clear.py=$TEST_WANT"; do
    f=${pair%%=*}; want=${pair##*=}; got=$(md5sum "$f" | cut -d' ' -f1)
    if [ "$got" = "$want" ]; then echo "BASELINE_OK|$f|$got"; else echo "BASELINE_BAD|$f|got=$got|want=$want"; fi
  done

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
  echo "--- 红名单身份（排序后的清单 md5，与批14/15 同值才算⛔ 新增行为回归）---"
  TMPLIST=/tmp/b16_redlist_$$.txt
  grep -E "$RED_RE" "$DISC" | sort >"$TMPLIST"
  echo "redlist_lines=$(wc -l <"$TMPLIST")"
  echo "redlist_md5=$(md5sum "$TMPLIST" | cut -d' ' -f1) want=$REDLIST_WANT"
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
