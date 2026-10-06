#!/usr/bin/env bash
# 批19 收口读数：全量回归 ＋ 红名单身份 ＋ AST 三数 ＋ 被测一件回基线。
# 形制照批18 的 runner（落成脚本的理由同批17：内联串在 bash 嵌套引号下会静默丢腿＝读数件不存在＝没测）。
# 用法：bash scripts/audit_1002/run_b19_regression_1002.sh
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OUT=workorders/readings/1003b19
mkdir -p "$OUT"
DISC="$OUT/discover_full.txt"
CEN="$OUT/census.txt"
LOG="$OUT/regression_and_census.txt"

# 被测生产码的 GREEN 基线＝patch_b19_db_getconn_1002.py 的 PATCH_OK 行 md5
DB_WANT=7dcf301fed05450166716f4d47982dbf
# 批14/15/16/17/18 记进台账的红名单身份
REDLIST_WANT=9490b2e3085174ec213d6b34d0e0d01d
RED_RE='^(FAIL|ERROR): '
TEST_FILE=tests/test_audit_1002_batch19_db_getconn_race.py

# 启动硬门四把键：名字从 config.py 硬门段现场 grep（同 run_b19_acceptance_1002.sh），⛔ 手抄
KEYS=$(sed -n '300,326p' butler/config.py \
       | grep -oE '[A-Z][A-Z0-9_]*_(API_)?(KEY|TOKEN)|BUTLER_WEB_PASSWORD' | sort -u)
n=$(printf '%s\n' "$KEYS" | grep -c .)
if [ "$n" != "4" ]; then echo "GATE_KEYS_UNEXPECTED count=$n want=4"; exit 8; fi
for k in $KEYS; do export "$k=DUMMY_NOT_REAL"; done
export DATA_DIR=/tmp/b19_qa_data
mkdir -p "$DATA_DIR"

LIVE_DB=/vol1/1000/docker/doubao-butler/data/butler.db
BEFORE=$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null || echo NO_LIVE_DB)

{
  echo "=== 批19 收口读数 ==="
  date -u '+stamp_utc=%Y-%m-%dT%H:%M:%SZ'
  echo "cmd: bash scripts/audit_1002/run_b19_regression_1002.sh"
  echo "ENV_PIN gate_names=$(printf '%s,' $KEYS | sed 's/,$//') values=DUMMY_NOT_REAL DATA_DIR=$DATA_DIR"
  echo "HEAD=$(git rev-parse --short HEAD)"
  echo "LIVE_DB_BEFORE=$BEFORE"

  echo "--- 跑前：被测一件必须是 GREEN 基线（变异探针还原后的自证）；验收件 md5 现读不手填 ---"
  md5sum butler/store/db.py "$TEST_FILE"
  got=$(md5sum butler/store/db.py | cut -d' ' -f1)
  if [ "$got" = "$DB_WANT" ]; then echo "BASELINE_OK|butler/store/db.py|$got"; else echo "BASELINE_BAD|got=$got|want=$DB_WANT"; fi
  echo "TEST_MD5_LIVE|$TEST_FILE|$(md5sum "$TEST_FILE" | cut -d' ' -f1)"

  echo
  echo "--- 全量回归（-v：skip 行只在 -v 下存在）---"
  echo "cmd: python3 -B -m unittest discover -v -s tests -t ."
  start=$(date +%s)
  python3 -B -m unittest discover -v -s tests -t . >"$DISC" 2>&1
  disc_rc=$?
  echo "discover_rc=$disc_rc wall=$(( $(date +%s) - start ))s"
  echo "ran_lines=$(grep -c '^Ran ' "$DISC") usage_error_lines=$(grep -c '^usage:' "$DISC")"
  grep -E '^Ran |^OK|^FAILED' "$DISC"
  echo "count^FAIL: = $(grep -c '^FAIL: ' "$DISC")"
  echo "count^ERROR: = $(grep -c '^ERROR: ' "$DISC")"
  echo "count RED_RE=[$RED_RE] = $(grep -Ec "$RED_RE" "$DISC")"
  echo "--- skipped 点名（每遍都要有名字＋理由；只报计数＝下次浮动时无从对齐）---"
  grep -E '\.\.\. skipped' "$DISC" | sed 's/^/  /'
  echo "skip_named=$(grep -cE '\.\.\. skipped' "$DISC") summary=$(grep -oE 'skipped=[0-9]+' "$DISC" | head -1)"

  echo
  echo "--- 红名单身份（排序后清单的 md5，与批14/15/16/17/18 同值才算⛔ 新增行为回归）---"
  TMPLIST=/tmp/b19_redlist_$$.txt
  grep -E "$RED_RE" "$DISC" | sort >"$TMPLIST"
  echo "redlist_lines=$(wc -l <"$TMPLIST")"
  RED_MD5=$(md5sum "$TMPLIST" | cut -d' ' -f1)
  echo "redlist_md5=$RED_MD5 want=$REDLIST_WANT"
  if [ "$RED_MD5" = "$REDLIST_WANT" ]; then echo "REDLIST_SAME"; else echo "REDLIST_CHANGED"; fi
  sed 's/^/  /' "$TMPLIST"
  rm -f "$TMPLIST"
  if [ -e "$TMPLIST" ]; then echo "TMP_RESIDUE|$TMPLIST"; else echo "TMP_REMOVED|$TMPLIST"; fi

  echo
  echo "--- AST 三数（批19 新增一件 8 枚腿⇒total 应比批18 那遍多 8，逐数点开）---"
  echo "cmd: python3 -B scripts/audit_1002/ast_test_census_1002.py"
  python3 -B scripts/audit_1002/ast_test_census_1002.py >"$CEN" 2>&1
  cen_rc=$?
  head -6 "$CEN"
  echo "census_rc=$cen_rc"
  echo "读数件：$(wc -c <"$DISC") B $DISC / $(wc -c <"$CEN") B $CEN"

  echo
  echo "--- 跑后：被测一件仍回基线（回归⛔ 改生产码）＋现网库反证 ---"
  md5sum butler/store/db.py
  echo "LIVE_DB_AFTER=$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null || echo NO_LIVE_DB)"
  echo "LIVE_DB_SAME=$([ "$BEFORE" = "$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null)" ] && echo YES || echo 'NO(现网有真流量，非本跑所致＝要人判)')"
} >"$LOG" 2>&1
cat "$LOG"
