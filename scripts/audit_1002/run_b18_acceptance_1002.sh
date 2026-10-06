#!/usr/bin/env bash
# 批18 验收件单跑：只跑 tests/test_audit_1002_batch18_silent_raise_paths.py。
# 为什么落成脚本（⛔ 手敲内联）：env 键名＋DATA_DIR 隔离是判据的一部分，内联串在 bash
# 嵌套引号下会静默丢腿（批16 那次整块没跑起来，读数件不存在＝没测）。
# 键名从 butler/config.py 的启动硬门段现场 grep，⛔ 手抄：我这一批三次把同一个键名
# 拼缺一个字母，还有一次 sed 的通配把 ENV_PIN 那行两段文字吃掉——手抄的键名不可信。
# 用法：bash scripts/audit_1002/run_b18_acceptance_1002.sh <读数文件名>
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OUT=workorders/readings/1003b18
mkdir -p "$OUT"
NAME=${1:?用法：run_b18_acceptance_1002.sh <读数文件名>}
LOG="$OUT/$NAME.txt"
MOD=tests.test_audit_1002_batch18_silent_raise_paths
CFG=butler/config.py

GATE_FROM=300
GATE_TO=326
# 硬门段的键都是大写常量（*_KEY / *_TOKEN，外加网页口令键）；正则写宽了，
# 靠下面 count=4 那道闸兜住——漏一把或多一把都当场 rc=8，⛔ 静默少 export 一个键。
KEYS=$(sed -n "${GATE_FROM},${GATE_TO}p" "$CFG" \
       | grep -oE '[A-Z][A-Z0-9_]*_(API_)?(KEY|TOKEN)|BUTLER_WEB_PASSWORD' \
       | sort -u)
n=$(printf '%s\n' "$KEYS" | grep -c .)
if [ "$n" != "4" ]; then
  echo "GATE_KEYS_UNEXPECTED count=$n want=4 range=${CFG}:${GATE_FROM}-${GATE_TO}"
  printf '%s\n' "$KEYS"
  exit 8
fi
for k in $KEYS; do export "$k=DUMMY_NOT_REAL"; done
export DATA_DIR=/tmp/b18_qa_data
mkdir -p "$DATA_DIR"

{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "ENV_PIN gate_range=${CFG}:${GATE_FROM}-${GATE_TO} derived_count=$n names=$(printf '%s,' $KEYS | sed 's/,$//') values=DUMMY_NOT_REAL DATA_DIR=$DATA_DIR"
  echo "cmd: python3 -B -m unittest -v $MOD"
  md5sum tests/test_audit_1002_batch18_silent_raise_paths.py butler/core/dialog.py butler/tts/singleton.py
  echo "--- 生产码 GREEN 基线：dialog=1159d960069c597c97eee1642a0dff9e singleton=eb0e6e3053814066fa6586d4b5a97e79 ---"
} >"$LOG" 2>&1

start=$(date +%s)
python3 -B -m unittest -v "$MOD" >>"$LOG" 2>&1
rc=$?
{
  echo "unittest_rc=$rc wall=$(( $(date +%s) - start ))s"
  # 先把摘要取进变量再回写：⛔ 在 `>>"$LOG"` 的块里直接 grep "$LOG"（自己读自己写，
  # grep 会报 "input file is also the output" 并把这条腿变成空）。
  summary=$(grep -E '^Ran |^OK$|^FAILED' "$LOG")
  printf '%s\n' "$summary"
} >>"$LOG" 2>&1
cat "$LOG"
exit $rc
