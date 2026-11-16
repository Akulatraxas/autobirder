FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/hf_cache \
    DATA_DIR=/data \
    CACHE_DIR=/cache \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch==2.15.0 torchvision==0.29.2 --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# world-writable so the app also works when started with `--user uid:gid`
RUN mkdir -p /app/hf_cache /cache && chmod 777 /app/hf_cache /cache
ENV HOME=/tmp

COPY LICENSE /app/LICENSE
COPY app/ /app/app/

EXPOSE 8000

# Runs as an unprivileged user (uid/gid 1000 by default). Photos are modified in place, so the
# user must be able to write to the mounted photo folder. If your files belong to another
# uid, override it at runtime: `--user $(id -u):$(id -g)` (docker), `--userns=keep-id`
# (rootless podman) or `user:` in docker-compose.yml. The cache dirs above are world-writable
# so any uid works. Do not run as root.
USER 1000:1000

CMD ["sh", "-c", "uvicorn app.main:_from_env --factory --host $HOST --port $PORT"]
