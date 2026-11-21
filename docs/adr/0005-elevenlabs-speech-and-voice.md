# 0005: ElevenLabs for speech recognition and voice-overs

- Status: accepted
- Date: 2026-11-21

## Context

Voice adds two things. Speech is the strongest intent signal in front of a
screen ("qué hambre"), and a spoken line makes a creative noticeable in a
busy space. Both need low latency and good Spanish.

## Decision

- **Speech-to-text:** ElevenLabs Scribe realtime over WebSocket from the
  perception agent, VAD-gated, with keyterm prompting (brands, categories,
  Chilean Spanish). Transcripts feed a `TopicExtractor` (instant keyterm map
  plus LLM structured output) that turns speech into IAB segments with a TTL.
- **Text-to-speech:** ElevenLabs Flash v2.5 (streaming, low latency) for
  creative voice-overs and "talk to the screen" replies. Voice-overs are
  cached by `(copy_hash, voice_id, model)`. Solo creatives use the brand
  voice; co-brand creatives use a neutral narrator voice.
- Both sit behind ports (`SpeechTranscriber`, `VoiceSynthesizer`) and are
  chosen in `app/bootstrap/` (agent: its own composition root).

## Privacy rules (binding)

- Speech mode is off by default, per screen, and shows a visible listening
  indicator when on.
- Audio and transcripts are never persisted or logged. Only aggregate topics
  with a TTL survive.
- No speaker identification and no voice embeddings.
- The pitch frames ambient listening as opt-in venues with signage; "talk to
  the screen" is consent by action.

## Consequences

- Speech topics are a warm-path signal and never block the auction.
- Needs network; when it's down, speech is reported as degraded and the loop
  continues without it.
- Latency figures (~150 ms Scribe v2 Realtime median, ~75–100 ms Flash) are
  ElevenLabs-reported model latency. Measure end to end from the venue.
