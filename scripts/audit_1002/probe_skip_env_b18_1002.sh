#!/usr/bin/env bash
# 批18 台账文书腿：skipped 从批17 的 9 变成 3，那 6 枚到底是**谁**让它们不再跳。
# 两个候选：(a) 本批 runner 现场填了四把启动硬门键的 DUMMY 值（b17 的 runner 一个都不填）；
#           (b) 我对 dialog/singleton/shim 的改动。
# 判别法：同一棵树、同一批测试，只差那四个 env——各跑一遍全量 discover，比 skipped 计数。
# 用法：bash scripts/audit_1002/probe_skip_env_b18_1002.sh
set -u
cd "$(dirname "$0")/../.." || exit 9
OUT=workorders/readings/1003b18/skip_env_attribution_run1.txt
DISC_DIR=/tmp/b18_skip_probe
mkdir -p "$DISC_DIR"
KEYS=$(sed -n '300,326p' butler/config.py \
       | grep -oE '[A-Z][A-Z0-9_]*_(API_)?(KEY|TOKEN)|BUTLER_WEB_PASSWORD' | sort -u)
n=$(printf '%s\n' "$KEYS" | grep -c .)
# DATA_DIR 两遍**都钉死**在 /tmp：⛔ 拿"复现批17 环境"当理由去 unset 它——那会让 T2 直接写活 WAL 库。
# 本探针只⛔ 动四把键，其余（含 DATA_DIR）两遍完全一致。
export DATA_DIR=/tmp/b18_qa_data
mkdir -p "$DATA_DIR"
{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "gate_key_names=$(printf '%s,' $KEYS | sed 's/,$//') count=$n want=4"
  if [ "$n" != "4" ]; then echo "GATE_KEYS_UNEXPECTED|退出"; exit 8; fi
  echo "同一棵树：dialog=$(md5sum butler/core/dialog.py | cut -c1-12) singleton=$(md5sum butler/tts/singleton.py | cut -c1-12) shim=$(md5sum tests/test_v25_pytest_shim.py | cut -c1-12)"
  echo
  echo "=== [T1] 填四把 DUMMY 键（= 本批 runner 的环境）==="
  start=$(date +%s)
  for k in $KEYS; do export "$k=DUMMY_NOT_REAL"; done
  export DATA_DIR=/tmp/b18_qa_data; mkdir -p "$DATA_DIR"
  python3 -B -m unittest discover -s tests -t . >"$DISC_DIR/t1.txt" 2>&1
  echo "T1_rc=$? wall=$(( $(date +%s) - start ))s"
  grep -E '^Ran |^FAILED|^OK' "$DISC_DIR/t1.txt" | sed 's/^/  /'
  echo
  echo "=== [T2] 撤掉那四把键（= 批17 runner 的环境），其余不动 ==="
  start=$(date +%s)
  for k in $KEYS; do unset "$k"; done
  python3 -B -m unittest discover -s tests -t . >"$DISC_DIR/t2.txt" 2>&1
  echo "T2_rc=$? wall=$(( $(date +%s) - start ))s"
  grep -E '^Ran |^FAILED|^OK' "$DISC_DIR/t2.txt" | sed 's/^/  /'
  echo
  echo "=== 判定 ==="
  s1=$(grep -oE 'skipped=[0-9]+' "$DISC_DIR/t1.txt" | grep -oE '[0-9]+' | head -1)
  s2=$(grep -oE 'skipped=[0-9]+' "$DISC_DIR/t2.txt" | grep -oE '[0-9]+' | head -1)
  echo "T1_skipped=${s1:-none} T2_skipped=${s2:-none}"
  if [ "${s2:-x}" = "9" ] && [ "${s1:-x}" = "3" ]; then
    echo "VERDICT|env 腿成立：那 6 枚是「四把键非空」让它们不再跳，⛔ 本批代码改动所致"
  else
    echo "VERDICT|env 腿不成立（T2=${s2:-none} 期望 9）⇒ 归因未定，⛔ 写进台账当结论"
  fi
  echo "红名单在两遍下是否一致："
  for t in t1 t2; do
    grep -cE '^(FAIL|ERROR): ' "$DISC_DIR/$t.txt" | sed "s/^/  ${t}_red_lines=/"
  done
  d=$(diff <(grep -E '^(FAIL|ERROR): ' "$DISC_DIR/t1.txt" | sort) <(grep -E '^(FAIL|ERROR): ' "$DISC_DIR/t2.txt" | sort) | wc -l)
  echo "  redlist_diff_lines=$d (0 ⇒ 两遍红名单同一身份)"
  echo "读数件：$DISC_DIR/t1.txt $(wc -c <"$DISC_DIR/t1.txt") B / $DISC_DIR/t2.txt $(wc -c <"$DISC_DIR/t2.txt") B（在 /tmp，⛔ 碰仓）"
} >"$OUT" 2>&1
rc=$?
echo "probe_rc=$rc out=$OUT ($(wc -c <"$OUT") B)"
cat "$OUT"
exit $rc
