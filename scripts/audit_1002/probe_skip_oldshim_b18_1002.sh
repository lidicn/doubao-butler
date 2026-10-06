#!/usr/bin/env bash
# 批18 台账文书腿（第二探针）：skipped 9→3 不是 env（上一条探针 T2 已否），
# 剩下的候选是**我对 pytest 替身档的两枚补丁**（摘掉 test_trace_chain 的 ENV_ONLY 豁免 + 自己挂 vendor 路径）。
# 判别法：只换 tests/test_v25_pytest_shim.py 一个文件——旧版取自 `git show HEAD:`（⛔ 手写复刻、⛔ checkout 工作树），
# 各跑一遍 `-v` discover，比 skipped 计数并点名。跑完必须把当前版按 md5 还原回去。
# 用法：bash scripts/audit_1002/probe_skip_oldshim_b18_1002.sh
set -u
cd "$(dirname "$0")/../.." || exit 9
SHIM=tests/test_v25_pytest_shim.py
OUT=workorders/readings/1003b18/skip_oldshim_attribution_run2.txt
D=/tmp/b18_skip_probe2
mkdir -p "$D"
CUR_WANT=cf16a41ee784302cb1798e2b636f808c
DIALOG_WANT=1159d960069c597c97eee1642a0dff9e
SINGLETON_WANT=eb0e6e3053814066fa6586d4b5a97e79
export DATA_DIR=/tmp/b18_qa_data
mkdir -p "$DATA_DIR"

md5of() { md5sum "$1" | cut -d' ' -f1; }

{
  date -u '+STAMP %Y-%m-%dT%H:%M:%SZ'
  echo "跑前：shim=$(md5of "$SHIM") dialog=$(md5of butler/core/dialog.py) singleton=$(md5of butler/tts/singleton.py)"
  [ "$(md5of "$SHIM")" = "$CUR_WANT" ] || { echo "ABORT|当前 shim md5 不是 $CUR_WANT"; exit 7; }
  [ "$(md5of butler/core/dialog.py)" = "$DIALOG_WANT" ] || { echo "ABORT|dialog 非基线"; exit 7; }
  [ "$(md5of butler/tts/singleton.py)" = "$SINGLETON_WANT" ] || { echo "ABORT|singleton 非基线"; exit 7; }
  cp -p "$SHIM" "$D/shim_current.py"

  git show HEAD:tests/test_v25_pytest_shim.py >"$D/shim_head.py" 2>"$D/gitshow.err"
  gitrc=$?
  echo "git_show_rc=$gitrc stderr_bytes=$(wc -c <"$D/gitshow.err") head_md5=$(md5of "$D/shim_head.py") lines=$(wc -l <"$D/shim_head.py")"
  if [ "$gitrc" != "0" ] || [ ! -s "$D/shim_head.py" ]; then echo "ABORT|取不到 HEAD 版 shim"; exit 7; fi

  echo
  echo "=== [T3] 换上 HEAD（批17 时代）那份 shim，跑 -v discover ==="
  cp "$D/shim_head.py" "$SHIM"
  echo "  换后 shim=$(md5of "$SHIM")"
  python3 -B -m unittest discover -v -s tests -t . >"$D/t3.txt" 2>&1
  echo "  T3_rc=$? ran_lines=$(grep -c '^Ran ' "$D/t3.txt") usage_error_lines=$(grep -c '^usage:' "$D/t3.txt")"
  grep -E '^Ran |^FAILED|^OK' "$D/t3.txt" | sed 's/^/    /'
  echo "  T3 点名（skipped 行，⛔ 缩略）："
  grep -iE '\.\.\. skipped' "$D/t3.txt" | sed 's/^/    /'
  echo "  T3_skipped=$(grep -oE 'skipped=[0-9]+' "$D/t3.txt" | grep -oE '[0-9]+' | head -1) skip_named=$(grep -ciE '\.\.\. skipped' "$D/t3.txt")"

  echo
  echo "=== [T4] 还原当前版 shim，同样 -v 再跑一遍 ==="
  cp "$D/shim_current.py" "$SHIM"
  echo "  还原后 shim=$(md5of "$SHIM") want=$CUR_WANT"
  if [ "$(md5of "$SHIM")" = "$CUR_WANT" ]; then echo "  RESTORE_OK"; else echo "  RESTORE_BAD|⛔ 继续"; fi
  python3 -B -m unittest discover -v -s tests -t . >"$D/t4.txt" 2>&1
  echo "  T4_rc=$? ran_lines=$(grep -c '^Ran ' "$D/t4.txt") usage_error_lines=$(grep -c '^usage:' "$D/t4.txt")"
  grep -E '^Ran |^FAILED|^OK' "$D/t4.txt" | sed 's/^/    /'
  echo "  T4 点名："
  grep -iE '\.\.\. skipped' "$D/t4.txt" | sed 's/^/    /'
  echo "  T4_skipped=$(grep -oE 'skipped=[0-9]+' "$D/t4.txt" | grep -oE '[0-9]+' | head -1) skip_named=$(grep -ciE '\.\.\. skipped' "$D/t4.txt")"

  echo
  echo "=== 判定 ==="
  # 分母闸：任一遍没打出 `Ran N` 行＝那一遍根本没跑（上一版就是 `-v` 放在 `discover` 之前，
  # CLI 报 usage、rc=2、skipped 计数取不到 ⇒ "没测" 会被读成 "0 跳过"）。
  for t in t3 t4; do
    if ! grep -q '^Ran ' "$D/$t.txt"; then echo "ABORT_DENOM|$D/$t.txt 无 Ran 行，本探针作废"; exit 6; fi
  done
  s3=$(grep -oE 'skipped=[0-9]+' "$D/t3.txt" | grep -oE '[0-9]+' | head -1)
  s4=$(grep -oE 'skipped=[0-9]+' "$D/t4.txt" | grep -oE '[0-9]+' | head -1)
  echo "shim=HEAD → skipped=${s3:-none} / shim=当前 → skipped=${s4:-none}"
  if [ "${s3:-x}" = "9" ] && [ "${s4:-x}" = "3" ]; then
    echo "VERDICT|归因成立：这 6 枚的跳/不跳由我对 shim 的两枚补丁决定（摘 ENV_ONLY 豁免），⛔ 生产码改动所致"
  else
    echo "VERDICT|单换 shim 复现不了 9→3（HEAD=${s3:-none} 当前=${s4:-none}）⇒ 台账写「未归因」并开单"
  fi
  echo "差集（哪些 skipped 名字只在 HEAD 版出现）："
  TMPO=$D/n3.txt; TMPN=$D/n4.txt
  grep -iE '\.\.\. skipped' "$D/t3.txt" | sed 's/ \.\.\..*//' | sort -u >"$TMPO"
  grep -iE '\.\.\. skipped' "$D/t4.txt" | sed 's/ \.\.\..*//' | sort -u >"$TMPN"
  comm -23 "$TMPO" "$TMPN" | sed 's/^/    /'
  echo "  only_in_HEAD_named=$(comm -23 "$TMPO" "$TMPN" | grep -c .)"
  echo
  echo "跑后（必须与跑前一致）：shim=$(md5of "$SHIM") dialog=$(md5of butler/core/dialog.py) singleton=$(md5of butler/tts/singleton.py)"
  echo "git status（本探针⛔ 留下未声明的改动）："
  git --no-optional-locks status --porcelain -- butler tests | sed 's/^/    /'
} >"$OUT" 2>&1
rc=$?
echo "probe2_rc=$rc out=$OUT ($(wc -c <"$OUT") B)"
cat "$OUT"
exit $rc
