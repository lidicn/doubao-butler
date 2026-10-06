#!/usr/bin/env bash
# 批20 验收件·容器跑法（第二版路线）。
# 宿主 python 没有 starlette ⇒ C/D 两组的执行腿在宿主只能 skip，容器那一份才是 18 腿全跑的读数。
#
# 路线为什么换（两条都是实读出来的坑，不是猜的）：
#   1) 容器 read_only: true（docker-compose.yml:13-16 + docker inspect ReadonlyRootfs=true），
#      `docker cp` 被 daemon 拒 ⇒ 进得去的只有 tmpfs：/tmp:size=64m ⇒ 码只能 `docker exec -i … tar xf -`。
#   2) ⛔ 覆盖 butler/：butler/app.py:928 在 **import 期** 走 create_app()→:246
#      StaticFiles(directory=…/dashboard_pwa)，只压 .py 进去必崩在目录不存在上。
#      而 /app/butler 就是部署树的 rw bind mount ⇒ 容器里的「盘上码」＝部署码，
#      本跑只读不动它，只把 tests/ 送进 /tmp，PYTHONPATH=/app:/tmp/b20_tests。
#   3) ⛔ cwd 设成 /tmp：容器 /tmp 里有别人在途的一整枚 tests 包（/tmp/tests/__init__.py，
#      现读 59 项）⇒ `-m unittest` 把 cwd 放 sys.path[0]，会压过 PYTHONPATH 后面那枚，
#      跑的就成了别人的 tests 包（本次第一跑就以 ModuleNotFoundError 露出来）。
#      修法＝cwd 用本次新建的 $CTESTS，并把 tests.__path__ 打进读数。
# 指路由跑尺的人**显式给**（B20_APP_PY=/app/butler/app.py），⛔ 让测试自己从 import 反推——
# 反推出来的「读的那份＝跑的那份」是同义反复，测不到指错副本。
# DATA_DIR 钉容器 /tmp（容器默认 /app/data 就是现网 WAL 库），并用现网库跑前跑后同值反证。
# 跑完删树，删与 ls 反证写在同一次调用里。
# 用法：bash scripts/audit_1002/run_b20_acceptance_in_container_1002.sh <读数文件名>
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OUT=workorders/readings/1003b20
mkdir -p "$OUT"
NAME=${1:?用法：run_b20_acceptance_in_container_1002.sh <读数文件名>}
LOG="$OUT/$NAME.txt"
CTR=doubao-butler
MOD=tests.test_audit_1002_batch20_async_outcome_silent_loss
TEST=tests/test_audit_1002_batch20_async_outcome_silent_loss.py
TARGETS="butler/bus/mqtt_client.py butler/triggers/registry.py butler/app.py butler/runtime.py"
CFG=butler/config.py
GATE_FROM=300
GATE_TO=326
LIVE_DB=/vol1/1000/docker/doubao-butler/data/butler.db
HOST_TDIR=/tmp/b20_host_tests
HOST_TAR=/tmp/b20_host_tests.tar
CTESTS=/tmp/b20_tests
CQDATA=/tmp/b20_qa_data
CAPP=/app/butler/app.py

KEYS=$(sed -n "${GATE_FROM},${GATE_TO}p" "$CFG" \
       | grep -oE '[A-Z][A-Z0-9_]*_(API_)?(KEY|TOKEN)|BUTLER_WEB_PASSWORD' \
       | sort -u)
n=$(printf '%s\n' "$KEYS" | grep -c .)
if [ "$n" != "4" ]; then
  echo "GATE_KEYS_UNEXPECTED count=$n want=4 range=${CFG}:${GATE_FROM}-${GATE_TO}"
  printf '%s\n' "$KEYS"
  exit 8
fi
ENVARGS=""
for k in $KEYS; do ENVARGS="$ENVARGS -e $k=DUMMY_NOT_REAL"; done
ENVARGS="$ENVARGS -e DATA_DIR=$CQDATA -e B20_APP_PY=$CAPP"

stat2() { stat -c '%Y %s' "$1" 2>/dev/null || echo NO_SUCH_FILE; }
BEFORE=$(stat2 "$LIVE_DB")
WAL_BEFORE=$(stat2 "${LIVE_DB}-wal")

