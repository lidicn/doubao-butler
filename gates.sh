#!/usr/bin/env bash
# 质量门禁本地入口 —— 复制到仓库根，`chmod +x gates.sh`
#
# 适用：AutoForge、doubao-butler（没有 GitHub remote，CI 无从挂起）。
# 有 remote 的两家请用 templates/ci/gates.yml。
#
# 退出码就是结论：0 过 / 1 有未获批违规 / 2 环境或配置不对。
# 验收单里贴这条命令的**完整输出**，不接受「跑过了」。
set -uo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
# 冒烟解释器：本地跑就用自己的 venv；要在容器里跑，改成
#   docker exec <容器> sh -c 'cd /app && homesdk-gates . --config .gates.toml'
PYTHON="${GATES_PYTHON:-python3}"

if ! "$PYTHON" -c "import homesdk.gates" 2>/dev/null; then
  echo "homesdk 未安装。先执行："
  echo "  $PYTHON -m pip install -e E:/NAS/homesdk        # 开发机"
  echo "  # 或 pip install /vol1/1000/docker/libs/homesdk/dist/homesdk-0.1.0-py3-none-any.whl"
  exit 2
fi

if [ ! -f "$REPO/.gates.toml" ]; then
  echo "缺少 $REPO/.gates.toml —— 从 E:/NAS/AgentOps/gates/<仓名>.gates.toml 复制一份再改名。"
  exit 2
fi

echo "══ AST 门禁（不含冒烟）═══════════════════════════════════════"
"$PYTHON" -m homesdk.gates "$REPO" --no-smoke
ast_rc=$?

echo
echo "══ import 冒烟（解释器：$("$PYTHON" -V 2>&1)）════════════════════"
# 单跑冒烟：只走 `import` 子进程，慢但一次性看清。
# 注意：这条在开发机上的红多半是「依赖没装齐 / 本地副本不完整」，
#       结论以容器内跑出来的为准（见 README 第三节）。
"$PYTHON" -m homesdk.gates "$REPO" --only-smoke 2>&1 | sed 's/^/  /'
smoke_rc=${PIPESTATUS[0]}

echo
if [ $ast_rc -ne 0 ]; then
  echo "结论：AST 门禁红（exit=$ast_rc）。修，或在 .gates-baseline.txt 里逐条写明放行理由。"
  exit $ast_rc
fi
if [ $smoke_rc -ne 0 ]; then
  echo "结论：冒烟红（exit=$smoke_rc）。先在容器里复跑一次再定性——见 README 第三节。"
  exit $smoke_rc
fi
echo '结论：门禁干净。注意 import 通过不等于服务能起，验收仍要 compose ps + HTTP。'
exit 0
