#!/usr/bin/env bash
# 批16 文书腿第二件：把「落码前」的三处窗口按行号打出来（⛔ checkout 工作树，只走 git show → /tmp）。
# 用途＝证明报告坐标与现读坐标的漂移方向，并给 §17 一个能复跑的旧码窗口。
set -u
cd /vol1/1000/docker/doubao-butler || exit 9
OUT=workorders/readings/1003b16/ledger_evidence_prefix.txt
{
  echo "=== 落码前窗口（git show HEAD:<file> → /tmp，再用 print_lines_1002.py 打行号）==="
  date -u '+stamp_utc=%Y-%m-%dT%H:%M:%SZ'
  echo "HEAD|$(git rev-parse --short HEAD)"
} > "$OUT"

for pair in "butler/integrations/bark.py:118-133" "butler/integrations/bark.py:165-196" \
            "butler/integrations/memory_agent.py:300-320" "butler/core/aliases.py:28-36"; do
  f="${pair%%:*}"; rng="${pair#*:}"
  tmp="/tmp/b16_prefix_$(echo "$f" | tr '/' '_')"
  git show "HEAD:$f" > "$tmp" 2>&1
  rc=$?
  {
    echo ""
    echo "--- $f  HEAD 版窗口 $rng（tmp=$tmp，git_show_rc=$rc）---"
  } >> "$OUT"
  python3 -B scripts/audit_1002/print_lines_1002.py "$tmp" "$rng" >> "$OUT" 2>&1
  echo "print_rc=$?" >> "$OUT"
  rm -f "$tmp"
  echo "TMP_REMOVED|$tmp|exists_after=$([ -e "$tmp" ] && echo YES || echo no)" >> "$OUT"
done

{
  echo ""
  echo "=== 自量 ==="
  wc -lc "$OUT"
  python3 -c "import hashlib;b=open('$OUT','rb').read();print('MD5|%s|CR|%d'%(hashlib.md5(b).hexdigest(),b.count(b'\r')))"
} >> "$OUT"
echo "done -> $OUT"