{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "cmd: bash scripts/audit_1002/run_b20_acceptance_in_container_1002.sh $NAME"
  echo "ENV_PIN gate_range=${CFG}:${GATE_FROM}-${GATE_TO} derived_count=$n names=$(printf '%s,' $KEYS | sed 's/,$//') values=DUMMY_NOT_REAL DATA_DIR=$CQDATA B20_APP_PY=$CAPP"
  echo "container=$CTR $(docker inspect -f '{{.State.Status}} started={{.State.StartedAt}} readonly={{.HostConfig.ReadonlyRootfs}} user={{.Config.User}}' "$CTR")"
  echo "python_in_container=$(docker exec "$CTR" python3 -V 2>&1)"
  echo "HEAD=$(git rev-parse --short HEAD)"
  echo "tree_root=$(pwd)"
  echo "--- 部署树基底（被测码＝容器 bind 进去的那一枚）---"
  md5sum "$TEST" $TARGETS
  echo "test_lines=$(wc -l <"$TEST") test_bytes=$(wc -c <"$TEST")"
  echo "LIVE_DB_BEFORE=$BEFORE"
  echo "LIVE_DB_WAL_BEFORE=$WAL_BEFORE"
} >"$LOG" 2>&1

# ── 只压 tests/：本验收件＋包标记，⛔ 动 butler/
rm -rf "$HOST_TDIR" "$HOST_TAR"
mkdir -p "$HOST_TDIR/tests"
cp "$TEST" tests/__init__.py "$HOST_TDIR/tests/" || echo "TREE_TESTS_CP_FAIL" >>"$LOG"
( cd "$HOST_TDIR" && tar cf "$HOST_TAR" tests ) || echo "TREE_TAR_FAIL" >>"$LOG"
DEPLOY_TEST_MD5=$(md5sum "$TEST" | cut -d' ' -f1)

{
  echo "--- 宿主暂存树点名（到货前只有这一枚 tar，⛔ 再猜内容）---"
  find "$HOST_TDIR" -type f | sort | sed 's/^/host_file|/'
  echo "host_tar_bytes=$(wc -c <"$HOST_TAR")"
} >>"$LOG" 2>&1

# ── 送进容器 tmpfs：先清同名目录＝⛔ 让上一次跑的旧件留在树里
docker exec "$CTR" sh -c "rm -rf $CTESTS $CQDATA; mkdir -p $CTESTS $CQDATA" >>"$LOG" 2>&1
pre_rc=$?
tar_rc=0
docker exec -i "$CTR" sh -c "tar xf - -C $CTESTS" <"$HOST_TAR" >>"$LOG" 2>&1 || tar_rc=$?
{
  echo "pre_clean_rc=$pre_rc tar_into_container_rc=$tar_rc"
  echo "--- 到货自证：容器里那份的 md5 必须与部署树同号（⛔ 读宿主 /tmp 当容器里有）---"
  docker exec "$CTR" sh -c "find $CTESTS -type f | sort; md5sum $CTESTS/$TEST" 2>&1
  ARR=$(docker exec "$CTR" sh -c "md5sum $CTESTS/$TEST" 2>/dev/null | cut -d' ' -f1)
  echo "arrived_test|$TEST|container=$ARR|deploy=$DEPLOY_TEST_MD5|same=$([ "$ARR" = "$DEPLOY_TEST_MD5" ] && echo YES || echo NO)"
  echo "--- 被测码血统：/app 是部署树的 bind mount，逐枚同号才算跑到现读的那份 ---"
  for f in $TARGETS; do
    a=$(docker exec "$CTR" md5sum "/app/$f" 2>&1 | cut -d' ' -f1)
    b=$(md5sum "$f" | cut -d' ' -f1)
    echo "bind_vs_deploy|$f|container=$a|deploy=$b|same=$([ "$a" = "$b" ] && echo YES || echo NO)"
  done
  echo "--- 容器里有没有一枚旧的 /app/tests（有就会抢 PYTHONPATH 前面的位）---"
  docker exec "$CTR" sh -c "ls -d /app/tests 2>&1"
  echo "--- ⚠ 容器 /tmp 里别人在途的 tests 包（跑尺 cwd ⛔ 设 /tmp，否则 sys.path[0] 会吃到它）---"
  docker exec "$CTR" sh -c "ls -d /tmp/tests 2>&1; echo shadow_entries=\$(ls -1 /tmp/tests 2>/dev/null | wc -l); ls /tmp/tests/$TEST 2>&1"
  echo "--- 吃到的到底是谁：包解析路径＋__file__ 必须落在 /app 与 $CTESTS，⛔ 旧副本 ---"
  docker exec -i $ENVARGS -w "$CTESTS" "$CTR" env PYTHONPATH="/app:$CTESTS" python3 -B -c \
    "import sys, tests, butler.app as m, butler.triggers.registry as r, butler.bus.mqtt_client as q, $MOD as t; print('sys_path_head=', sys.path[:4]); print('tests_pkg=', list(tests.__path__)); print('import_proof app=', m.__file__); print('import_proof registry=', r.__file__); print('import_proof mqtt_client=', q.__file__); print('import_proof test=', t.__file__); print('APP_PATH_in_test=', t.APP_PATH, 'B20_APP_PY=', t.APP_ENV)" 2>&1
} >>"$LOG" 2>&1

