#!/usr/bin/env bash
# 批20 变异尺·容器跑法。
# 为什么必须在容器里跑变异：宿主 python 没有 starlette ⇒ C1/C2/D 三腿在宿主是 skip，
# 变异「没被咬」和「没人测」在宿主读数里长得一模一样。容器那一跑 skipped=0 才算数。
#
# 树怎么来：容器 /app/butler 就是部署树的 rw bind mount（bind_vs_deploy 同号已证），
#   所以变异树＝`cp -r /app/butler /tmp/b20_mut/butler`，⛔ 往部署树里写一个字节。
#   ⛔ 只拷 .py：butler/app.py 在 import 期要 StaticFiles(directory=…/dashboard_pwa)，
#   整目录拷一次 8.4MB，容器 tmpfs 是 64m，装得下。
# PYTHONPATH 只放 /tmp/b20_mut（⛔ 再挂 /app）——万一顺序错，跑的其实是没变的现网码，
#   变异全「存活」会被读成「尺子没洞」；所以每次跑都打 butler.*.__file__ 自证吃的是副本。
# 每枚变异跑完 --restore 回原字节，md5 当场对账；跑完删树，删与 ls 反证写在同一次调用里。
# 判据：KILL 类＝指名的腿必须逐枚变红（哪枚没红就报 HOLE，⛔ 用「总分变了」糊过去）；
#       CTRL 类＝语义等价的写法改动必须继续全绿（红了＝尺在匹配字面量⛔ 匹配行为）。
# 用法：bash scripts/audit_1002/run_b20_mutation_in_container_1002.sh <读数文件名>
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OUT=workorders/readings/1003b20
mkdir -p "$OUT"
NAME=${1:?用法：run_b20_mutation_in_container_1002.sh <读数文件名>}
LOG="$OUT/$NAME.txt"
CTR=doubao-butler
MOD=tests.test_audit_1002_batch20_async_outcome_silent_loss
TEST=tests/test_audit_1002_batch20_async_outcome_silent_loss.py
TARGETS="butler/bus/mqtt_client.py butler/triggers/registry.py butler/app.py butler/runtime.py"
CFG=butler/config.py
GATE_FROM=300
GATE_TO=326
LIVE_DB=/vol1/1000/docker/doubao-butler/data/butler.db
CMUT=/tmp/b20_mut
CQDATA=/tmp/b20_mut_data
HOST_STAGE=/tmp/b20_mut_stage
HOST_TAR=/tmp/b20_mut_stage.tar
HOST_MLOG=/tmp/b20_mut_run.log
CAPP=$CMUT/butler/app.py

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
DEPLOY_MD5_BEFORE=$(md5sum $TARGETS | tr '\n' ' ')

{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "cmd: bash scripts/audit_1002/run_b20_mutation_in_container_1002.sh $NAME"
  echo "ENV_PIN gate_range=${CFG}:${GATE_FROM}-${GATE_TO} derived_count=$n names=$(printf '%s,' $KEYS | sed 's/,$//') values=DUMMY_NOT_REAL DATA_DIR=$CQDATA B20_APP_PY=$CAPP"
  echo "container=$CTR $(docker inspect -f '{{.State.Status}} started={{.State.StartedAt}} readonly={{.HostConfig.ReadonlyRootfs}} user={{.Config.User}}' "$CTR")"
  echo "python_in_container=$(docker exec "$CTR" python3 -V 2>&1)"
  echo "HEAD=$(git rev-parse --short HEAD) tree_root=$(pwd)"
  echo "--- 部署树基底（变异树就是从这一枚 cp 出来的，本跑⛔ 动它）---"
  md5sum "$TEST" $TARGETS
  echo "deploy_md5_before=$DEPLOY_MD5_BEFORE"
  echo "LIVE_DB_BEFORE=$BEFORE"
  echo "LIVE_DB_WAL_BEFORE=$WAL_BEFORE"
  echo "--- 分母三数（宿主 AST 现读，与两枚跑尺件同一把尺）---"
  python3 -B "$PWD/scripts/audit_1002/b20_ast_denominator.py" "$TEST"
} >"$LOG" 2>&1

# ── 变异树：容器内 cp，⛔ 经宿主中转 butler/
docker exec "$CTR" sh -c "rm -rf $CMUT $CQDATA; mkdir -p $CMUT $CQDATA; cp -r /app/butler $CMUT/butler; find $CMUT -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; du -sb $CMUT | tr -d '\n'; echo ' mut_tree_bytes'; ls -d $CMUT/butler/static $CMUT/butler/dashboard_pwa" >>"$LOG" 2>&1
cp_rc=$?

# ── tests/ 与变异尺进容器：唯一可行的通道还是 tar → docker exec -i
rm -rf "$HOST_STAGE" "$HOST_TAR"
mkdir -p "$HOST_STAGE/tests"
cp "$TEST" tests/__init__.py "$HOST_STAGE/tests/"
cp scripts/audit_1002/b20_mutate.py "$HOST_STAGE/"
( cd "$HOST_STAGE" && tar cf "$HOST_TAR" tests b20_mutate.py )
tar_rc=0
docker exec -i "$CTR" sh -c "tar xf - -C $CMUT" <"$HOST_TAR" >>"$LOG" 2>&1 || tar_rc=$?

