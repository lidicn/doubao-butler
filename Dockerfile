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

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# WO-BUT-002⑦：homesdk 固化到镜像（v2.6 升级到 0.3.1，含 http/auth/mqtt/presence/time 全模块）
COPY vendor/homesdk-0.3.1-py3-none-any.whl /tmp/homesdk-0.3.1-py3-none-any.whl
RUN pip install --no-cache-dir /tmp/homesdk-0.3.1-py3-none-any.whl && rm -f /tmp/homesdk-0.3.1-py3-none-any.whl

COPY butler /app/butler

EXPOSE 8095

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8095/api/health').status==200 else 1)"

CMD ["uvicorn", "butler.app:app", "--host", "0.0.0.0", "--port", "8095", "--workers", "1"]
