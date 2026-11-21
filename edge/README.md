# Perception agent

## Local video replay (first slice)

With Python 3.12 and uv installed, from `edge/` run:

```sh
uv sync
uv run on-sight /path/to/video.mp4 --fps 3
```

The first run downloads Ultralytics `yolo26n.pt`; model warmup runs before the
playback clock starts, and startup and CPU latency depend on the machine.
Windows uses PyTorch 2.6 and torchvision 0.21 to avoid a missing
`VCRUNTIME140_THREADS.dll` seen with newer PyTorch on the test machine.
MP4 and MOV support depends on the OpenCV codecs. JSON snapshots
are printed as the video plays; inference falling behind skips to the current
playback position. The input file remains on the user's machine and decoded
frames stay in memory. Each invocation has a new anonymous session. The state
distinguishes current detections, recently observed objects, active tracklets,
and departed tracklets; the result is a local debug stream, not persisted data.
`--model` can select another compatible Ultralytics model for benchmarking.

The generic detector reports its supported object classes only; clothing and
brands are not inferred. The source and detector ports are replaceable. The
audio port is disabled by default. Remote GPU failover, a face-blurred VLM
adapter, the backend WebSocket connection, browser upload/debug view, and
Render deployment are not implemented yet. In particular, no frames are sent
to a third party. CPU throughput and ByteTrack stability need measurement on
representative video and deployment hardware.

The agent is the device-side source for the [WebSocket contract](../app/contract/messages.py).

- [Vision](agent/vision) will capture latest-frame camera input, detect people
  and objects, and track anonymous session-local tracklets. Send structured
  observations as `audience.snapshot` and send a face-blurred JPEG with
  `audience.keyframe` for warm tagging.
- [Speech](agent/speech) will contribute sound-scene observations to
  `audience.snapshot`. In opt-in speech mode with a visible indicator, use VAD
  and speech recognition to emit ephemeral `audience.utterance` messages for
  topic extraction.

The [Audience context](../app/modules/audience/CONTEXT.md) turns these inputs into
stable moments. This scaffold creates package locations for the vision and
speech adapters. Hardware capture and provider clients are pending.
