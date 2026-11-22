# 0010: The /screen page can be its own sensor

- Status: accepted
- Date: 2026-11-22
- Amends: ADR 0004 (the Python agent is no longer the only sensor), ADR 0005 (speech can run in the browser)

## Context

The public deploy (Render) has no edge agent next to it, so `/screen` there shows
the house ad forever: nothing sends `audience.snapshot`. Running `edge/` against
prod needs a laptop with Python, models and the AGENT_TOKEN. The team wants the
screen itself to use its camera and mic.

## Decision

`/screen` runs a perception agent in the page (`web/src/features/screen/sensor/`):

- **Vision:** YOLO26n-pose exported to ONNX (end-to-end head, 416 px,
  `web/public/models/yolo26n-pose.onnx`) runs on-device with onnxruntime-web
  (WebGPU, else WASM) at up to 5 fps, one inference in flight. Looking and
  distance use the edge agent's keypoint geometry; an IoU tracker keeps
  session-local ids. It sends the same `audience.snapshot` on `/ws/agent`, with a
  1 s heartbeat. No held-product detection and no age evidence: `minor_likely`
  is `uncertain` whenever someone is present, which only blocks restricted
  categories (invariant 2).
- **Speech:** while `live.status.listening` (speech mode for the screen, ADR
  0005), the mic (browser echo cancellation on) streams 16 kHz PCM to ElevenLabs
  Scribe Realtime with a **single-use token** from `POST /api/speech/token/{screen}`
  (SCREEN_TOKEN as bearer). The API key never reaches the browser. Transcripts
  become ephemeral `audience.utterance` frames; the backend matches keyterms.
- **Auth:** `/ws/agent` also accepts the SCREEN_TOKEN offered as a subprotocol
  (`team18.token.<token>`), because a browser can't set headers.
- **Opt out:** `/screen?sense=0`. The local station (`make station`) opens the
  player that way, so the edge agent stays the only sensor there.

## Consequences

- Frames never leave the browser (stronger than ADR 0007 for this path); only
  counts, anonymous tracklets and distance bands do.
- Lower recall than the edge agent (nano model, no YOLOE objects, no "complete
  the basket" from held products). The edge agent remains the demo-grade sensor.
- The web build ships a 12 MB model and a 21 MB WASM runtime, loaded only by `/screen`.
- Prod needs `SPEECH_MODE_SCREENS` for the mic (set in `terraform.tfvars`).
