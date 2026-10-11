#!/bin/bash
# 批197（WebUI 自更新）宿主侧「检查」腿：fetch 发布源 ⇒ 写预览件给容器读。
#
# 为什么在宿主而不在容器（现读拓扑 `workorders/readings/1011/b197_topology_nas.txt`，2026-10-11T03:33:08Z）：
# 容器挂载面只有 butler/ data/ tts/，`/app` 里⛔ `.git`，容器内 `which git` 为空⇒取码/快进判定只能在宿主。
# 本脚本⛔ 改码、⛔ 重启、⛔ 删标记——它只写 `.update_available.json`；落码腿在 host_update.sh。
#
# crontab（owner 逐次点头后才装，本批不自装）：
#   */5 * * * * /vol1/1000/docker/doubao-butler/scripts/host_check_update.sh >> /tmp/db_update.log 2>&1
#
# 发布源＝.env 里的 UPDATE_REPO_URL（默认＝现在的公开 GitHub 仓）；本仓⛔ 私有源⇒⛔ 不需要 ssh 密钥，
# 若将来换私有源，密钥由 owner 自己在宿主配（平台凭据⛔ 座位代改）。
set -uo pipefail

# 三枚环境变量只给「本地合成仓演练」用（批198 的门就这么跑）；现网⛔ 设它们＝默认值即部署树路径。
REPO_DIR="${DB_UPDATE_REPO_DIR:-/vol1/1000/docker/doubao-butler}"
DATA_DIR="${DB_UPDATE_DATA_DIR:-$REPO_DIR/data}"
PY="${DB_UPDATE_PY:-python3}"
PREVIEW="$DATA_DIR/.update_available.json"
LOG="$DATA_DIR/update.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# 只取这三枚键；⛔ 打印 .env 的其它行（里面有口令）。空值回默认，与 butler/config.py 同口径。
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

REPO_URL="$(get_key UPDATE_REPO_URL 'https://github.com/lidicn/doubao-butler.git')"
BRANCH="$(get_key UPDATE_BRANCH 'main')"

cd "$REPO_DIR" || { log "ERROR: 进不了 $REPO_DIR"; exit 1; }

LOCAL="$(git rev-parse HEAD 2>/dev/null || echo '')"
# 脏＝跟踪文件被改（⛔ 数未跟踪件：workorders/ data/ 常年未跟踪，不是「未收编的本地改动」）
DIRTY="$(git status --porcelain --untracked-files=no 2>/dev/null | grep -c . || true)"

REMOTE=""
FETCH_ERR=""
if git fetch --quiet --force "$REPO_URL" "$BRANCH" 2>>"$LOG"; then
    REMOTE="$(git rev-parse FETCH_HEAD 2>/dev/null || echo '')"
else
    FETCH_ERR="git fetch 失败（网络或发布源不可达）"
    log "ERROR: $FETCH_ERR url=$REPO_URL"
fi

FAST_FORWARD="false"
BEHIND=0
HAS_UPDATE="false"
CHANGELOG=""
if [ -n "$REMOTE" ] && [ -n "$LOCAL" ]; then
    if git merge-base --is-ancestor "$LOCAL" "$REMOTE" 2>/dev/null; then
        FAST_FORWARD="true"
    fi
    BEHIND="$(git rev-list --count "$LOCAL..$REMOTE" 2>/dev/null || echo 0)"
    if [ "$LOCAL" != "$REMOTE" ] && [ "$FAST_FORWARD" = "true" ]; then
        HAS_UPDATE="true"
        CHANGELOG="$(git log --oneline "$LOCAL..$REMOTE" --no-merges 2>/dev/null | head -15 || true)"
    fi
fi

log "check: local=${LOCAL:0:12} remote=${REMOTE:0:12} behind=$BEHIND dirty=$DIRTY ff=$FAST_FORWARD"

export _P_PREVIEW="$PREVIEW" _P_HAS="$HAS_UPDATE" _P_LOCAL="$LOCAL" _P_REMOTE="$REMOTE"
export _P_BEHIND="$BEHIND" _P_DIRTY="$DIRTY" _P_FF="$FAST_FORWARD" _P_BRANCH="$BRANCH"
export _P_URL="$REPO_URL" _P_FETCH_ERR="$FETCH_ERR" _P_CHANGELOG="$CHANGELOG"
"$PY" <<'PY'
import datetime
import json
import os

raw = os.environ.get("_P_CHANGELOG", "").strip()
preview = {
    "has_update": os.environ.get("_P_HAS") == "true",
    "ref": os.environ.get("_P_BRANCH", "main"),
    "repo_url": os.environ.get("_P_URL", ""),
    "local_commit": (os.environ.get("_P_LOCAL", "") or "")[:12],
    "remote_commit": (os.environ.get("_P_REMOTE", "") or "")[:12],
    "behind": int(os.environ.get("_P_BEHIND", "0") or 0),
    "dirty": int(os.environ.get("_P_DIRTY", "0") or 0),
    "fast_forward": os.environ.get("_P_FF") == "true",
    "fetch_error": os.environ.get("_P_FETCH_ERR", ""),
    "changelog": [line.strip() for line in raw.splitlines() if line.strip()],
    "checked_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
target = os.environ["_P_PREVIEW"]
tmp = target + ".tmp"
# 先写临时件再改名：容器⛔ 读到半截 JSON（与 butler/api/update_routes.py 的标记腿同形制）
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(preview, fh, ensure_ascii=False, indent=2)
os.replace(tmp, target)
print("[preview] has_update=%s behind=%s dirty=%s ff=%s" % (
    preview["has_update"], preview["behind"], preview["dirty"], preview["fast_forward"]))
PY
rc=$?
[ "$rc" -ne 0 ] && log "ERROR: 写预览件失败 rc=$rc"
exit "$rc"
