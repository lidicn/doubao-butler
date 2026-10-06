cd /vol1/1000/docker/doubao-butler 2>/dev/null || cd /vol1/1000/doubao-butler 2>/dev/null || { echo '找不到目录'; exit 1; }
echo '=== 当前目录 ==='
pwd
echo '=== 构建镜像（利用缓存）==='
docker compose build 2>&1 | tail -15
echo '=== 重启容器 ==='
docker compose up -d 2>&1
echo '=== 容器状态 ==='
docker ps --filter name=doubao-butler --format '{{.Names}} {{.Status}}'
echo '=== 验证新代码 ==='
sleep 3
docker exec doubao-butler grep -c '8765/api/v1/pm/send' /app/butler/api/pwa_chat_routes.py