{
  echo "cp_rc=$cp_rc tar_into_container_rc=$tar_rc"
  echo "--- 到货自证（副本里的验收件与变异尺必须与部署树/仓内同号）---"
  docker exec "$CTR" sh -c "find $CMUT -maxdepth 2 -type f | sort | head -12; md5sum $CMUT/$TEST $CMUT/b20_mutate.py" 2>&1
  echo "test_same=$([ "$(docker exec "$CTR" md5sum $CMUT/$TEST 2>/dev/null | cut -d' ' -f1)" = "$(md5sum "$TEST" | cut -d' ' -f1)" ] && echo YES || echo NO)"
  echo "mutate_same=$([ "$(docker exec "$CTR" md5sum $CMUT/b20_mutate.py 2>/dev/null | cut -d' ' -f1)" = "$(md5sum scripts/audit_1002/b20_mutate.py | cut -d' ' -f1)" ] && echo YES || echo NO)"
  echo "--- 快照：四枚原字节存档，restore 有据 ---"
  docker exec -i "$CTR" python3 "$CMUT/b20_mutate.py" --snapshot 2>&1
} >>"$LOG" 2>&1

{
  echo "--- 吃的是副本还是现网码：__file__ 必须落在 $CMUT（⛔ /app）---"
  docker exec -i $ENVARGS -w "$CMUT/tests" "$CTR" env PYTHONPATH="$CMUT" \
    python3 -B -c "import sys, butler.app as a, butler.triggers.registry as r, butler.bus.mqtt_client as q, butler.runtime as rt; print('sys_path_head=', sys.path[:3]); print('prove_app=', a.__file__); print('prove_registry=', r.__file__); print('prove_mqtt=', q.__file__); print('prove_runtime=', rt.__file__)" 2>&1
} >>"$LOG" 2>&1

# ── 逐枚变异
# ⚠ 名册行形制是 `MUT|id|kind=…`（竖线不是空格）；docker exec ⛔ 带 -i：
#   本循环的 stdin 就是名册本身，`-i` 会把后面的行读走＝只跑第一枚甚至一枚不跑。
#   上一次这一跑以 kill=0 ctrl=0 的空账收场，就是这两条叠出来的假绿。
MUTLIST=$(docker exec "$CTR" python3 "$CMUT/b20_mutate.py" --list 2>&1)
roster_lines=$(printf '%s\n' "$MUTLIST" | grep -c '^MUT|')
{
  echo "--- 变异名册（尺自报分母）---"
  printf '%s\n' "$MUTLIST"
  echo "roster_lines=$roster_lines"
} >>"$LOG" 2>&1

kill_total=0; ctrl_total=0; hole_total=0; apply_fail=0; ran_bad=0; iterated=0
while IFS= read -r line; do
  case "$line" in
    MUT\|*) ;;
    *) continue ;;
  esac
  iterated=$((iterated + 1))
  mid=$(printf '%s' "$line" | cut -d'|' -f2)
  kind=$(printf '%s' "$line" | sed 's/.*kind=\([A-Z]*\).*/\1/')
  mfile=$(printf '%s' "$line" | sed 's/.*file=\([^|]*\).*/\1/')
  expect=$(printf '%s' "$line" | sed 's/.*expect_fail=//' | tr ',' ' ')
  [ "$kind" = "KILL" ] && kill_total=$((kill_total + 1)) || ctrl_total=$((ctrl_total + 1))

  {
    echo ""
    echo "================ $mid (kind=$kind file=$mfile) ================"
  } >>"$LOG" 2>&1

  apply_out=$(docker exec "$CTR" python3 "$CMUT/b20_mutate.py" --apply "$mid" </dev/null 2>&1)
  apply_rc=$?
  echo "apply_rc=$apply_rc $apply_out" >>"$LOG" 2>&1
  if [ "$apply_rc" != "0" ]; then
    apply_fail=$((apply_fail + 1))
    echo "VERDICT|$mid|APPLY_FAIL（变异没落进副本＝这一枚没测，⛔ 记成存活）" >>"$LOG" 2>&1
    docker exec "$CTR" python3 "$CMUT/b20_mutate.py" --restore </dev/null >>"$LOG" 2>&1
    continue
  fi

  start=$(date +%s)
  docker exec $ENVARGS -w "$CMUT/tests" "$CTR" </dev/null env PYTHONPATH="$CMUT" \
    python3 -B -m unittest -v "$MOD" >"$HOST_MLOG" 2>&1
  mrc=$?
  ran=$(grep -cE '^Ran ' "$HOST_MLOG")
  ran_n=$(grep -E '^Ran ' "$HOST_MLOG" | awk '{print $2}')
  echo "unittest_rc=$mrc ran_lines=$ran ran_tests=$ran_n wall=$(( $(date +%s) - start ))s" >>"$LOG" 2>&1
  grep -E '^Ran |^OK|^FAILED' "$HOST_MLOG" | sed 's/^/summary|/' >>"$LOG" 2>&1
  grep -E '^(FAIL|ERROR): ' "$HOST_MLOG" | cut -c1-160 | sed 's/^/  FAILED_LEG|/' >>"$LOG" 2>&1
  grep -E '^AssertionError|^RuntimeError|^TypeError|^SyntaxError|^AttributeError' "$HOST_MLOG" | cut -c1-200 | sed 's/^/  WHY|/' >>"$LOG" 2>&1

  if [ "$ran_n" != "18" ]; then
    ran_bad=$((ran_bad + 1))
    echo "VERDICT|$mid|RAN_NOT_18（分母变了＝这一跑的结局不能当证据）" >>"$LOG" 2>&1
  fi

  if [ "$kind" = "KILL" ]; then
    for e in $expect; do
      if grep -qE "^(FAIL|ERROR): $e " "$HOST_MLOG"; then
        echo "KILLED_BY|$mid|$e" >>"$LOG" 2>&1
      else
        hole_total=$((hole_total + 1))
        echo "HOLE|$mid|该腿没咬|$e（坏形状已落进副本而它判绿＝尺有洞）" >>"$LOG" 2>&1
      fi
    done
  else
    if [ "$mrc" = "0" ] && grep -qE '^OK' "$HOST_MLOG"; then
      echo "SURVIVED|$mid（等价写法改动，尺子没误伤＝它测的是行为）" >>"$LOG" 2>&1
    else
      hole_total=$((hole_total + 1))
      echo "CTRL_DIED|$mid（语义等价却红了＝尺在匹配字面量，登记为尺缺陷）" >>"$LOG" 2>&1
    fi
  fi

  docker exec "$CTR" python3 "$CMUT/b20_mutate.py" --restore </dev/null >>"$LOG" 2>&1
