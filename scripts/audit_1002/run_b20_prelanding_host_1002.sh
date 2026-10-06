#!/usr/bin/env bash
# 批20 落码前预跑：把部署树那四枚**干净的**基线码复制成 /tmp/b20_dry，在复制件上跑三枚一次性落码件，
# 再用宿主 python 跑验收件——⛔ 动部署树本体（宿主没有 starlette，C/D 执行腿只能到容器里数，
# 但 A/B 两组＋C3/C4 两条结构腿在宿主就能对**打完补丁的那份**取到绿/红）。
# 每跑一次重建 /tmp/b20_dry：三枚 patcher 是一次性的，⛔ 在同一棵树上跑第二遍。
# 期望读数：A/B 全 ok、C3/C4 ok、跳过 6 条（C1/C2/C5＋D1/D2/D3，全因宿主导不进 butler.app）。
# DATA_DIR 与 B20_APP_PY 都钉在 /tmp 的复制件上，⛔ 碰现网 WAL 库、⛔ 读部署树当被测码。
# 用法：bash scripts/audit_1002/run_b20_prelanding_host_1002.sh <读数文件名>
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OUT=workorders/readings/1003b20
mkdir -p "$OUT"
NAME=${1:?用法：run_b20_prelanding_host_1002.sh <读数文件名>}
LOG="$OUT/$NAME.txt"
TEST=tests/test_audit_1002_batch20_async_outcome_silent_loss.py
MOD=tests.test_audit_1002_batch20_async_outcome_silent_loss
TARGETS="butler/app.py butler/runtime.py butler/bus/mqtt_client.py butler/triggers/registry.py"
PATCHERS="patch_b20_mqtt_deadguard_1002 patch_b20_registry_future_1002 patch_b20_app_lifecycle_sse_1002"
CFG=butler/config.py
GATE_FROM=300
GATE_TO=326
LIVE_DB=/vol1/1000/docker/doubao-butler/data/butler.db
DRY=/tmp/b20_dry
DDATA=/tmp/b20_dry_data
stat2() { stat -c '%Y %s' "$1" 2>/dev/null || echo NO_SUCH_FILE; }

KEYS=$(sed -n "${GATE_FROM},${GATE_TO}p" "$CFG" \
       | grep -oE '[A-Z][A-Z0-9_]*_(API_)?(KEY|TOKEN)|BUTLER_WEB_PASSWORD' \
       | sort -u)
n=$(printf '%s\n' "$KEYS" | grep -c .)
if [ "$n" != "4" ]; then
  echo "GATE_KEYS_UNEXPECTED count=$n want=4 range=${CFG}:${GATE_FROM}-${GATE_TO}"
  printf '%s\n' "$KEYS"
  exit 8
fi

BEFORE=$(stat2 "$LIVE_DB")
{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "cmd: bash scripts/audit_1002/run_b20_prelanding_host_1002.sh $NAME"
  echo "ENV_PIN gate_range=${CFG}:${GATE_FROM}-${GATE_TO} derived_count=$n values=DUMMY_NOT_REAL DATA_DIR=$DDATA B20_APP_PY=$DRY/butler/app.py"
  echo "HEAD=$(git rev-parse --short HEAD)"
  echo "LIVE_DB_BEFORE=$BEFORE"
} >"$LOG" 2>&1

rm -rf "$DRY" "$DDATA"
mkdir -p "$DRY/tests" "$DDATA"
# 整包复制（⛔ 只拷那四枚）：`from butler.runtime import Runtime`／`butler.bus.topics`
# 这些 import 要吃得下，只拷四枚会退化成「一整档一个 ImportError」那种 harness 缺陷。
# static/ 与 dashboard_pwa/ 不拷：宿主本来就导不进 butler.app（没有 starlette），
# 那两枚目录在这里没有任何读者。
rsync -a --exclude 'static/' --exclude 'dashboard_pwa/' butler/ "$DRY/butler/" 2>>"$LOG"
cp "$TEST" tests/__init__.py "$DRY/tests/" 2>>"$LOG"

