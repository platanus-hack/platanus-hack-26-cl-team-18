# Perception agent

## Local video replay (first slice)

With Python 3.12 and uv installed, from `edge/` run:

```sh
uv sync --extra vision
uv run on-sight /path/to/video.mp4 --fps 3 --print-snapshots
```

With `--backend`, `on-sight` uses the same pose-based pipeline as
`python -m agent.vision`: it publishes `audience.snapshot` and face-blurred
keyframes, and drives ads for the given screen id. Start `make dev` from the
repository root, then run from `edge/`:

```sh
uv run on-sight /path/to/video.mp4 --fps 3 --screen-id demo-01 --backend ws://localhost:8000/ws/agent
```

For the detection-only workbench, add `--recognition-only` to the command.
That explicitly selects the debug `recognition.snapshot` feed and optional
`--vlm` crop enrichment. Without `--backend`, `on-sight` also stays in debug
mode; its `--model`, `--fps`, and `--print-snapshots` options apply to that mode.
Live mode uses the canonical pose/object models and face-blurred whole keyframes;
`AUDIENCE_AI` controls their warm enrichment.

In recognition-only mode, `GET /recognition/demo-01` returns the latest snapshot and backend receive
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

- [Vision](agent/vision) captures the latest camera frame, detects people and
  objects, tracks anonymous session-local tracklets, reads prop markers, and
  sends `audience.snapshot` plus a face-blurred JPEG with `audience.keyframe`
  for warm tagging. See "Vision (implemented)" below.
- [Speech](agent/speech) will contribute sound-scene observations to
  `audience.snapshot`. In opt-in speech mode with a visible indicator, use VAD
  and speech recognition to emit ephemeral `audience.utterance` messages for
  topic extraction.

The [Audience context](../app/modules/audience/CONTEXT.md) turns these inputs into
stable moments. This scaffold creates package locations for the vision and
speech adapters. Hardware capture and provider clients are pending.

## Vision (implemented)

```sh
cd edge && env -u VIRTUAL_ENV uv sync --extra dev --extra vision     # OpenCV + Ultralytics (torch)
cd edge && uv run python -m agent.vision --screen demo-01 --markers props/markers.json   # webcam 0
cd edge && uv run python -m agent.vision --screen demo-01 --video run.mp4                # replay mode (paced)
cd edge && uv run python -m agent.vision --screen replay --video clip.mp4 \
    --record ../evals/perception/recordings/clip.jsonl --offline                         # eval recording
# options: --pose-model / --object-model (pass exported .engine/.mlpackage/openvino weights),
#          --object-every 6, --device 0|cpu|mps, --no-keyframes
```

The frame path, in `agent/vision/` (ADR 0004, 0007, 0008):

- **Capture:** `capture.py`. `LatestFrameCamera` reads 1280×720 MJPG with a
  buffer of 1 in its own thread and keeps only the newest frame; a slow
  detector skips frames, never queues them. `VideoFileSource` replays a
  recorded video at its frame rate (replay mode).
- **Detection:** `adapters.py`. `UltralyticsDetector` runs YOLO26 pose every
  frame and YOLO26 objects every `--object-every` frames. `ArucoMarkerDetector`
  finds prop markers (about 7 ms). Provider types stop here: everything
  downstream uses `types.py`.
- **Failover:** `detector.py`. `FailoverDetector(primary, secondary)` switches
  from the remote GPU to local inference when the primary is down or its p95
  exceeds 150 ms, and probes back after 10 s. The remote adapter for `gpu/`
  is not written yet; it plugs in as `primary` (ADR 0007).
- **Geometry:** `geometry.py`. Looking = nose and both eyes visible with the
  nose centered; distance band from shoulder width; the blur box from face
  keypoints (top of the person box when keypoints are missing, so no face is
  left unblurred).
- **Tracking:** `tracking.py`. A greedy IoU tracker with session-counter ids
  (`t1`, `t2`, …, never reused), dwell, and looking smoothed over 500 ms. It
  runs on the laptop so failover never loses tracks.
