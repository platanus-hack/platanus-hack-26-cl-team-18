# Stateless YOLO26 inference service (ADR 0007). Build from gpu/:
#   docker build -t team18-gpu .
#   docker run --gpus all -p 8080:8080 -e GPU_TOKEN=... -e GPU_TENSORRT=1 \
#       -v team18-gpu-models:/models team18-gpu
# The image ships baked weights (no download at start); the /models volume keeps
# them plus the exported .engine across restarts.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# python3.12 is Ubuntu 24.04's system Python; libgl/libglib are for opencv (an ultralytics dep);
# git fetches the CLIP tokenizer package YOLOE text prompts need.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12 python3.12-venv libgl1 libglib2.0-0 ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra yolo --no-install-project --python python3.12

# TensorRT export needs the TensorRT Python wheels; opt in at build time with
# --build-arg TENSORRT=1 and at run time with -e GPU_TENSORRT=1 (falls back to .pt).
ARG TENSORRT=0
# TensorRT must match torch's CUDA major: the locked torch (2.15) ships CUDA 13
# wheels (nvidia-cuda-runtime 13.x in uv.lock), so use the cu13 TensorRT build.
RUN if [ "$TENSORRT" = "1" ]; then uv pip install --python /opt/venv/bin/python "tensorrt-cu13>=11.4" "onnx>=1.16" onnxslim; fi

COPY service ./service

# Bake the weights into the image (needs network at *build* time only): YOLO26
# pose, YOLOE-26 and the YOLOE vocabulary baked in (service/vocabulary.py), so
# the container starts with no download. The MobileCLIP text encoder is only
# needed to bake and is deleted afterwards (~250 MB). Rebuild after editing
# the vocabulary: its fingerprint names the baked file.
WORKDIR /opt/weights
RUN PYTHONPATH=/app YOLO_AUTOINSTALL=false python -c "\
from ultralytics import YOLO; \
from service.vocabulary import bake_object_model; \
YOLO('yolo26s-pose.pt'); \
print(bake_object_model('yoloe-26s-seg.pt'))" \
    && rm -f mobileclip*.ts mobileclip*.pt \
    && ls -la /opt/weights

ENV YOLO_AUTOINSTALL=false \
    GPU_MODEL=yolo \
    GPU_POSE_MODEL=/models/yolo26s-pose.pt \
    GPU_OBJECT_MODEL=/models/yoloe-26s-seg.pt \
    GPU_DEVICE=0 \
    GPU_TENSORRT=0
VOLUME ["/models"]
EXPOSE 8080
# Run from /models so anything Ultralytics writes (an .engine export) persists.
WORKDIR /models
ENV PYTHONPATH=/app
HEALTHCHECK --interval=10s --timeout=3s --start-period=120s CMD python -c "import urllib.request,sys; sys.exit(0 if b'\"ok\"' in urllib.request.urlopen('http://127.0.0.1:8080/health').read() else 1)"
# Seed the /models volume with the baked weights without overwriting what it has
# (a TensorRT .engine exported on an earlier start stays next to them).
CMD ["sh", "-c", "cp --update=none /opt/weights/*.pt /models/ && exec python -m service --host 0.0.0.0 --port 8080"]
