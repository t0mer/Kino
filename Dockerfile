# syntax=docker/dockerfile:1

# --- M2 placeholder: web frontend build -------------------------------------
# FROM --platform=$BUILDPLATFORM node:22-alpine AS web
# WORKDIR /web
# COPY web/package*.json ./
# RUN npm ci
# COPY web/ ./
# RUN npm run build        # outputs /web/dist, copied into the runtime stage below
# -----------------------------------------------------------------------------

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=0 UV_LINK_MODE=copy
WORKDIR /src
COPY pyproject.toml README.md ./
COPY kino/ ./kino/
RUN uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

FROM python:3.12-slim
ARG VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    KINO_VERSION=${VERSION} \
    KINO_DATA_DIR=/data

# No apt packages needed: opencv-python-headless and onnxruntime wheels are self-contained
# on python:3.12-slim (verified by importing cv2/onnxruntime/pillow-heif and running cv2 ops).

COPY --from=builder /opt/venv /opt/venv
# M2: COPY --from=web /web/dist /opt/venv/share/kino/web

RUN useradd --create-home --uid 1000 kino \
    && mkdir -p /data/photos \
    && chown -R kino:kino /data
USER kino
WORKDIR /data

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3).status==200 else 1)"

ENTRYPOINT ["kino"]
CMD ["api"]
