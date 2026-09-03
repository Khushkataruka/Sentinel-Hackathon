# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- frontend --
FROM node:20-alpine AS frontend
WORKDIR /src
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


# ------------------------------------------------------------------ models --
# Discarded after the export; only the .onnx is carried forward. CPU-only
# torch, because this stage exists to run one export and then die.
FROM python:3.11-slim AS models
RUN pip install --no-cache-dir \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      ultralytics
WORKDIR /out
RUN yolo export model=yolov8n.pt format=onnx opset=12 imgsz=640 \
 && mv yolov8n.onnx yolo.onnx \
 && ls -l yolo.onnx


# ----------------------------------------------------------------- runtime --
FROM python:3.11-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

# libglib2.0-0 is opencv-python-headless's one system dependency.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# Workspace members must be present before sync: pyproject resolves
# sentinel-* from [tool.uv.sources], not from PyPI.
COPY pyproject.toml ./
COPY packages/ packages/
RUN uv sync --no-dev \
 && uv pip install "onnxruntime>=1.18"

COPY db/ db/
COPY scripts/ scripts/
COPY adapters/ adapters/
COPY docker/ docker/

COPY --from=models /out/yolo.onnx /app/var/models/yolo.onnx

ENV PATH="/app/.venv/bin:${PATH}"


# --------------------------------------------------------------------- web --
FROM nginx:1.27-alpine AS web
COPY --from=frontend /src/dist /usr/share/nginx/html
COPY docker/nginx/default.conf /etc/nginx/conf.d/default.conf
