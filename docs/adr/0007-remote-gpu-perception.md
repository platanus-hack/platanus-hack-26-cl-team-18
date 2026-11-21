# 0007: YOLO inference on a remote GPU service, with a local fallback

- Status: accepted (provider to be chosen from the benchmark)
- Date: 2026-11-21

## Context

The team wants YOLO on a real GPU for fast, accurate recognition, hosted on
separate infrastructure (provider not chosen yet). Generation (video, image,
voice, LLM) stays on hosted APIs. Moving inference off the laptop puts a
network round trip into the hot loop, so the design has to keep that cost
visible and survivable.

## Decision

Split the perception agent into **capture (local)** and **inference
(remote GPU)**:

- `edge/` (laptop) keeps camera/mic capture, VAD, Scribe streaming, tracking
  (ByteTrack on returned detections), looking/distance geometry, face blur for
  keyframes, and the snapshot logic. It calls inference through a `Detector`
  port.
- `gpu/` is a **stateless inference service**: Python 3.12, Ultralytics YOLO26
  exported to **TensorRT FP16**, FastAPI WebSocket. It takes a JPEG frame and
  returns pose and object detections. It ships as a Docker image, so it runs
  unchanged on Modal, RunPod, a GPU VM, or a GPU box at the venue.
- **Two `Detector` adapters:** `RemoteGpuDetector` (primary) and
  `LocalDetector` (YOLO26n on the laptop CPU/GPU via OpenVINO/CoreML/TensorRT).
  A `FailoverDetector` switches to local when the remote p95 over the last 2 s
  exceeds the budget or the socket drops, and switches back after 10 s
  healthy. The console shows which one is active.
- Tracking stays local so the server is stateless and failover doesn't lose
  tracks.

## Transport

- One persistent **WebSocket over TLS**, binary messages:
  `{seq, measured_at}` header + JPEG of the frame **resized to 640 px long
  side, quality 70** (~30–40 KB). Response: JSON detections + `server_ms`.
- **At most 2 frames in flight; never queue.** If the result for frame N
  isn't back, frame N+2 replaces anything pending. Effective rate 10–20 fps
  depending on RTT.
- Bandwidth: about 3–5 Mbps upstream. **Use a phone hotspot or wired
  uplink**, not venue Wi-Fi.

## Choosing the provider (from results)

Run `scripts/bench_gpu.py` against each candidate from the venue network:
300 frames, reporting p50/p95 of **round trip** (send → detections back).

| Criterion | Requirement |
|---|---|
| Round trip p50 | ≤ 80 ms (beats local CPU with margin) |
| Round trip p95 | ≤ 150 ms |
| Cold start | None during the demo (keep ≥ 1 warm instance / pod running) |
| Region | As close to Santiago as possible. Check South American regions with GPUs first; a US region adds about 120–180 ms RTT on its own and will likely fail the p50 |

If no remote candidate passes, the default is a **GPU box on the venue LAN**,
or `LocalDetector` alone. Record the outcome as an amendment here.

## Privacy

Raw frames leave the laptop, but only to **our own** inference service: TLS
only, processed in memory, never stored or logged, no third-party API, no
face recognition. Third parties (the VLM) still only receive face-blurred
keyframes. Product invariant 3 in `CLAUDE.md` is worded to match.

## Consequences

- The hot loop gains a network hop; it is budgeted (≤ 150 ms p95) and has an
  automatic fallback.
- Snapshots carry `detector=remote|local` and the inference round trip in the
  latency trace.
- `make demo` starts the local fallback warm even when remote is primary.