- **Held products:** `held.py`. A marker near a person box, seen in 3 of the
  last 8 frames, is a held product. `props/markers.json` maps marker id →
  brand slug. Until the contract has a field, a held product travels as
  `DetectedObject(label="product:<slug>")` (ADR 0008 D2 has the two hand-offs).
- **Noise filters** (ADR 0008 D10): people count only when their box is at
  least 18 % of the frame height (far passers-by are not the audience) and
  after 2 detections (no one-frame ghosts); objects count only near a counted
  person.
- **Snapshots:** `snapshot.py`. Facts (people, looking, objects, held
  products) are published once stable for 500 ms (`--debounce-ms`); a snapshot
  goes out at once when the published facts change, otherwise as a 1 s
  heartbeat, at most 10 Hz. If a crowd never holds still, the most frequent
  facts of the last second are published every 1.5 s, so counts never freeze. Counts are the published facts; tracklets are the
  live samples used for attention. `minor_likely` comes from a pose
  heuristic (`minor.py`, ADR 0008 D13) that only blocks; the sound scene
  (`agent/sound`, `--sound` or `--sound-wav`) sends labels, never audio. Both
  are debounced facts.
- **Record mode** (ADR 0008 D11): `--record` reads the clip on clip time
  (`ClipSource`: sampled at `--sample-fps`, not paced, no wall clock) and
  writes the eval replay contract `{"t_s", "snapshot"}` per line, plus a
  `.meta.json` with inference p50/max. `make eval-perception` scores it.
- **Keyframes:** `pipeline.py` sends a blurred, 512 px JPEG at most every
  1.5 s while someone is in frame.

Tests: `tests/test_vision.py` uses synthetic detections and needs neither
OpenCV nor a camera (runs in CI). `tests/test_vision_opencv.py` exercises real
OpenCV (ArUco on every shipped marker, face blur, clip-time sampling) and
skips unless the `vision` extra is installed.

## Speech mode (implemented)

Speech mode is off unless a screen opts in (ADR 0005). Before turning it on,
read "Retention on Creator" in that ADR.

```sh
make edge-install                        # Python 3.12 project, separate from the backend
cp edge/.env.example edge/.env           # ELEVENLABS_API_KEY=...
cd edge && uv run python -m agent.speech --speech-mode --screen demo-01            # microphone
cd edge && uv run python -m agent.speech --speech-mode --wav line.wav              # replay a WAV
# For the backend to use the topics: SPEECH_MODE_SCREENS='["demo-01"]' make dev
```

The audio path, in `agent/speech/`:

- **Capture:** `sources.py` delivers 32 ms frames at 16 kHz, mono, int16.
  `MicSource` drops the oldest frame when it falls behind. `WavSource` replays
  a file.
- **Speech detection:** `vad.py`. `SpeechGate` applies pre-roll, hangover and
  a segment cap, using `SileroVad` (pass `--silero-model`) or `EnergyVad`.
- **Transcription:** `scribe.py` holds one long-lived Scribe Realtime session.
  It batches audio into chunks of at least 96 ms, commits manually at the end
  of each speech segment, and sends silence keepalives because the server
  closes after 15 s idle. Errors are classified: fatal ones stop the client
  and mark speech degraded; the rest reconnect with backoff.
- **Keyterms:** `keyterms.py` matches accent- and word-boundary-aware keyterms
  on *partial* transcripts and reports each one once per segment, including
  Chilean variants. The same catalog is sent to Scribe as keyterm prompts.
- **Echo guard:** `echo_guard.py` drops our own voice-overs, both inside
  playback windows and by fuzzy match against the line being played.
- **Output:** `pipeline.py` emits `audience.utterance`. A keyterm on a partial
  transcript is sent immediately; the committed transcript follows. The text
  is never logged.
- **Backend link:** `../backend_link.py` is a reconnecting `/ws/agent` client
  with a bounded outbox that drops the oldest message first.

Tests (`make edge-check`) run against `tests/fake_scribe.py`, a local server
that speaks the Scribe protocol. `make test-live` runs the real round trip:
ElevenLabs TTS speaks a line, our pipeline transcribes it, and the test
asserts the keyterms. It needs a key and spends credits.

`agent/contract.py` is a verbatim copy of `app/contract/messages.py`, written
by `make contract`. Never edit it here.
