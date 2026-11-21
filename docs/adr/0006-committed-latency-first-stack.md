# 0006: Committed latency-first stack

- Status: accepted
- Date: 2026-11-21

## Context

The product's input and output devices are a screen, a camera and a microphone.
Latency is the product promise (p50 < 1.5 s glass-to-glass). The team needs
one stack it doesn't re-debate during the hackathon.

## Decision

Adopt `docs/stack.md` as binding. In short:

- **Devices:** one laptop (NVIDIA RTX preferred) driving a wide-FOV UVC webcam
  (720p30 MJPG, manual exposure), a USB speakerphone with hardware echo
  cancellation (mic and ad audio in one device), and a 16:9 TV in Game mode
  over HDMI.
- **Perception agent:** Python 3.12; YOLO26n-pose every frame (people, looking,
  face box, distance) plus YOLO26s objects at 5 fps; ByteTrack; TensorRT FP16 /
  CoreML / OpenVINO export; Silero VAD; ElevenLabs Scribe v2 Realtime with our
  own keyterm matcher on partial transcripts; an echo guard.
- **Backend:** Python 3.14, FastAPI + uvicorn[standard], one worker, Pydantic JSON (see amendment),
  in-memory hot state, the existing DDD kernel, Postgres, and an asyncio
  `WarmWorker` with per-provider limits.
- **Models:** Claude Haiku 5.5 (official `anthropic` SDK, effort low,
  structured output, prompt caching) for tagging, topics and copy.
  ElevenLabs Flash v2.5 for voice. fal for images and video (MiniMax H3 Max
  Turbo warm; a top-quality model cold).
- **Player:** Vite + React + TS in Chromium kiosk; A/B double-buffered layers
  with a 250 ms crossfade; Web Audio for voice; H.264 MP4 loops; CSS/WAAPI
  motion only.

## Amendment (2026-11-21)

YOLO inference moves to a remote GPU service with a local fallback (ADR 0007).
Generation stays on hosted APIs. The provider is picked by `bench_gpu.py`.

## Amendment (2026-11-21): WS serialization

orjson is replaced by Pydantic v2's own Rust JSON (`model_dump_json`,
`TypeAdapter.validate_json`) for every WS message. Measured on the contract's
real messages (best of 5 × 20k): serializing a 3-brand `creative.ready` takes
2.97 µs vs 3.66 µs for `orjson.dumps(model_dump(mode="json"))`; parsing and
validating a 6-person `audience.snapshot` takes 7.54 µs vs 9.25 µs for
`orjson.loads` + `validate_python`. Pydantic's path is about 20% faster, a single
pass that validates while parsing, and one less dependency. orjson stays
allowed for payloads that aren't contract models.

## Amendment (2026-11-21): TypeScript toolchain

`web/` runs two compilers. TypeScript 5.9 is the toolchain of record because
typescript-eslint's type-aware rules need TS `<6.1`. TypeScript 7 (native,
installed as `typescript-native`) runs `npm run typecheck`, 0.22 s vs 1.49 s.
`tsconfig.json` is valid for both (no `baseUrl`). CI checks types with both:
TS 7 in typecheck and 5.9 inside ESLint. When typescript-eslint supports TS 7,
drop 5.9 in one change.

## Why Haiku rather than Opus for the warm path

Every warm LLM call is a short, latency-bound classification or copy task. The
fastest current Claude model fits those best, and `docs/stack.md` §5 keeps a
config-only upgrade path to `claude-opus-5-5` with fast mode for copy if
rehearsal shows the quality falls short.

## Consequences

- An Anthropic adapter for `ILLMProvider` is the first AI task.
- A day-1 benchmark gates each provider (`docs/stack.md` §8). A failure swaps
  exactly that component, recorded here as an amendment.
- Any new component in the loop needs a new ADR and a measured latency win.
