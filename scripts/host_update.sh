#!/bin/bash
# 批197（WebUI 自更新）宿主侧「执行」腿：看标记 ⇒ 落码 ⇒ 按 ref 决定⛔/✅ 重启。
#
# 三道拒执（与容器侧 butler/update_gate.py 同一把尺，⛔ 两套口径）：
#   1) 自更新未启用；2) 有未收编的本地改动（执行时刻现数，⛔ 抄 5 分钟前那份预览）；
#   3) 目标不是当前码的后代（非快进＝会覆盖本地历史）。
# 重启只在版本 tag（owner 2026-10-11 裁定「日常 main 更新只落码不重启，打 tag 才允许自动重启」）。
#
# crontab（owner 逐次点头后才装，本批不自装）：
#   * * * * * /vol1/1000/docker/doubao-butler/scripts/host_update.sh >> /tmp/db_update.log 2>&1
set -uo pipefail

REPO_DIR="${DB_UPDATE_REPO_DIR:-/vol1/1000/docker/doubao-butler}"
DATA_DIR="${DB_UPDATE_DATA_DIR:-$REPO_DIR/data}"
PY="${DB_UPDATE_PY:-python3}"
CONTAINER="${DB_UPDATE_CONTAINER:-doubao-butler}"
MARKER="$DATA_DIR/.update_request.json"
RESULT="$DATA_DIR/.update_result.json"
LOCK="$DATA_DIR/.update.lock.flock"
LOG="$DATA_DIR/update.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

get_key() {
    "$PY" - "$REPO_DIR/.env" "$1" "$2" <<'PY'
import pathlib
import sys

path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
value = ""
p = pathlib.Path(path)
if p.exists():
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, val = line.split("=", 1)
        if name.strip() == key:
            value = val.strip().strip('"').strip("'")
            break
print(value or default)
PY
}

write_result() {  # $1=ok(true/false) $2=action $3=error
    export _R_OK="$1" _R_ACTION="$2" _R_ERR="$3"
    export _R_FROM="$FROM_SHA" _R_FROM_TAG="$FROM_TAG" _R_TO="$TO_SHA" _R_TO_TAG="$TO_TAG"
    export _R_CHANGELOG="$CHANGELOG" _R_RESULT="$RESULT"
    "$PY" <<'PY' >> "$LOG" 2>&1
import json
import os
import time

raw = os.environ.get("_R_CHANGELOG", "[]").strip()
try:
    changelog = json.loads(raw)
except ValueError:
    changelog = [line.strip() for line in raw.splitlines() if line.strip()]
payload = {
    "ok": os.environ.get("_R_OK") == "true",
    "action": os.environ.get("_R_ACTION", "refuse"),
    "from": os.environ.get("_R_FROM", "unknown"),
    "from_tag": os.environ.get("_R_FROM_TAG", "unknown"),
    "to": os.environ.get("_R_TO", "unknown"),
    "to_tag": os.environ.get("_R_TO_TAG", "unknown"),
    "error": os.environ.get("_R_ERR", ""),
    "changelog": changelog,
    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
tmp = os.environ["_R_RESULT"] + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
os.replace(tmp, os.environ["_R_RESULT"])
PY
}

refuse_and_exit() {  # $1=error —— 拒执也要留字面原因，容器 status 才报得出来
    TO_SHA="$FROM_SHA"; TO_TAG="$FROM_TAG"
    log "REFUSE: $1"
    write_result false refuse "$1"
    rm -f "$MARKER"
    exit 1
}

[ -f "$MARKER" ] || exit 0

if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK" || exit 0
    flock -n 9 || { log "上一轮还在跑，本轮跳过"; exit 0; }
fi

log "=== 收到更新请求 $MARKER ==="
# 先给结果件字段兜底（`set -u`：进不了目录那条腿也要写得出 result）
FROM_SHA="unknown"; FROM_TAG="unknown"; TO_SHA="unknown"; TO_TAG="unknown"; CHANGELOG="[]"
cd "$REPO_DIR" || { write_result false refuse "进不了 $REPO_DIR"; exit 1; }

ENABLED="$(get_key SELF_UPDATE_ENABLED 'false')"
REPO_URL="$(get_key UPDATE_REPO_URL 'https://github.com/lidicn/doubao-butler.git')"
DEFAULT_BRANCH="$(get_key UPDATE_BRANCH 'main')"

FROM_SHA="$(git rev-parse HEAD 2>/dev/null || echo 'unknown')"
FROM_TAG="$(git describe --tags --always 2>/dev/null || echo "$FROM_SHA")"
TO_SHA="$FROM_SHA"
TO_TAG="$FROM_TAG"
CHANGELOG="[]"

REF="$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1])).get('ref','') or sys.argv[2])" \
       "$MARKER" "$DEFAULT_BRANCH" 2>/dev/null || echo "$DEFAULT_BRANCH")"
