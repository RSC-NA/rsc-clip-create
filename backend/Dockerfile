# FastAPI API and the FFmpeg render worker. One image, two commands -- both
# need ffmpeg (the API probes uploads with ffprobe) and both read and write the
# same storage volume.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg brings ffprobe with it. Pillow ships manylinux wheels, so it needs no
# build toolchain here, and fonts come from Pillow's bundled Aileron unless
# RENDER_FONT_PATH points elsewhere.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend

# Storage is a shared volume between the API and the worker. Creating it here
# with the right ownership means a fresh named volume inherits it.
RUN groupadd --system app \
 && useradd --system --gid app --home /app --no-log-init app \
 && mkdir -p /app/storage/clips /app/storage/renders /app/storage/sweeps \
 && chown -R app:app /app

USER app

# Absolute, not the default relative "storage": the API and the worker would
# otherwise resolve it against their own working directories.
ENV STORAGE_ROOT=/app/storage \
    PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
