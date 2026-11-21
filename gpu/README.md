# gpu/: remote YOLO26 / YOLOE-26 inference service

The stateless inference half of the perception agent (ADR 0007). It takes a
JPEG frame over a WebSocket and returns pose and object detections. Capture,
tracking, geometry, face blur and snapshots all stay in `edge/`. Its own uv
project (Python 3.12) and a Docker image, so it runs unchanged on a GPU VM,
RunPod/Modal, or a GPU box on the venue LAN.

- **Stateless.** No tracks and no session state.
- **Never queues.** Each connection handles one frame at a time: read, decode,
  infer, reply, then read the next. The client (`edge/agent/vision/remote.py`)
  keeps at most 2 frames in flight and drops newer frames instead of queueing.
  Admission is shared across connections: while one frame is being processed,
  other clients receive `inference busy; frame dropped` before image decoding.
  The edge detector treats this as unavailable and uses its local fallback.
- **Never stores frames.** JPEGs are decoded in memory and dropped once the
  reply goes out. Nothing is written to disk, and no pixels, frame bytes or
  per-frame access lines are logged. `tests/test_service.py` checks this.

## Run

```bash
cd gpu
uv sync                                  # service + tests (fake model only)
uv run pytest
GPU_MODEL=fake uv run python -m service --port 8080       # no model, for wiring/benches
uv sync --extra yolo                     # the real model (pulls torch)
GPU_DEVICE=0 uv run python -m service --port 8080         # YOLO26 on GPU 0 (.pt)
GPU_TENSORRT=1 GPU_DEVICE=0 uv run python -m service      # one-time TensorRT FP16 export
```

Docker (NVIDIA Container Toolkit on the host):

```bash
docker build -t team18-gpu --build-arg TENSORRT=1 gpu/
docker run --gpus all -p 8080:8080 -e GPU_TOKEN="$GPU_TOKEN" -e GPU_TENSORRT=1 \
  -v team18-gpu-models:/models team18-gpu
```

Put TLS in front of it (the provider's HTTPS proxy, Caddy, or a tunnel) so the
agent connects over `wss://`. Raw frames travel only over TLS (ADR 0007).

## Config (env)

| Var | Default | Meaning |
|---|---|---|
| `GPU_TOKEN` | unset | If set, `/infer` requires `Authorization: Bearer <token>` (constant-time compare). A bad token is closed with 1008 before accept |
| `GPU_MODEL` | `yolo` | `yolo`, or `fake` (canned detections, no download) |
| `GPU_POSE_MODEL` / `GPU_OBJECT_MODEL` | `yolo26s-pose.pt` / `yoloe-26s-seg.pt` | Weights, downloaded on first use. The object model is YOLOE-26: the shared vocabulary (`service/vocabulary.py`) is baked into `<stem>-vocab-<fingerprint>.pt` once (needs MobileCLIP, ~250 MB, kept on `/models`). An empty object model disables objects |
| `GPU_OBJECT_CONF` | `0.25` | Object score floor (open-vocabulary scores run lower than pose) |
| `GPU_DEVICE` | auto | `0`, `cpu`, … |
| `GPU_IMGSZ` / `GPU_CONF` | `640` / `0.35` | Same as the local detector |
| `GPU_TENSORRT` | `0` | `1` exports each `.pt` to a `.engine` (FP16) next to it once, reuses it after that, and falls back to `.pt` if the export fails |
| `GPU_FAKE_DELAY_MS` / `GPU_FAKE_DETECTIONS` | `0` / unset | Fake model only: per-frame delay, and a JSON file of canned `persons`/`objects` |

## Protocol

`GET /health` → `{"status": "ok" | "starting", "model": "..."}` (no auth, no data).

`WS /infer`: one **binary** message per frame:

```
+----------------------+---------------------------+-------------+
| header length N      | header: N bytes UTF-8 JSON | JPEG bytes  |
| 4 bytes, uint32 BE   |                           |             |
+----------------------+---------------------------+-------------+

header = {"seq": 42,                       # int >= 0, echoed back
          "measured_at": "2026-...+00:00", # device capture time, echoed back
          "width": 640, "height": 360,     # size of THIS JPEG (must match)
          "objects": true}                 # optional; false = pose only
```

The header is at most 4 KB and the whole message at most 2 MB. The agent sends
a 640 px long side at q70 (~35 KB).

Reply (one **text** message per request, in order):

```json
{"seq": 42, "measured_at": "...", "width": 640, "height": 360,
 "persons": [{"box": [x1, y1, x2, y2], "confidence": 0.91,
              "keypoints": [[x, y, c], ...17 COCO keypoints]}],
 "objects": [{"label": "cup", "box": [x1, y1, x2, y2], "confidence": 0.62}],
 "server_ms": 9.8, "model_ms": 7.1}
```

Coordinates are pixels in the JPEG that was sent; the client scales them back
to the camera frame. Persons come from the pose model (class 0 only); objects
come from the detect model with `person` removed. This is the same parsing as
`edge/agent/vision/adapters.py`. `server_ms` covers receive to reply (decode +
inference) and `model_ms` covers inference only.

A malformed frame gets `{"seq": <int or null>, "error": "<reason>"}` and the
connection stays open. A text message gets the same error shape.

## Point the agent at it

```bash
cd edge
GPU_TOKEN=... uv run python -m agent.vision --screen demo-01 --gpu-url wss://<host>/infer
```

This builds `FailoverDetector(primary=RemoteGpuDetector, fallback=local YOLO)`.
A timeout (0.5 s), a disconnect, or a p95 over 150 ms switches to local, and
the agent probes remote again after 10 s.

## Benchmark gate (docs/stack.md §8)

```bash
uv run --project edge python scripts/bench_gpu.py --url wss://<host>/infer --frames 300 \
  --token "$GPU_TOKEN" [--image a_real_frame.jpg]
```

Pass means round trip p50 ≤ 80 ms and p95 ≤ 150 ms with zero errors. The exit
code is 0 on pass and 1 on fail. Run it from the venue uplink.
