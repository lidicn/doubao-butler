#!/usr/bin/env bash
# 批20 收口读数：全量回归 ＋ 红名单身份 ＋ 被测四件回基线 ＋ skip 分母点名。
# 形制照批19（落成脚本的理由同批17：内联串在 bash 嵌套引号下会静默丢腿＝读数件不存在＝没测）。
# skip 这一格为什么要点名：批20 的验收件有 6 枚腿（C1/C2/C5/D1/D2/D3）在宿主只能 skip
# （宿主 python 没有 starlette），容器那一跑才是 0 skip 的全量读数。
# 「skipped=N」只报计数不报名字，下次浮动就无从对齐＝假绿通道；所以本件把六枚腿逐枚点名，
# 少一枚或多一枚都判 RED，并把容器跑法的读数件号写在同一页上。
# 用法：bash scripts/audit_1002/run_b20_regression_1002.sh
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OUT=workorders/readings/1003b20
mkdir -p "$OUT"
DISC="$OUT/discover_full.txt"
CEN="$OUT/census.txt"
LOG="$OUT/regression_and_census.txt"

TEST_FILE=tests/test_audit_1002_batch20_async_outcome_silent_loss.py
# 被测四件的 GREEN 基线＝三枚一次性落码件的 PATCH_OK 行 md5（2026-10-02T21:53:41Z 落盘）
BASE_WANT="75f1e1b52b3e5b9cbca0303529769f66 butler/bus/mqtt_client.py
6b046810b9a18ec3c899bcc0b93906d4 butler/triggers/registry.py
fd7c61a429782326e385fdedf762f5a3 butler/app.py
ddb771c8403cff5ce582dc78dced306f butler/runtime.py"
# 批14/15/16/17/18/19 记进台账的红名单身份（批20 只加绿腿，⛔ 动这张名单）
REDLIST_WANT=9490b2e3085174ec213d6b34d0e0d01d
RED_RE='^(FAIL|ERROR): '
# 宿主只能 skip 的六枚腿（容器那跑 0 skip＝green_b20_container_run2.txt）。
# 每枚给两种写法：`方法名##docstring 首行片段`——unittest -v 对**有 docstring 的腿打印的是
# 文档串⛔ 方法名**（本件里三枚如此），只按方法名 grep 会把它们判成「没点名」＝假红。
SKIP_WANT=6
SKIP_LEGS="test_loop_returns_when_stop_event_is_already_set##stop 事件已置位
test_loop_wakes_promptly_when_stop_is_set_during_sleep##间隔 30 秒也要在
test_structural_legs_read_the_same_file_the_runner_executes##C3/C4 读的那枚
test_full_subscriber_drop_is_counted##test_full_subscriber_drop_is_counted
test_full_subscriber_drop_is_logged##test_full_subscriber_drop_is_logged
test_subscriber_with_room_receives_payload##正常投递不受计数逻辑影响"

KEYS=$(sed -n '300,326p' butler/config.py \
       | grep -oE '[A-Z][A-Z0-9_]*_(API_)?(KEY|TOKEN)|BUTLER_WEB_PASSWORD' | sort -u)
n=$(printf '%s\n' "$KEYS" | grep -c .)
if [ "$n" != "4" ]; then echo "GATE_KEYS_UNEXPECTED count=$n want=4"; exit 8; fi
for k in $KEYS; do export "$k=DUMMY_NOT_REAL"; done
export DATA_DIR=/tmp/b20_qa_data
export B20_APP_PY="$PWD/butler/app.py"
mkdir -p "$DATA_DIR"

LIVE_DB=/vol1/1000/docker/doubao-butler/data/butler.db
BEFORE=$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null || echo NO_LIVE_DB)

base_check() {
  while IFS= read -r want; do
    f=$(printf '%s' "$want" | cut -d' ' -f2)
    got=$(md5sum "$f" | cut -d' ' -f1)
    if [ "$got" = "$(printf '%s' "$want" | cut -d' ' -f1)" ]; then
      echo "BASELINE_OK|$f|$got"
    else
      echo "BASELINE_BAD|$f|got=$got|want=$(printf '%s' "$want" | cut -d' ' -f1)"
    fi
  done <<EOF
$BASE_WANT
EOF
}

