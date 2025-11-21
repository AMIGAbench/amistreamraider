FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    STREAMLINK_WEBBROWSER_EXECUTABLE=/usr/local/bin/chromium-headless

WORKDIR /app

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    curl \
    ffmpeg \
    fonts-liberation \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxkbcommon0 \
    libxrandr2 \
    libgbm1 \
    libxshmfence1 \
    libdrm2 \
    mbuffer \
    nano \
    procps \
    psmisc \
    xvfb \
    pulseaudio \
    mpv \
    x11-utils \
    x11-apps \
    netpbm && \
    rm -rf /var/lib/apt/lists/*

RUN cat <<'EOF' >/usr/local/bin/chromium-headless && \
    chmod +x /usr/local/bin/chromium-headless
#!/usr/bin/env bash
set -euo pipefail
export DISPLAY=${DISPLAY:-:99}
exec /usr/bin/chromium --disable-gpu --disable-software-rasterizer --disable-dev-shm-usage --no-sandbox "$@"
EOF

COPY server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    pip install --no-cache-dir streamlink==7.3.0

COPY . .

RUN chmod +x scripts/docker-entrypoint.sh \
    scripts/tcp_sink.py \
    scripts/hls_repacker.py \
    scripts/tcp_wrapper.py \
    scripts/fifo_switch.py

EXPOSE 18081 5001

ENTRYPOINT ["bash", "scripts/docker-entrypoint.sh"]
