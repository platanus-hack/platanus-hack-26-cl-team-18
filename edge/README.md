# Perception agent scaffold

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
