FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# 系统依赖：ffmpeg（TTS）
# docker CLI 通过挂载宿主机 /usr/bin/docker 提供（见 docker-compose.yml）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# P0-10 phase3: 非 root 用户运行
RUN useradd -r -u 10001 -s /usr/sbin/nologin butler \
    && mkdir -p /app/data /app/tts \
    && chown -R 10001:10001 /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# homesdk 固化到镜像（0.3.3：adm/errors·adm/status·adm/probe + auth token_key + **areas 房间区域机制**）
# DCD 20261104「房间定义统一到 HA area_id 三仓契约」T7：三仓显式锁 homesdk>=0.3.3。
COPY vendor/homesdk-0.3.3-py3-none-any.whl /tmp/homesdk-0.3.3-py3-none-any.whl
RUN pip install --no-cache-dir /tmp/homesdk-0.3.3-py3-none-any.whl && rm -f /tmp/homesdk-0.3.3-py3-none-any.whl

COPY butler /app/butler

EXPOSE 8095

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8095/api/health').status==200 else 1)"

USER butler

CMD ["uvicorn", "butler.app:app", "--host", "0.0.0.0", "--port", "8095", "--workers", "1"]