{
  echo "--- 复制件基底（⛔ 部署树本体被本脚本改写过）---"
  md5sum $TARGETS
  ( cd "$DRY" && md5sum $TARGETS ) | sed 's/^/pre_patch|/'
} >>"$LOG" 2>&1

for p in $PATCHERS; do
  echo "=== $p ===" >>"$LOG" 2>&1
  ( cd "$DRY" && python3 -B "/vol1/1000/docker/doubao-butler/scripts/audit_1002/$p.py" ) >>"$LOG" 2>&1
  echo "patcher_rc=$p=$?" >>"$LOG" 2>&1
  ( cd "$DRY" && python3 -B "/vol1/1000/docker/doubao-butler/scripts/audit_1002/$p.py" ) >>"$LOG" 2>&1
  echo "rerun_must_raise_rc=$p=$?" >>"$LOG" 2>&1
done

{
  echo "--- 打完补丁的复制件（落码后部署树应与此同号）---"
  ( cd "$DRY" && md5sum $TARGETS ) | sed 's/^/dry_patched|/'
  ( cd "$DRY" && wc -l $TARGETS ) | sed 's/^/dry_lines|/'
  python3 -B -c "
import pathlib
for f in '$TARGETS'.split():
    b = pathlib.Path('$DRY/' + f).read_bytes()
    print('dry_eol|%s|crlf=%d|lf=%d|bytes=%d' % (f, b.count(b'\r\n'), b.count(b'\n'), len(b)))
" 2>&1
  echo "--- 语法闸（复制件整树编译一遍；rc 取 compileall 自己的，⛔ 管道末端）---"
  python3 -B -m compileall -q "$DRY/butler" >"$DRY/.compileall.out" 2>&1
  echo "compileall_rc=$?"
  tail -3 "$DRY/.compileall.out"
} >>"$LOG" 2>&1

start=$(date +%s)
( cd "$DRY" && for k in $KEYS; do export "$k=DUMMY_NOT_REAL"; done
  export DATA_DIR="$DDATA" B20_APP_PY="$DRY/butler/app.py" PYTHONPATH="$DRY"
  python3 -B -m unittest -v "$MOD" ) >>"$LOG" 2>&1
rc=$?
{
  echo "unittest_rc=$rc wall=$(( $(date +%s) - start ))s"
  echo "import_shadow=（宿主 cwd=$DRY，tests 只有这一枚，⛔ 容器那棵 /tmp/tests 的干扰不在本路）"
  grep -E '^Ran |^OK|^FAILED' "$LOG" | sed 's/^/summary|/'
  echo "skipped_lines=$(grep -c '\.\.\. skipped' "$LOG")"
  echo "--- 逐腿结局（去掉类前缀噪音）---"
  grep -E '\.\.\. (ok|FAIL|ERROR|skipped)' "$LOG" \
    | sed -E 's/ \(tests[^)]*\)//' | cut -c1-160 | sed 's/^/  LEG|/'
  echo "--- 失败原因 ---"
  grep -E '^AssertionError|^ERROR:|^FAIL: ' "$LOG" | cut -c1-260 | sed 's/^/  WHY|/'
  echo "LIVE_DB_AFTER=$(stat2 "$LIVE_DB")"
  echo "LIVE_DB_SAME=$([ "$BEFORE" = "$(stat2 "$LIVE_DB")" ] && echo YES || echo 'NO(现网真流量，非本跑所致)')"
  echo "deploy_tree_untouched=$(md5sum butler/app.py butler/runtime.py butler/bus/mqtt_client.py butler/triggers/registry.py | tr '\n' ' ')"
  echo "dry_data_files=$(ls -1 "$DDATA" 2>/dev/null | tr '\n' ' ')"
  date -u '+END_STAMP %Y-%m-%dT%H:%M:%SZ'
} >>"$LOG" 2>&1

echo "rc=$rc log=$LOG bytes=$(wc -c <"$LOG")"