log "目标 ref=$REF url=$REPO_URL 当前=${FROM_SHA:0:12}"

[ "$ENABLED" = "true" ] || refuse_and_exit "自更新未启用（SELF_UPDATE_ENABLED=false）"

DIRTY="$(git status --porcelain --untracked-files=no 2>/dev/null | grep -c . || true)"
if [ "${DIRTY:-0}" -gt 0 ]; then
    refuse_and_exit "有未收编的本地改动（$DIRTY 枚跟踪文件被改）"
fi

if ! git fetch --force "$REPO_URL" "$REF" >> "$LOG" 2>&1; then
    refuse_and_exit "git fetch 失败（网络或发布源不可达），未动码"
fi
TO_SHA="$(git rev-parse FETCH_HEAD 2>/dev/null || echo 'unknown')"

if ! git merge-base --is-ancestor "$FROM_SHA" "$TO_SHA" 2>/dev/null; then
    refuse_and_exit "目标不是当前码的后代（⛔ 覆盖：先把本地历史收编或推到发布源）"
fi
if [ "$FROM_SHA" = "$TO_SHA" ]; then
    refuse_and_exit "已是最新（${FROM_SHA:0:12}），无落码必要"
fi

# 备份成员按「在不在」现取：⛔ 因为树里没有 scripts/ 就整个拒执，也⛔ 一份空备份当「有备份」
MEMBERS=""
for m in butler docker-compose.yml scripts; do
    [ -e "$REPO_DIR/$m" ] && MEMBERS="$MEMBERS $m"
done
if [ -z "$MEMBERS" ]; then
    refuse_and_exit "备份目标为空（butler/ docker-compose.yml scripts/ 都不在 $REPO_DIR）"
fi
BACKUP="$DATA_DIR/update-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
if ! tar czf "$BACKUP" -C "$REPO_DIR" --exclude='.git' --exclude='data' --exclude='workorders' \
        --exclude='tts' $MEMBERS >> "$LOG" 2>&1; then
    refuse_and_exit "备份失败（$BACKUP），未动码"
fi
log "备份: $BACKUP"

CHANGELOG="$(git log --oneline "$FROM_SHA..$TO_SHA" --no-merges 2>/dev/null | head -20 | \
    "$PY" -c "import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()], ensure_ascii=False))" \
    2>/dev/null || echo '[]')"

if ! git reset --hard "$TO_SHA" >> "$LOG" 2>&1; then
    refuse_and_exit "git reset 失败（工作树可能被占用），已停在 ${FROM_SHA:0:12}"
fi
TO_TAG="$(git describe --tags --always 2>/dev/null || echo "$TO_SHA")"

if ! "$PY" -m compileall -q butler >> "$LOG" 2>&1; then
    log "ERROR: 语法检查失败，回滚到 ${FROM_SHA:0:12}"
    git reset --hard "$FROM_SHA" >> "$LOG" 2>&1
    write_result false refuse "语法检查失败，已自动回滚到 ${FROM_SHA:0:12}"
    rm -f "$MARKER"
    exit 1
fi
log "py_compile 通过"

# ⛔ 重启 vs 重启：日常 main 只落码；只有 v<主>.<次> 形态的版本 tag 才自动重启
if printf '%s' "$REF" | grep -qE '^v[0-9]+\.[0-9]'; then
    ACTION="apply_and_restart"
    write_result true "$ACTION" ""
    rm -f "$MARKER"
    log "=== 版本 tag（$REF）⇒ 落码并重启 ==="
    sleep 2
    docker restart "$CONTAINER" >> "$LOG" 2>&1 || log "ERROR: docker restart 失败，需人工"
else
    ACTION="apply_only"
    write_result true "$ACTION" ""
    rm -f "$MARKER"
    log "=== 日常更新（$REF）⇒ 只落码，⛔ 自动重启（重启只在版本 tag）；生效等下一次 restart ==="
fi
