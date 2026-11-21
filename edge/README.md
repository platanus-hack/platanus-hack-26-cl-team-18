# Perception agent

## Local video replay (first slice)

With Python 3.12 and uv installed, from `edge/` run:

```sh
uv sync
uv run on-sight /path/to/video.mp4 --fps 3 --print-snapshots
```

To publish to the local backend while replaying, start `uvicorn app.main:app`
from the repository root, then run from `edge/`:

```sh
uv run on-sight /path/to/video.mp4 --fps 3 --screen-id demo-01 --backend ws://localhost:8000/ws/agent
```

`GET /recognition/demo-01` returns the latest snapshot and backend receive
time in local/test environments; a missing or expired snapshot returns 404.
The classifier can also register an in-process handler for
`RecognitionSnapshot` with `container.router.on(...)`. This is a separate
recognition contract, not the classified `audience.snapshot`: it does not
invent looking, distance, or minor-safety values.

The canonical versioned schema is `RecognitionSnapshot` in
[`app/contract/messages.py`](../app/contract/messages.py). It carries the
screen and anonymous session, sequence and warm revision, capture time, video
time, tracked people, departed people, current YOLO detections (including
untracked detections, boxes and confidence), current/recent object counts,
audio-event slots, evidence-bearing attributes, latency, and an explicit
`finished` flag. Consumers should use `(session_id, seq, revision)` to handle
updates and discard stale data. The WebSocket sender keeps only the latest
pending snapshot; connectivity failures do not stop detection. The backend
keeps at most 64 screens in memory and expires snapshots after 30 seconds,
with periodic cleanup. No frame, crop or audio is persisted.

The first run downloads Ultralytics `yolo26n.pt`; model warmup runs before the
playback clock starts, and startup and CPU latency depend on the machine.
Windows uses PyTorch 2.6 and torchvision 0.21 to avoid a missing
`VCRUNTIME140_THREADS.dll` seen with newer PyTorch on the test machine.
MP4 and MOV support depends on the OpenCV codecs. With `--print-snapshots`, JSON snapshots
are printed as the video plays; leave it off when stdout is retained. Inference falling behind skips to the current
playback position. The input file remains on the user's machine and decoded
frames stay in memory. Each invocation has a new anonymous session. The state
distinguishes current detections, recently observed objects, active tracklets,
and departed tracklets; the result is a local debug stream, not persisted data.
`--model` can select another compatible Ultralytics model for benchmarking.

The generic detector reports its supported object classes only; it does not
infer clothing or brands. For optional local VLM enrichment, set `VLM_ENABLED=true`
and `ANTHROPIC_API_KEY` in the backend environment, then add `--vlm` to the
agent command. The selector uses a one-item queue, checks pose landmarks,
permanently blacks out the head and other overlapping people, and rejects
ambiguous images. The backend validates the opaque upper band before calling
the existing Anthropic AI adapter. A crop reference is an ephemeral identifier,
not a stored image. A slow or unavailable provider cannot block YOLO; its
results update the original session and track, including departed tracks.
Uncertainty is qualitative, not a calibrated probability.

The existing gateway has no agent/classifier authentication. Consequently both
the recognition HTTP read endpoint and VLM processing are disabled in `prod`;
the in-process classifier subscription remains available. Do not expose the
local gateway publicly. Audio inference remains disabled. Remote GPU failover,
browser upload/debug view and production VLM deployment remain unfinished.
Real-world tracking, redaction coverage and CPU throughput still need testing
on representative venue video and deployment hardware.

The agent is the device-side source for the [WebSocket contract](../app/contract/messages.py).

- [Vision](agent/vision) will capture latest-frame camera input, detect people
  and objects, and track anonymous session-local tracklets. Send structured
  observations as `audience.snapshot` and send a face-blurred JPEG with
  `audience.keyframe` for warm tagging.
- [Speech](agent/speech) will contribute sound-scene observations to
  `audience.snapshot`. In opt-in speech mode with a visible indicator, use VAD
  and speech recognition to emit ephemeral `audience.utterance` messages for
  topic extraction.

The [Audience context](../app/modules/audience/CONTEXT.md) turns recognized
facts into stable moments. Live camera capture and speech adapters are pending.