{
  echo "=== 批20 收口读数 ==="
  date -u '+stamp_utc=%Y-%m-%dT%H:%M:%SZ'
  echo "cmd: bash scripts/audit_1002/run_b20_regression_1002.sh"
  echo "ENV_PIN gate_names=$(printf '%s,' $KEYS | sed 's/,$//') values=DUMMY_NOT_REAL DATA_DIR=$DATA_DIR B20_APP_PY=$B20_APP_PY"
  echo "HEAD=$(git rev-parse --short HEAD)"
  echo "LIVE_DB_BEFORE=$BEFORE"

  echo "--- 跑前：被测四件必须是落码基线（变异探针在副本树上跑，restore 已对账）---"
  md5sum $TEST_FILE
  base_check

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

  echo "--- skip 分母（批20 件在宿主应恰好 $SKIP_WANT 枚，逐枚点名＋理由）---"
  grep -E '\.\.\. skipped' "$DISC" | grep 'batch20' | sed 's/^/  /' | cut -c1-200
  b20_skip=$(grep -E '\.\.\. skipped' "$DISC" | grep -c 'batch20')
  echo "batch20_skip=$b20_skip want=$SKIP_WANT"
  miss=0
  while IFS= read -r pair; do
    leg=${pair%%##*}
    frag=${pair##*##}
    if grep -E '\.\.\. skipped' "$DISC" | grep 'batch20' | grep -qE "$leg|$frag"; then
      echo "SKIP_NAMED_OK|$leg"
    else
      echo "SKIP_NAMED_MISSING|$leg"
      miss=$((miss + 1))
    fi
  done <<EOF
$SKIP_LEGS
EOF
  echo "skip_named_missing=$miss"
  [ "$b20_skip" = "$SKIP_WANT" ] && [ "$miss" = "0" ] && echo "SKIP_DENOM_SAME" || echo "SKIP_DENOM_CHANGED(b20_skip=$b20_skip want=$SKIP_WANT missing=$miss)"
  echo "全库 skipped 总行数=$(grep -cE '\.\.\. skipped' "$DISC") summary=$(grep -oE 'skipped=[0-9]+' "$DISC" | head -1)"

  echo
  echo "--- 红名单身份（排序后清单的 md5，与批14–19 同值才算⛔ 新增行为回归）---"
  TMPLIST=/tmp/b20_redlist_$$.txt
  grep -E "$RED_RE" "$DISC" | sort >"$TMPLIST"
  echo "redlist_lines=$(wc -l <"$TMPLIST")"
  RED_MD5=$(md5sum "$TMPLIST" | cut -d' ' -f1)
  echo "redlist_md5=$RED_MD5 want=$REDLIST_WANT"
  if [ "$RED_MD5" = "$REDLIST_WANT" ]; then echo "REDLIST_SAME"; else echo "REDLIST_CHANGED"; fi
  sed 's/^/  /' "$TMPLIST"
  rm -f "$TMPLIST"
  if [ -e "$TMPLIST" ]; then echo "TMP_RESIDUE|$TMPLIST"; else echo "TMP_REMOVED|$TMPLIST"; fi

  echo
  echo "--- AST 三数（独立第二尺，与容器 Ran=18 对账）---"
  echo "cmd: python3 -B scripts/audit_1002/ast_test_census_1002.py"
  python3 -B scripts/audit_1002/ast_test_census_1002.py >"$CEN" 2>&1
  cen_rc=$?
  head -6 "$CEN"
  echo "census_rc=$cen_rc"
  echo "本件单枚尺：$(python3 -B "$PWD/scripts/audit_1002/b20_ast_denominator.py" "$TEST_FILE" | head -1)"
  echo "读数件：$(wc -c <"$DISC") B $DISC / $(wc -c <"$CEN") B $CEN"

  echo
  echo "--- 跑后：被测四件仍回基线（回归⛔ 改生产码）＋现网库反证 ---"
  base_check
  echo "LIVE_DB_AFTER=$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null || echo NO_LIVE_DB)"
  echo "LIVE_DB_SAME=$([ "$BEFORE" = "$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null)" ] && echo YES || echo 'NO(现网有真流量，非本跑所致＝要人判)')"
  rm -rf "$DATA_DIR"
  echo "data_dir_after_rm=$(ls -d "$DATA_DIR" 2>&1 | sed 's/.*: //')"
} >"$LOG" 2>&1
cat "$LOG"
