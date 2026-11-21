# Committed stack (latency-first)

> **Doc status: BINDING** (ADR 0006, amended by ADR 0007: YOLO on a remote GPU service). This is the stack. Don't swap a component
> without a new ADR and a measured latency win. Why it's shaped this way:
> [`architecture.md`](architecture.md). Every number below is a budget to verify
> with the day-1 benchmark (§8), not a promise.

## 1. Devices (the product's input and output)

One **demo station** = one laptop + three devices. Everything in the hot loop
runs on that laptop.

| Device | Commit | Settings that matter for latency |
|---|---|---|
| **Compute (local)** | Demo laptop: capture, tracking, backend, player, and the **local fallback detector** (YOLO26n via OpenVINO/CoreML/TensorRT, whatever the laptop has) | Plugged in, performance power profile |
| **Compute (GPU)** | **Remote GPU inference service `gpu/`** for YOLO (ADR 0007). Provider chosen from benchmark results: serverless GPU, GPU VM, or a GPU box on the venue LAN | ≥ 1 warm instance during the demo; closest region to Santiago |
| **Uplink** | Phone hotspot or wired, **not venue Wi-Fi** | About 3–5 Mbps upstream for frames |
| **Camera (input)** | USB **UVC webcam**, 1080p-capable, **wide FOV (≥ 90°)** to see groups. Mounted top-center of the screen, at about 1.7 m height, tilted slightly down | Capture **1280×720 @ 30 fps, MJPG** (not YUYV: USB bandwidth). **Manual exposure and auto-exposure priority off** (`v4l2-ctl -c exposure_dynamic_framerate=0` or the equivalent), otherwise the FPS halves in dim light. `CAP_PROP_BUFFERSIZE=1` |
| **Microphone + speaker (input and output)** | **USB conference speakerphone with hardware echo cancellation (AEC)**. It is both the room mic and the ad's audio output. Backup: a wireless lapel mic for the presenter | 16 kHz mono capture. AEC is mandatory: without it the mic hears our own voice-over, transcribes it, and the ad re-targets itself |
| **Screen (output)** | **43"+ TV or monitor, 16:9 landscape**, HDMI from the laptop | **TV "Game mode" on** (cuts 50–100 ms of TV post-processing), 60 Hz, overscan off, the laptop's power saving off for the display |

## 2. Process layout (fixed)

```
                     gpu/ inference service (remote GPU, Docker, YOLO26 TensorRT)
                        ▲ JPEG 640px (≤ 2 in flight)   │ detections JSON
                        │ WSS                          ▼
camera ─┐                                                           ┌─► screen (HDMI)
mic ────┤  edge/ perception agent (Python 3.12, laptop)             │   Chromium kiosk  web/ /screen
        │    capture thread → FailoverDetector(remote | local)      │
        │    → ByteTrack → looking/distance → keyframe blur         │
        │    mic → Silero VAD → Scribe realtime (speech mode)       │
        └──── WS localhost (pydantic JSON; JPEG as binary) ──► app/ backend (Python 3.14, FastAPI)
                                                          │  in-memory moment + campaign book
                                                          │  bus → auction → creative
                                                          │  warm tasks: Claude, ElevenLabs, fal
                                                          ├──── WS ──► /screen (player)  ──► speakerphone
                                                          └──── WS ──► /console (2nd monitor / projector)
```

Three processes on the laptop share one clock, plus one remote stateless GPU
service (ADR 0007). No Redis, no message broker, no extra service. The only
network hops are the GPU service (hot, budgeted ≤ 150 ms p95, with local
failover) and the hosted generation APIs (warm).

## 3. Perception agent (`edge/`)

