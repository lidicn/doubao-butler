#!/usr/bin/env bash
# 批20 验收件单跑：只跑 tests/test_audit_1002_batch20_async_outcome_silent_loss.py。
# 形制照批19 的 runner：env 键名现场从 butler/config.py 的启动硬门段 grep＋count=4 闸（⛔ 手抄），
# DATA_DIR 钉 /tmp＝判据的一部分（⛔ 碰现网 WAL 库），并用现网库文件 mtime 跑前跑后同值反证一次。
# 本批改四枚生产文件，md5sum 一并把这四枚的基底钉进读数件。
# 用法：bash scripts/audit_1002/run_b20_acceptance_1002.sh <读数文件名>
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OUT=workorders/readings/1003b20
mkdir -p "$OUT"
NAME=${1:?用法：run_b20_acceptance_1002.sh <读数文件名>}
LOG="$OUT/$NAME.txt"
MOD=tests.test_audit_1002_batch20_async_outcome_silent_loss
TEST=tests/test_audit_1002_batch20_async_outcome_silent_loss.py
TARGETS="butler/bus/mqtt_client.py butler/triggers/registry.py butler/app.py butler/runtime.py"
CFG=butler/config.py
GATE_FROM=300
GATE_TO=326
LIVE_DB=/vol1/1000/docker/doubao-butler/data/butler.db

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
export DATA_DIR=/tmp/b20_qa_data
# 结构腿读的那枚 app.py 由跑尺件**显式指路**（⛔ 让测试从 import 结果反推＝同义反复）
export B20_APP_PY="$PWD/butler/app.py"
mkdir -p "$DATA_DIR"

BEFORE=$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null || echo NO_LIVE_DB)

{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "ENV_PIN gate_range=${CFG}:${GATE_FROM}-${GATE_TO} derived_count=$n names=$(printf '%s,' $KEYS | sed 's/,$//') values=DUMMY_NOT_REAL DATA_DIR=$DATA_DIR"
  echo "cmd: python3 -B -m unittest -v $MOD"
  md5sum "$TEST" $TARGETS
  echo "HEAD=$(git rev-parse --short HEAD)"
  echo "LIVE_DB_BEFORE=$BEFORE"
} >"$LOG" 2>&1

start=$(date +%s)
python3 -B -m unittest -v "$MOD" >>"$LOG" 2>&1
rc=$?
echo "unittest_rc=$rc wall=$(( $(date +%s) - start ))s" >>"$LOG" 2>&1

{
  echo "--- 分母三数（AST 现读⪰collect⪰ran，⛔ 把「没跑」读成「绿」）---"
  python3 -B "$PWD/scripts/audit_1002/b20_ast_denominator.py" "$TEST"
  grep -E '^Ran |^OK|^FAILED' "$LOG" | sed 's/^/summary|/'
  echo "ran_lines=$(grep -c '^Ran ' "$LOG")"
  echo "--- 逐腿结局（RED 阶段要能逐枚对上我要修的那件事）---"
  grep -E '\.\.\. (ok|FAIL|ERROR|skipped)' "$LOG" | sed 's/^/  LEG|/'
  echo "--- 失败原因（断言消息原样，⛔ 缩略）---"
  python3 -B - "$LOG" <<'PY'
import pathlib, re, sys
txt = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
blocks = [b for b in re.split(r"^={10,}$", txt, flags=re.M)
          if re.search(r"^(FAIL|ERROR): ", b, flags=re.M)]
print("why_blocks=%d" % len(blocks))
for b in blocks:
    head = re.search(r"^(FAIL|ERROR): (\S+)", b, flags=re.M)
    tail = re.split(r"^-{10,}$", b, maxsplit=1, flags=re.M)[-1]
    lines, idx = tail.splitlines(), None
    for i, l in enumerate(lines):
        if l == "" or l.startswith("  ") or l.startswith("Traceback"):
            continue
        idx = i
        break
    print("  WHY|%s" % head.group(0))
    for l in (lines[idx:] if idx is not None else ["<无消息行>"]):
        if re.match(r"^(-{10,}|Ran |OK$|FAILED)", l):
            break
        print("  WHY|  %s" % l)
PY
  echo "--- 现网库反证：跑后 mtime/字节 应与跑前同号（本跑只写 /tmp）---"
  echo "LIVE_DB_AFTER=$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null || echo NO_LIVE_DB)"
  echo "LIVE_DB_SAME=$([ "$BEFORE" = "$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null)" ] && echo YES || echo 'NO(现网有真流量，非本跑所致＝要人判)')"
  echo "tmp_data_dir_files=$(ls -1 "$DATA_DIR" 2>/dev/null | tr '\n' ' ')"
} >>"$LOG" 2>&1

echo "rc=$rc log=$LOG bytes=$(wc -c <"$LOG")"
cat "$LOG"