done <<EOF
$MUTLIST
EOF

{
  echo ""
  echo "--- 总账 ---"
  echo "gate|iterated=$iterated roster_lines=$roster_lines kill_mutants=$kill_total ctrl_mutants=$ctrl_total holes=$hole_total apply_fail=$apply_fail ran_not_18=$ran_bad"
  echo "--- 现网库反证 ---"
  echo "LIVE_DB_AFTER=$(stat2 "$LIVE_DB")"
  echo "LIVE_DB_SAME=$([ "$BEFORE" = "$(stat2 "$LIVE_DB")" ] && echo YES || echo 'NO(现网有真流量，非本跑所致＝要人判)')"
  echo "LIVE_DB_WAL_AFTER=$(stat2 "${LIVE_DB}-wal")"
  echo "LIVE_DB_WAL_SAME=$([ "$WAL_BEFORE" = "$(stat2 "${LIVE_DB}-wal")" ] && echo YES || echo 'NO(见上)')"
  echo "deploy_md5_after=$(md5sum $TARGETS | tr '\n' ' ')"
  echo "deploy_tree_untouched=$([ "$DEPLOY_MD5_BEFORE" = "$(md5sum $TARGETS | tr '\n' ' ')" ] && echo YES || echo NO)"
} >>"$LOG" 2>&1

# ── 硬闸：分母没跑满＝这一跑⛔ 算「全过」（本次第一跑就是被 0 枚变异骗过去的）
gate=GREEN
[ "$iterated" = "$roster_lines" ] || gate=RED_iterated_ne_roster
[ "$roster_lines" = "12" ] || gate=RED_roster_not_12
[ "$kill_total" = "8" ] || gate=RED_kill_not_8
[ "$ctrl_total" = "4" ] || gate=RED_ctrl_not_4
[ "$hole_total" = "0" ] || gate=RED_holes
[ "$apply_fail" = "0" ] || gate=RED_apply_fail
[ "$ran_bad" = "0" ] || gate=RED_ran_not_18
echo "GATE|$gate|iterated=$iterated/$roster_lines kill=$kill_total ctrl=$ctrl_total holes=$hole_total apply_fail=$apply_fail ran_not_18=$ran_bad" >>"$LOG" 2>&1

docker exec "$CTR" sh -c "rm -rf $CMUT $CQDATA; ls -d $CMUT $CQDATA 2>&1 | sed 's/^/after_rm|/'" >>"$LOG" 2>&1
rm -rf "$HOST_STAGE" "$HOST_TAR" "$HOST_MLOG"
{
  echo "host_stage_after_rm=$(ls -d "$HOST_STAGE" 2>&1 | sed 's/.*: //')"
  echo "host_mlog_after_rm=$(ls -d "$HOST_MLOG" 2>&1 | sed 's/.*: //')"
  date -u '+END_STAMP %Y-%m-%dT%H:%M:%SZ'
} >>"$LOG" 2>&1

echo "gate=$gate holes=$hole_total apply_fail=$apply_fail iterated=$iterated/$roster_lines log=$LOG bytes=$(wc -c <"$LOG")"
[ "$gate" = "GREEN" ] || exit 7
