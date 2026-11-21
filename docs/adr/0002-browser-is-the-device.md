# 0002: The browser is the device (TypeScript player with on-device perception)

- Status: superseded by [0004](0004-python-perception-agent-yolo.md) (2026-11-21); the browser remains the **player** only
- Date: 2026-11-21

## Context

We need camera and mic capture, real-time detection and a creative player.
Options considered: a Python edge agent (OpenCV + YOLO) plus a separate
player; native C++/Rust; or a single Chromium kiosk page.

## Decision

The screen is one TypeScript web page (`/screen`) running in Chromium:
`getUserMedia` for capture, MediaPipe Tasks (WASM/WebGPU) for face, landmark,
object and audio classification, and React for rendering creative specs. It
talks to the Python backend over one WebSocket.

## Why

- No install and no Python 3.14 wheel issues for ML libraries on the device.
- DOOH players already render HTML5; this is how real screens can be
  retrofitted.
- Perception and playback in one process: attention is measured against
  exactly what is on screen, with no clock-sync problem between two local
  processes.
- Face blurring happens on device before keyframes leave it.

## Consequences

- Detection quality is limited to MediaPipe models; semantic richness comes
  from the warm-path VLM.
- Production can move perception to a Jetson-class device behind the same
  message contract (`docs/architecture.md` §9).
