#!/usr/bin/env bash
# 批16 文书腿：把 §17 要引的两类读数一次跑齐并落盘（量尺脚本 + 生效面 docker 现读）。
# 每条腿单独取 rc（⛔ 把管道末端 tail/head/grep 的 rc 当尺子的 rc）；容器侧一律现读，⛔ 用上一次的结论。
set -u
cd /vol1/1000/docker/doubao-butler || exit 9
OUT=workorders/readings/1003b16/ledger_evidence.txt
mkdir -p workorders/readings/1003b16

{
  echo "=== 批16 文书腿量尺（台账 §17 的每个行号／计数出自本件）==="
  date -u '+stamp_utc=%Y-%m-%dT%H:%M:%SZ'   # 格式必须以 + 开头，否则 date 把它当「日期字符串」→ invalid date
  echo "cmd: python3 -B scripts/audit_1002/probe_b16_ledger_1002.py"
} > "$OUT"

python3 -B scripts/audit_1002/probe_b16_ledger_1002.py >> "$OUT" 2>&1
echo "probe_rc=$?" >> "$OUT"

{
  echo ""
  echo "=== 生效面现读（⚠ 这一段的判读规则见台账 §17：bind mount ⇒ 盘上已是新码；进程侧只认 StartedAt）==="
  echo "cmd: docker inspect doubao-butler --format '{{json .Mounts}}'"
} >> "$OUT"
docker inspect doubao-butler --format '{{json .Mounts}}' >> "$OUT" 2>&1
echo "inspect_mounts_rc=$?" >> "$OUT"

echo "cmd: docker inspect doubao-butler --format 'Created=... StartedAt=... RestartCount=...'" >> "$OUT"
docker inspect doubao-butler --format 'Created={{.Created}} State.StartedAt={{.State.StartedAt}} RestartCount={{.RestartCount}} Image={{.Config.Image}}' >> "$OUT" 2>&1
echo "inspect_state_rc=$?" >> "$OUT"

DIFFTMP=$(mktemp)
docker diff doubao-butler > "$DIFFTMP" 2>&1
DIFFRC=$?
{
  echo "cmd: docker diff doubao-butler   （diff 本体先落 tmp 再数，⛔ 用管道末端的 rc）"
  echo "diff_rc=$DIFFRC diff_lines=$(wc -l < "$DIFFTMP") diff_C_lines=$(grep -c '^C ' "$DIFFTMP" || true)"
  grep '^C ' "$DIFFTMP" | head -5
} >> "$OUT"
rm -f "$DIFFTMP"

echo "cmd: docker exec doubao-butler grep -c '<symbol>' /app/butler/...  （读的是挂载盘，⛔ 活进程）" >> "$OUT"
for pair in "butler/integrations/bark.py:_encrypt_url" "butler/integrations/memory_agent.py:alive" "butler/core/aliases.py:os.replace"; do
  f="${pair%%:*}"; sym="${pair#*:}"
  n=$(docker exec doubao-butler grep -c "$sym" "/app/$f" 2>/dev/null); rc=$?
  echo "  CONTAINER_GREP|$f|$sym|count=$n|rc=$rc" >> "$OUT"
done

{
  echo ""
  echo "=== 读数件自量 ==="
  wc -lc "$OUT"
  python3 -c "import hashlib,sys;b=open('$OUT','rb').read();print('MD5|%s|CR|%d'%(hashlib.md5(b).hexdigest(),b.count(b'\r')))"
} >> "$OUT"
echo "done -> $OUT"
