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

## Amendment (2026-11-21): implementation and verified API facts

Verified against the ElevenLabs docs for our **Creator** plan.

**What exists**
- **Speech-to-text, in the agent:** `edge/agent/speech/`. Audio goes mic →
  `SpeechGate` (Silero, or an energy VAD as fallback) → `ScribeRealtime` (raw
  WebSocket) → `SpeechPipeline`, which runs the keyterm matcher on
  *partial* transcripts plus the echo guard, and sends `audience.utterance`
  to the backend over `BackendLink`.
- **Text-to-speech, in the backend:** a `VoiceSynthesizer` port, implemented by
  `ElevenLabsVoiceSynthesizer` (raw httpx). `VoiceoverService` stores
  voice-overs in `FileMediaStore`, served at `/media/voiceovers/`, keyed by
  copy, voice, model, format and language. Voice-overs are generated on the
  warm path and sent as `creative.upgraded`. A cached voice-over is attached on
  the hot path.
- **Speech topics:** handled by `audience/domain/speech_evidence.py`. Keyterms
  map to segments that expire after a TTL, those segments merge into the
  moment, and the demo re-runs the auction ("it heard me"). The backend
  accepts speech only from screens listed in `SPEECH_MODE_SCREENS`.

**Speech-to-text facts that shaped the code**
- **Endpoint:** `wss://api.elevenlabs.io/v1/speech-to-text/realtime`, with
  `model_id=scribe_v2_realtime`, `audio_format=pcm_16000`,
  `commit_strategy=manual` and the `xi-api-key` header. `scribe_v3_live` is a
  drop-in successor (config only) to benchmark on day 1.
- **Inactivity:** the server closes the connection after **15 s without a
  client message**. The client sends a 96 ms silence chunk after 5 s idle.
  Silence is billed: $0.22/h realtime on Creator, which includes 100 h.
- **Chunks and commits:** chunks must be 0.1–1 s, so we send 96 ms or more.
  Commits come from our VAD at the end of each speech segment. Rapid commits
  degrade quality and the server auto-commits at about 36 s.
- **Keyterms:** at most 50 terms and 1,000 characters
  (`KeytermCatalog.prompt_terms` enforces both). Keyterms add $0.08/h.
- **Errors:** auth, quota and terms errors are fatal; the client stops and
  speech is reported degraded. Rate limits, resource limits and
  transcriber errors are transient; the client reconnects with backoff.
- **Concurrency:** Creator allows 15 realtime STT sessions.

**Text-to-speech facts**
- **Request:** `POST /v1/text-to-speech/{voice_id}?output_format=mp3_44100_128`
  with `model_id=eleven_flash_v2_5` and `language_code=es`. On Creator, 44.1 kHz
  PCM needs Pro.
- **Limits:** Flash on Creator allows 10 concurrent requests and includes
  550k characters a month.
- **Retries:** 429 (`rate_limit_exceeded`, `concurrent_limit_exceeded`,
  `system_busy`), 5xx and network errors are retried twice with jitter.
  401/403 count as auth and 402 as quota; either turns voice off for the
  session and raises `voice_fallback` on the console.
- **Why raw clients:** both protocols are small, and the SDK's realtime API
  has changed shape between versions. Raw clients give us control of
  keepalives, reconnects and error translation, and tests run against
  recorded-protocol fakes (`edge/tests/fake_scribe.py`, httpx `MockTransport`).

## Retention on Creator (open decision)

**Zero retention (`enable_logging=false`) is Enterprise-only.** On Creator,
ElevenLabs keeps STT audio and transcripts, and TTS text and audio. This
contradicts "audio and transcripts are never persisted" above for the
*provider* side. Our own process still never stores or logs them.

Until this is decided, speech mode stays off by default. The agent requires
`--speech-mode`, and the backend requires `SPEECH_MODE_SCREENS`.

The options are:
1. Enterprise zero retention.
2. Treat provider retention as a disclosed exception: signage, consent by
   action, and deleting history through the API.
3. On-device STT for ambient listening, with ElevenLabs only for consent-based
   "talk to the screen".

TTS of ad copy carries no personal data and is unaffected.
