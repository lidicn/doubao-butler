#!/usr/bin/env bash
# 把 scripts/git-hooks/ 下的钩子装进本仓 .git/hooks/（批208）。
# 为什么还要一道「装」：钩子本体⛔ 被 git 跟踪（.git 不入库），所以每个 clone 都要跑一次本脚本。
# 幂等：已存在且内容相同→跳过；内容不同→备份成 <hook>.bute-before-<epoch> 再覆盖。
set -uo pipefail
REPO="$(git rev-parse --show-toplevel)"
SRC="$REPO/scripts/git-hooks"
DST="$REPO/.git/hooks"
mkdir -p "$DST"
changed=0
for f in "$SRC"/*; do
  [ -f "$f" ] || continue
  name="$(basename "$f")"
  case "$name" in install-hooks.sh) continue ;; esac
  if [ -f "$DST/$name" ] && cmp -s "$f" "$DST/$name"; then
    echo "SKIP: $name 已在位且同内容"
    continue
  fi
  [ -f "$DST/$name" ] && cp "$DST/$name" "$DST/$name.bute-$(date +%s)"
  cp "$f" "$DST/$name"
  chmod +x "$DST/$name" "$f"
  echo "OK: 装上 $name"
  changed=$((changed+1))
done
echo "[result] 新装/更新 $changed 枚；核对：git config core.hooksPath 应为空（用默认 .git/hooks）"
exit 0