| Piece | Commit | Why |
|---|---|---|
| Runtime | **Python 3.12**, its own `uv` project | Most mature wheels for torch, TensorRT, ultralytics and onnxruntime. The backend's 3.14 is unaffected (ADR 0004) |
| Capture | OpenCV (V4L2 on Linux) in a **dedicated thread with a single "latest frame" slot** | The inference loop always takes the newest frame; stale frames are dropped, never queued |
| People + looking | **YOLO26n-pose** (Ultralytics), every frame, 640 px | One pass gives person boxes plus 17 keypoints. Looking = nose and both eyes visible, with the nose centered between the eyes. Distance band = shoulder width in pixels. The face blur box comes from the head keypoints. **No separate face or head-pose model**: one model fewer in the hot loop. YOLO26 is NMS-free and edge-optimized; fall back to YOLO11n-pose only if the installed Ultralytics lacks it |
| Objects | **YOLO26s** detect, **5 fps**, filtered to demo-relevant COCO classes (bottle, cup, backpack, handbag, laptop, cell phone, dog, skateboard, sports ball, bicycle, stroller-like) | Objects change slowly, so a 5 fps cadence halves GPU load |
| Tracking | **ByteTrack** via `model.track(persist=True)` | Anonymous tracklets, dwell time, no re-identification |
| Where inference runs | **`gpu/` service** (Python 3.12, FastAPI WS, YOLO26 pose + objects exported to **TensorRT FP16**, Docker), primary. **Local** YOLO26n (OpenVINO / CoreML / TensorRT on the laptop), hot standby. `FailoverDetector` switches on p95 > 150 ms or a disconnect (ADR 0007) | GPU accuracy and speed without betting the demo on the network |
| Frame transport | Persistent WSS, binary: header `{seq, measured_at}` + JPEG 640 px q70 (~35 KB). **≤ 2 frames in flight, never queued** | Bounded latency; stale frames are replaced, not buffered |
| Tracking location | **Local** (ByteTrack on returned detections) | Keeps the GPU service stateless, so failover keeps the tracks |
| Keyframe | Blur faces (Gaussian on head boxes), resize to **512 px long side**, JPEG quality 80, ≤ 1 per 1.5 s or on moment change | Small image = fewer vision tokens = faster tagging |
| Audio | `sounddevice` 16 kHz mono int16 → **Silero VAD** (ONNX, 32 ms chunks) | Only speech segments are streamed, which saves bandwidth and latency |
| Speech-to-text | **ElevenLabs Scribe v3 Live** (`scribe_v3_live`) over WebSocket (`/v1/speech-to-text/realtime`), streamed straight from the agent, our VAD commits after 320 ms of silence | Measured from Santiago: keyterm → partial ~200 ms (v2: ~1.2 s), speech end → final ~230 ms, WER 0 on the demo line (ADR 0005, latency amendment) |
| Keyterm hits | **Our own matcher on partial transcripts** (not committed ones) against a per-campaign keyterm list incl. Chilean variants ("hambre", "bajón", "gym", "café"…) | Fires the intent signal about 100–300 ms earlier than waiting for the commit |
| Echo guard | Hardware AEC plus: drop transcripts during our own voice-over playback windows (backend sends `player.audio_window`), and drop utterances fuzzy-matching the current voice-over text | Prevents the screen from re-targeting itself |

## 4. Backend (`app/`)

| Piece | Commit |
|---|---|
| Runtime | **Python 3.14**, FastAPI, **uvicorn[standard]** (uvloop + httptools + websockets), **single worker** |
| Serialization | **Pydantic v2's Rust JSON** for every WS message (`model_dump_json` / `validate_json`, one pass with validation); orjson only for non-model payloads (ADR 0006 amendment) |
| Hot state | In-process memory: current `AudienceMoment` per screen, campaign book (loaded from Postgres at boot and on change), creative and media cache index |
| Domain | Existing DDD kernel. The sync `MessageBus` is called via `anyio.to_thread.run_sync` from async routes (one thread hop, < 1 ms) |
| DB | PostgreSQL (docker locally), psycopg2. One commit per hot-path command (~2–5 ms on localhost) |
| Warm tasks | `asyncio` tasks owned by an in-process `WarmWorker`, with **bounded concurrency per provider** (semaphores) and dedupe by cache key. Never awaited by hot-path code |
| Media | Local disk cache dir served at `/media` (Starlette StaticFiles). Generated video goes through **ffmpeg** at ingest: H.264 High, yuv420p, 720p, `+faststart`, loop-trimmed |

## 5. AI and media providers

| Need | Commit | Settings |
|---|---|---|
| Keyframe → IAB tags | **Claude Haiku 5.5** (`claude-haiku-5-5`) via the **official `anthropic` Python SDK** (async client), behind `AudienceTagger` | `output_config.effort: "low"`, structured output (`messages.parse` / `output_config.format`) with a strict schema, **prompt caching** on the frozen system prompt (IAB subset + brand catalog + rules), small `max_tokens` |
| Speech → topics | **Claude Haiku 5.5**, same settings, behind `TopicExtractor` | Keyterm hits already applied; this only refines |
| Co-brand copy | **Claude Haiku 5.5**, effort `low`, behind `CopyWriter` | Validated output; stock copy is the fallback. If copy quality falls short in rehearsal, switch this one port to `claude-opus-5-5` with `speed: "fast"` (config only) |
| Voice-over (cached) | **ElevenLabs Flash v2.5** (`eleven_flash_v2_5`), HTTP convert → MP3, behind `VoiceSynthesizer` | Cached by `(copy_hash, voice_id, model)`. Pre-generated at seed time for demo creatives |
| "Talk to the screen" reply | **ElevenLabs Flash v2.5** over the `stream-input` WebSocket → PCM chunks → player | Start speaking on the first chunk |
| Hero image (optional) | **fal**, fastest FLUX-class text-to-image endpoint, behind `ImageGenerator` | No logos or text in prompts |
| Video, warm upgrade | **fal → MiniMax H3 Max Turbo** image-to-video, 5 s, 480–768p, behind `VideoGenerator` | Max 2 concurrent jobs, per-hour budget cap |
| Video, cold loops | **fal → best-quality model available on fal** (Veo 3.1 Quality class), run at seed time | One loop per compatible bundle and per solo brand |

