echo '=== 1. 复制新代码到容器 ==='
docker cp /vol1/1000/doubao-butler/butler/api/pwa_chat_routes.py doubao-butler:/app/butler/api/pwa_chat_routes.py 2>&1
docker cp /vol1/1000/doubao-butler/butler/core/tools.py doubao-butler:/app/butler/core/tools.py 2>&1
echo '=== 2. 验证文件已更新 ==='
docker exec doubao-butler grep -c '8765/api/v1/pm/send' /app/butler/api/pwa_chat_routes.py
echo '=== 3. 查找并重启 uvicorn 进程 ==='
docker exec doubao-butler sh -c 'ps aux | grep -E "uvicorn|python.*main|python.*app" | grep -v grep'
echo '=== 4. 发送 SIGHUP 或杀掉进程让 supervisord/docker 重启 ==='
docker exec doubao-butler sh -c 'pkill -f "uvicorn" 2>/dev/null; sleep 2; ps aux | grep uvicorn | grep -v grep'
echo '=== 5. 等3秒后验证服务 ==='
sleep 3
curl -s http://localhost:8095/api/health 2>/dev/null || echo '服务重启中...'