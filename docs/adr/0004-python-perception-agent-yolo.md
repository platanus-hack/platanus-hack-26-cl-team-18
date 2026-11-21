# 0004: Python perception agent with YOLO (supersedes 0002)

- Status: accepted
- Date: 2026-11-21

## Context

ADR 0002 put perception in the browser with MediaPipe. The team prefers YOLO:
better object detection, built-in multi-object tracking (ByteTrack), custom
classes, and the Python ML ecosystem it already knows.

## Decision

Perception runs in a separate local Python process, `edge/` (its own `uv`
project), on the same machine as the camera:

- OpenCV capture → YOLO (nano/small, 640 px) for people and objects →
  ByteTrack → anonymous tracklets.
- *Looking*, the distance band and the face blur box come from pose keypoints
  (YOLO26n-pose; see ADR 0006 and `docs/stack.md`). Faces are blurred before a
  keyframe leaves the process.
- Mic → VAD → sound scene, plus ElevenLabs Scribe realtime in speech mode
  (ADR 0005).
- Sends `audience.snapshot`, `audience.keyframe` and `audience.utterance` to the
  backend over a localhost WebSocket (`docs/architecture.md` §10).

The browser (`web/`) stays as the player and console.

## Why a separate project

- torch/onnxruntime/ultralytics wheel support for Python 3.14 may lag. The
  agent can pin 3.12/3.13 without touching the backend's 3.14.
- Perception crashes or GPU stalls can't take down the exchange.
- It's the same boundary production would have (an edge box next to the
  screen).

## Consequences

- Three processes in the demo; `make demo` must start all of them.
- Agent and player share the laptop clock, so attention-seconds are computed in
  the backend by joining the agent's looking samples with the player's playback
  window.
- The agent is infrastructure: no domain logic, no auction decisions. It emits
  facts (snapshots), and the backend decides.
- Frames and audio never leave the agent unblurred or unconsented.
