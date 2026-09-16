# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

ARG STUDIO_UID=1000
ARG STUDIO_GID=1000
ARG STUDIO_REVISION=unknown
LABEL org.opencontainers.image.title="Studio Standalone Docker tester" \
      org.opencontainers.image.source="https://github.com/ToxicHost/sd-webui-forge-studio" \
      org.opencontainers.image.revision=$STUDIO_REVISION

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/studio/data/home XDG_CACHE_HOME=/studio/data/cache \
    HF_HOME=/studio/data/cache/huggingface TORCH_HOME=/studio/data/cache/torch \
    SD_WEBUI_CACHE_DIR=/studio/data/cache/engine \
    YOLO_CONFIG_DIR=/studio/data/cache/ultralytics \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git ffmpeg libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && test "$STUDIO_UID" -gt 0 && test "$STUDIO_GID" -gt 0 \
    && groupadd --non-unique --gid "$STUDIO_GID" studio \
    && useradd --uid "$STUDIO_UID" --gid "$STUDIO_GID" --no-create-home --home-dir /studio/data/home studio \
    && mkdir -p /studio/app /studio/data \
    && ln -s /studio/data/logs /studio/logs \
    && chown -R studio:studio /studio

WORKDIR /studio/app
COPY requirements.txt ./
# Same CUDA builds selected by modules/platform_selection.py for native installs.
RUN python -m pip install --no-cache-dir torch==2.11.0+cu130 torchvision==0.26.0+cu130 \
      --index-url https://download.pytorch.org/whl/cu130 \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check

# Dockerfile.dockerignore admits only runtime files, guides and licenses.
COPY --chown=studio:studio . ./
USER studio
RUN python scripts/setup_assets.py
ENTRYPOINT ["python", "packaging/docker/entrypoint.py"]
