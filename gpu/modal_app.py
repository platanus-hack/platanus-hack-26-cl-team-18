"""Deploy the GPU inference service on Modal (ADR 0007 candidate provider).

    modal secret create team18-gpu GPU_TOKEN=$(openssl rand -hex 16)   # required: no token, no start
    cd gpu && modal deploy modal_app.py
    uv run python -m scripts.bench.gpu --url wss://<workspace>--team18-gpu-serve.modal.run/infer

One warm container (no cold start in the demo) and one container max, so the
service's own no-queue admission gate holds. Weights live on a Volume. Frames
are processed in memory and never stored (same service code as Docker).
Stop it after the demo: `modal app stop team18-gpu`.
"""

import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0", "git")
    .uv_pip_install(
        "fastapi>=0.115",
        "uvicorn[standard]>=0.32",
        "numpy>=2.0",
        "pillow>=11.0",
        "ultralytics>=8.4,<9",
        "clip @ git+https://github.com/ultralytics/CLIP.git",
        "ftfy>=6.3",
        "regex>=2024.0",
    )
    .env(
        {
            "GPU_MODEL": "yolo",
            "GPU_POSE_MODEL": "/models/yolo26s-pose.pt",
            "GPU_OBJECT_MODEL": "/models/yoloe-26s-seg.pt",
            "GPU_DEVICE": "0",
            "GPU_TENSORRT": "0",
            "YOLO_CONFIG_DIR": "/models/.ultralytics",
            "YOLO_AUTOINSTALL": "false",
        }
    )
    # Bake the weights + YOLOE vocabulary into the image at build time (same as the
    # Dockerfile), so the first container start downloads nothing. MobileCLIP is
    # only needed to bake and is deleted afterwards.
    .add_local_python_source("service", copy=True)
    .run_commands(
        "mkdir -p /opt/weights && cd /opt/weights && PYTHONPATH=/root YOLO_CONFIG_DIR=/tmp/yolo python -c "
        '"from ultralytics import YOLO; from service.vocabulary import bake_object_model; '
        "YOLO('yolo26s-pose.pt'); print(bake_object_model('yoloe-26s-seg.pt'))\" "
        "&& rm -f mobileclip*.ts mobileclip*.pt && ls -la /opt/weights"
    )
)

app = modal.App("team18-gpu")
models = modal.Volume.from_name("team18-gpu-models", create_if_missing=True)


@app.function(
    image=image,
    gpu="L4",
    min_containers=1,
    max_containers=1,
    scaledown_window=600,
    volumes={"/models": models},
    secrets=[modal.Secret.from_name("team18-gpu")],
    timeout=3600,
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app()
def serve():
    import os
    import shutil
    from pathlib import Path

    for baked in Path("/opt/weights").glob("*.pt"):  # seed the volume; never overwrite
        if not (Path("/models") / baked.name).exists():
            shutil.copy2(baked, "/models")
    os.chdir("/models")  # an exported .engine lands here; the volume keeps it
    from service.app import check_exposure, create_app

    # A public *.modal.run URL: the `team18-gpu` secret must carry GPU_TOKEN.
    check_exposure("0.0.0.0", os.environ.get("GPU_TOKEN") or None)
    return create_app()
