#!/usr/bin/env bash
# 批19 验收件单跑：只跑 tests/test_audit_1002_batch19_db_getconn_race.py。
# 形制照批18 的 runner：env 键名现场从 butler/config.py 的启动硬门段 grep＋count=4 闸（⛔ 手抄，
# 批18 我三次把键名拼缺字母）；内联串在 bash 嵌套引号下会静默丢腿⇒ 落成脚本。
# DATA_DIR 钉 /tmp＝判据的一部分（⛔ 碰现网 WAL 库），并额外用现网库文件 mtime 跑前跑后同值反证一次。
# 用法：bash scripts/audit_1002/run_b19_acceptance_1002.sh <读数文件名>
set -u
cd "$(git rev-parse --show-toplevel)" || exit 9
OUT=workorders/readings/1003b19
mkdir -p "$OUT"
NAME=${1:?用法：run_b19_acceptance_1002.sh <读数文件名>}
LOG="$OUT/$NAME.txt"
MOD=tests.test_audit_1002_batch19_db_getconn_race
TEST=tests/test_audit_1002_batch19_db_getconn_race.py
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
export DATA_DIR=/tmp/b19_qa_data
mkdir -p "$DATA_DIR"

BEFORE=$(stat -c '%Y %s' "$LIVE_DB" 2>/dev/null || echo NO_LIVE_DB)

{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "ENV_PIN gate_range=${CFG}:${GATE_FROM}-${GATE_TO} derived_count=$n names=$(printf '%s,' $KEYS | sed 's/,$//') values=DUMMY_NOT_REAL DATA_DIR=$DATA_DIR"
  echo "cmd: python3 -B -m unittest -v $MOD"
  md5sum "$TEST" butler/store/db.py
  echo "HEAD=$(git rev-parse --short HEAD)"
  echo "LIVE_DB_BEFORE=$BEFORE"
} >"$LOG" 2>&1

start=$(date +%s)
python3 -B -m unittest -v "$MOD" >>"$LOG" 2>&1
rc=$?
echo "unittest_rc=$rc wall=$(( $(date +%s) - start ))s" >>"$LOG" 2>&1

{
  echo "--- 分母三数（AST 现读⪰collect⪰ran，⛔ 把「没跑」读成「绿」）---"
  python3 -B - "$TEST" <<'PY'
import ast, sys, pathlib
t = ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
methods, module_level, classes = [], [], []
for c in ast.walk(t):
    if isinstance(c, ast.ClassDef):
        classes.append(c.name)
        for m in c.body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test_"):
                methods.append("%s.%s" % (c.name, m.name))
for m in t.body:
    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test_"):
        module_level.append(m.name)
print("ast_classes=%d ast_methods=%d ast_module_level=%d ast_total=%d"
      % (len(classes), len(methods), len(module_level), len(methods) + len(module_level)))
print("ast_names=" + ",".join(sorted(methods)))
if module_level:
    print("⛔ 模块级 test_ 函数在场：discover 不 import pytest 时会静默收下＝零执行不报错")
PY
  grep -E '^Ran |^OK|^FAILED' "$LOG" | sed 's/^/summary|/'
  echo "ran_lines=$(grep -c '^Ran ' "$LOG")"
  echo "--- 逐腿结局（RED 阶段要能逐枚对上我要修的那件事）---"
  grep -E '\.\.\. (ok|FAIL|ERROR|skipped)' "$LOG" | sed 's/^/  LEG|/'
  echo "--- 失败原因（断言消息原样，⛔ 缩略）---"
  # run1 教训：`sed -n '/^===* FAIL/,/^Ran /p'` 0 命中＝unittest 的 `=====` 与 `FAIL:` 在**两行**上，
  # 同一行模式永远匹配不到⇒ 空区块会被读成「没有失败原因」。改按块切，取每块的非缩进尾部。
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
