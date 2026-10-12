#!/bin/bash
set -e
cd /vol1/1000/docker/doubao-butler

echo "=== 1. 全树 Python 语法检查 ==="
python3 -c "
import pathlib, sys
errors = []
count = 0
for p in sorted(pathlib.Path('butler').rglob('*.py')):
    if '__pycache__' in str(p) or 'graphify-out' in str(p):
        continue
    count += 1
    try:
        compile(p.read_bytes(), str(p), 'exec')
    except (SyntaxError, IndentationError) as e:
        errors.append(f'{p}:{e.lineno}: {e.msg}')
if errors:
    for e in errors:
        print(f'FAIL: {e}')
    sys.exit(1)
print(f'OK: {count} files parsed, 0 errors')
"

echo ""
echo "=== 2. 检查子串误判 ==="
COUNT=$(grep -rn '"ok" in r\b\|"ok" in result\b\|"ok" in res\b\|"ok" in out\b\|"success" in result\b\|"success" in r\b' butler/ --include="*.py" 2>/dev/null | grep -v __pycache__ | wc -l)
if [ "$COUNT" -gt 0 ]; then
    echo "FAIL: $COUNT 处子串误判："
    grep -rn '"ok" in r\b\|"ok" in result\b\|"ok" in res\b\|"ok" in out\b\|"success" in result\b\|"success" in r\b' butler/ --include="*.py" | grep -v __pycache__
    exit 1
else
    echo "OK: 无子串误判"
fi

echo ""
echo "=== 3. 检查死副本 ==="
DEAD=0
for f in butler/runner.py butler/role_routes.py butler/agent.py; do
    if [ -f "$f" ]; then
        echo "FAIL: 死副本 $f 仍存在"
        DEAD=1
    fi
done
if [ "$DEAD" -ne 0 ]; then
    exit 1
fi
echo "OK: 无死副本"

echo ""
echo "=== 4. 检查写面存活（P0 2026-09-30） ==="
# health 200 / 容器 healthy / RestartCount=0 / 日志窗无 Traceback 这四格，在写面停摆 3 小时 01 分的全过程里全是绿灯。
# 这条腿只看一个读数：最后一笔写入离现在多远。判据＝全部 4 张表都停才红。
if [ "${ALLOW_DEPLOY_WITH_STALLED_WRITES:-}" = "1" ]; then
    echo "OVERRIDE: 已显式设定 ALLOW_DEPLOY_WITH_STALLED_WRITES=1 ⇒ 跳过写面腿（本次部署自负）"
elif python3 scripts/db_write_liveness_probe.py --db data/butler.db --max-age 900; then
    echo "OK: 写面在收写"
else
    RC=$?
    echo "FAIL: 写面腿 rc=$RC ⇒ ⛔ 本窗部署（新写入照样进不去，且部署会把主卧 24h 窗的起算点再次推后）"
    echo "      确需在停摆状态下部署：export ALLOW_DEPLOY_WITH_STALLED_WRITES=1 再跑本脚本"
    exit 1
fi

echo ""
echo "=== 检查通过 ==="
