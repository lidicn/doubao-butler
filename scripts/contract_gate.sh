#!/usr/bin/env bash
# 卡6③：ADM 跨仓契约门（契约表 §五 要求「部署前必跑」）。
#
# 退出码就是结论：0 过 / 1 契约不一致 / 2 这条腿此刻量不到（环境不对，⛔ 当绿）。
#
#   bash scripts/contract_gate.sh                          # 宿主机/开发机 python
#   CONTRACT_IN_CONTAINER=1 bash scripts/contract_gate.sh  # 容器里跑（tests/ 不在镜像，先送进 /tmp）
#
# 一档一个进程：同进程连跑多档会串 DATA_DIR、串 sys.modules（假绿假红都从这条来）。
# 分母三个数：Ran / failures+errors / skipped——`Ran 0` 或没有 Ran 行一律判红。
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MODULES="test_db_ma_contract test_af_db_contract test_ma_db_events_contract test_topic_whitelist"
MODE=host
[ "${CONTRACT_IN_CONTAINER:-0}" = "1" ] && MODE=container
CNAME="${CONTRACT_CONTAINER:-doubao-butler}"
PY="${CONTRACT_PY:-python3}"

if [ ! -d "$REPO/tests/contract" ]; then
  echo "  缺少 $REPO/tests/contract —— 分母为 0 的门不许判绿。判 2。"
  exit 2
fi

if [ "$MODE" = "container" ]; then
  if ! docker inspect -f '{{.State.Running}}' "$CNAME" 2>/dev/null | grep -q '^true$'; then
    echo "  容器 $CNAME 没在跑 ⇒ 本腿未量，判 2。"
    exit 2
  fi
  echo "  送 tests/ 进容器 /tmp（现读：/app 只挂 butler·data·requirements.txt·songloft_miot·tts）"
  if ! (cd "$REPO" && tar -cf - tests) | docker exec -i "$CNAME" sh -c 'cd /tmp && tar -xf -'; then
    echo "  tar 投递失败 ⇒ 本腿未量，判 2。"
    exit 2
  fi
elif ! (cd "$REPO" && "$PY" -c "import butler" 2>/dev/null); then
  echo "  解释器（$PY）import 不了 butler ⇒ 本腿未量，⛔ 记成绿。换 CONTRACT_IN_CONTAINER=1 在容器里跑。"
  exit 2
fi

rc_all=0
total=0
for m in $MODULES; do
  if [ "$MODE" = "container" ]; then
    out="$(docker exec -w /app -e DATA_DIR=/tmp/dd_contract \
           -e ADM_CONTRACT_DOC="${ADM_CONTRACT_DOC:-}" -e HOMESDK_WHEEL="${HOMESDK_WHEEL:-}" \
           "$CNAME" sh -c "PYTHONPATH=/app:/tmp python -m unittest tests.contract.$m 2>&1")"
    rc=$?
  else
    out="$(cd "$REPO" && "$PY" -m unittest "tests.contract.$m" 2>&1)"
    rc=$?
  fi
  if ! printf '%s\n' "$out" | grep -q '^Ran '; then
    echo "  $m: 没打出 'Ran N tests' ＝收集器一条没吃到（或解释器压根没进到用例），判红。"
    printf '%s\n' "$out" | tail -15 | sed 's/^/    /'
    rc_all=1
    continue
  fi
  ran="$(printf '%s\n' "$out" | sed -n 's/^Ran \([0-9]*\) tests.*/\1/p' | tail -1)"
  skip="$(printf '%s\n' "$out" | sed -n 's/^OK (skipped=\([0-9]*\)).*/\1/p' | tail -1)"
  [ -z "$skip" ] && skip=0
  total=$((total + ran))
  echo "  $m: Ran=${ran} skipped=${skip} rc=${rc}"
  if [ "$rc" -ne 0 ]; then
    rc_all=1
    printf '%s\n' "$out" | tail -22 | sed 's/^/    /'
  fi
  if [ "$rc" -eq 0 ] && [ "$ran" -eq 0 ]; then
    echo "    —— 0 条用例的 OK 不算通过，判红。"
    rc_all=1
  fi
done
echo "  契约合计 Ran=${total}（四档此刻基线 36 上下；骤降＝有档没被收集）"
if [ "$total" -lt 20 ]; then
  echo "  合计分母 ${total} < 20 ⇒ 门自己不可信，判红。"
  rc_all=1
fi
exit $rc_all
