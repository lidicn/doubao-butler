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

# WO-BUT-002⑦：homesdk 固化到镜像（consent 判定 + 质量门禁）
COPY vendor/homesdk /tmp/homesdk
RUN pip install --no-cache-dir /tmp/homesdk && rm -rf /tmp/homesdk

COPY butler /app/butler

EXPOSE 8095

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8095/api/health').status==200 else 1)"

CMD ["uvicorn", "butler.app:app", "--host", "0.0.0.0", "--port", "8095", "--workers", "1"]