`app/modules/ai` calls Claude through `AnthropicProvider`
(`infrastructure/adapters/llm/anthropic.py`), which implements `ILLMProvider`
with the **official `anthropic` SDK**. It maps SDK exceptions onto the typed
provider errors, and `ModelProfile.describe()` (`claude-haiku-5-5@low`) is the
provenance string stored with each result.

## 6. Player and console (`web/`)

| Piece | Commit | Why |
|---|---|---|
| Build | **Vite + React + TypeScript**, `npm` | Zero-config dev server; agents know it |
| Browser | **Chromium in kiosk mode** with `--kiosk --autoplay-policy=no-user-gesture-required --ignore-gpu-blocklist --enable-gpu-rasterization --disable-features=Translate --noerrdialogs` | Audio and video autoplay without a click; GPU compositing |
| Rendering | **Two stacked layers (A/B double buffer)**. The next creative mounts hidden in the back layer, waits for required logos' `img.decode()`, then a **250 ms CSS opacity crossfade**. Optional video/hero media loads independently (ADR 0001 amendment) | The swap costs one compositor frame. Never mount-and-show |
| Video | `<video muted playsinline loop preload="auto">` with H.264 MP4 from `/media` | Hardware decode everywhere |
| Audio | **Web Audio API** with pre-decoded `AudioBuffer`s for voice-overs, a `GainNode` for ducking, and an `AudioWorklet` for streamed PCM replies | Instant start; `<audio>` elements add decode lag |
| Motion | CSS transitions + Web Animations API only | No animation library in the critical bundle |
| Overlays | Logos (SVG), copy, QR (`qrcode` lib), price: DOM over the video | Crisp, brand-exact, never generated |
| Console charts | CSS bars + numbers; **uPlot** only for the live latency time series | Cheapest real-time rendering |

## 7. What we explicitly don't use (and why)

| Not used | Because |
|---|---|
| MediaPipe in the browser | Replaced by YOLO26-pose (ADR 0004, 0007) |
| Self-hosted generation models | Video, image, voice and LLM stay on hosted APIs; the GPU service only runs YOLO |
| A separate head-pose or face model | Pose keypoints already give looking and the face box |
| Redis, Kafka, Celery | One process holds hot state; the warm worker is `asyncio`; brokers add hops |
| Async psycopg rewrite | The sync kernel plus a thread hop costs < 1 ms; a rewrite costs hours |
| Video generation in the hot path | 2–15 s at best (ADR 0003) |
| OpenAI-compatibility shim for Claude | Use the official SDK adapter |
| Lottie / PixiJS / Framer Motion | CSS and WAAPI are enough and add nothing to load time |
| Cloud backend for the live demo | Render has no South American region; localhost is ~0 ms |

## 8. Day-1 benchmark (gate, 1 hour max)

A `scripts/bench_*.py` script per provider: 20 calls from the venue network,
reporting p50/p95. Pass criteria:

| Check | Pass |
|---|---|
| Remote GPU detector round trip (`scripts/bench_gpu.py`, 300 frames, from the venue uplink) | p50 ≤ 80 ms, p95 ≤ 150 ms. This picks the provider (ADR 0007) |
| Local fallback detector per frame | ≤ 45 ms |
| Haiku 5.5 keyframe tagging (512 px, structured) | p50 ≤ 1.2 s |
| Haiku 5.5 copy | p50 ≤ 1.0 s |
| Scribe realtime, end of speech → committed text | p50 ≤ 400 ms end to end |
| Flash v2.5, first audio byte | p50 ≤ 400 ms end to end |
| fal turbo video, 5 s at 768p | p50 ≤ 10 s |
| Glass-to-glass, cache hit | p50 ≤ 1.0 s incl. debounce |

If one fails, change that **one** component (the fallbacks named above),
record it in ADR 0006, and move on. Don't re-open the rest.