start=$(date +%s)
docker exec -i $ENVARGS -w "$CTESTS" "$CTR" \
  env PYTHONPATH="/app:$CTESTS" python3 -B -m unittest -v "$MOD" >>"$LOG" 2>&1
rc=$?
echo "unittest_rc=$rc wall=$(( $(date +%s) - start ))s" >>"$LOG" 2>&1

{
  echo "--- 分母三数（AST 在宿主算，Ran 在容器数）---"
  python3 -B "$PWD/scripts/audit_1002/b20_ast_denominator.py" "$TEST"
  grep -E '^Ran |^OK|^FAILED' "$LOG" | sed 's/^/summary|/'
  echo "ran_lines=$(grep -c '^Ran ' "$LOG")"
  echo "skipped_lines=$(grep -c '\.\.\. skipped' "$LOG")"
  echo "--- 逐腿结局 ---"
  grep -E '\.\.\. (ok|FAIL|ERROR|skipped)' "$LOG" | sed 's/^/  LEG|/' | cut -c1-200
  echo "--- 失败原因（AssertionError 原样行）---"
  grep -E '^AssertionError|^ERROR:|^FAIL: ' "$LOG" | cut -c1-300 | sed 's/^/  WHY|/'
  echo "--- 现网库反证：跑后 mtime/字节 应与跑前同号（本跑只写容器 tmpfs）---"
  echo "LIVE_DB_AFTER=$(stat2 "$LIVE_DB")"
  echo "LIVE_DB_SAME=$([ "$BEFORE" = "$(stat2 "$LIVE_DB")" ] && echo YES || echo 'NO(现网有真流量，非本跑所致＝要人判)')"
  echo "LIVE_DB_WAL_AFTER=$(stat2 "${LIVE_DB}-wal")"
  echo "LIVE_DB_WAL_SAME=$([ "$WAL_BEFORE" = "$(stat2 "${LIVE_DB}-wal")" ] && echo YES || echo 'NO(见上，WAL 随现网写入涨落)')"
  echo "container_qa_data_files=$(docker exec "$CTR" sh -c "ls -1 $CQDATA 2>/dev/null | tr '\n' ' '")"
  echo "deploy_code_after_run=$(md5sum $TARGETS | tr '\n' ' ')"
} >>"$LOG" 2>&1

# ── 收尾：容器 tmpfs 与宿主暂存都是本次造的，跑完即删，删与反证同一次调用
docker exec "$CTR" sh -c "rm -rf $CTESTS $CQDATA; ls -d $CTESTS $CQDATA 2>&1 | sed 's/^/after_rm|/'" >>"$LOG" 2>&1
rm -rf "$HOST_TDIR" "$HOST_TAR"
{
  echo "host_tdir_after_rm=$(ls -d "$HOST_TDIR" 2>&1 | sed 's/.*: //')"
  echo "host_tar_after_rm=$(ls -d "$HOST_TAR" 2>&1 | sed 's/.*: //')"
  date -u '+END_STAMP %Y-%m-%dT%H:%M:%SZ'
} >>"$LOG" 2>&1

echo "rc=$rc log=$LOG bytes=$(wc -c <"$LOG")"
