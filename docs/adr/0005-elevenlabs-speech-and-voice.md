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

## Amendment (2026-11-21): credit discipline and first live run

The account has about 10,000 credits, so every path that can spend them is capped.

**Spending controls in the code**
- **TTS has a persistent budget.** `ELEVENLABS_CHAR_BUDGET` defaults to 2000
  characters, and the ledger is `var/elevenlabs-budget.json`. It lives outside
  `/media` on purpose, because that directory is publicly served. A new line
  that doesn't fit is refused before any call; cache hits stay free. A corrupt
  ledger fails closed, and the console raises `voice_fallback`.
- **Cached voice-overs still play once calls stop.** After auth, quota or
  budget errors, voice-overs already in the cache keep playing.
- **STT sessions open lazily.** No Scribe session exists until there is
  speech, and the session closes after 60 s without speech (`idle_close_s`).
  An empty venue bills nothing. The cost is one connection setup on the first
  utterance after a quiet minute.
- **Live tests are cheap to rerun.** The edge round trip caches its TTS audio
  in `edge/tests/fixtures/`, and the backend test caches its voice-over in
  `var/live-cache/`. Reruns cost 0 TTS characters.

**The API key is scoped**
- It has: TTS, and speech-to-text including realtime tokens.
- It lacks: `voices_read` and `user_read`, so voices can't be listed and the
  balance can't be read through the API.
- Consequences: spend is tracked from the `character-cost` response header,
  and the narrator voice id must be pasted from the dashboard.
- The tests use the premade voice `JBFqnCBsd6RMkjVDRZzb`, which is English;
  pick a Spanish one for the demo.

**First live run, from Santiago**
- **TTS:** Flash v2.5 in Spanish returned an MP3 in 491 ms. ElevenLabs charged
  18 credits for 35 characters, about half a credit per character on Flash.
- **STT:** Scribe v2 Realtime returned the committed transcript 206 ms after
  our commit. The early keyterm utterance (`hambre`, `café`) fired from the
  partial transcript.
- **Total spent:** about 36 credits and about 10 s of realtime STT.

## Amendment (2026-11-21): latency tuning from live measurements in Santiago

These numbers come from `edge/agent/speech/bench.py`, which runs the real
Scribe API through our own VAD and client, using a TTS fixture with
per-character timings. Medians of 3 runs. The test line is *"Uf, qué hambre.
¿Vamos por un café después del gimnasio?"*.

| Variant | keyterm → partial | first partial | speech end → final | WER |
|---|---|---|---|---|
| `scribe_v2_realtime`, our VAD at 512 ms | 1,214 ms | 3,547 ms | 446 ms | 0.00 |
| `scribe_v3_live`, our VAD at 512 ms | 208 ms | 607 ms | 431 ms | 0.00 |
| **`scribe_v3_live`, our VAD at 320 ms** | **201 ms** | **593 ms** | **227 ms** | **0.00** |
| `scribe_v3_live`, server VAD at 0.3 s | 334 ms | 699 ms | 395 ms | 0.00 |
| `scribe_v3_live`, `turn_prediction` | 560 ms | 677 ms | no commit within 3 s | — |

**Speech-to-text decisions**
- **Default model:** `scribe_v3_live`, which meets the stack doc's day-1 switch
  condition. v2 buffers about 2 s before its first partial, while v3's first
  partial arrives in about 0.6 s. The docs also report 44% lower semantic WER.
- **Commits:** our own VAD commits, with a **320 ms** hangover. Server VAD and
  turn prediction were both slower on this line.
- **Network:** from Santiago every request makes a round trip of about 200 ms
  to `us-central1`, the region reported in the `x-region` header. The TCP and
  TLS handshakes complete at a local Google edge in under 15 ms.
  `api.us.elevenlabs.io` was slower, at 223 ms.

**Text-to-speech decisions**
- **Latency:** Flash v2.5 takes about 320 ms on a warm connection, about 200 ms
  of which is that round trip. Turning `use_speaker_boost` off made no
  measurable difference (323 ms against 349 ms), so it stays on.
- **What gets spoken:** the **approved offer line**, which is stable per tier,
  not the AI copy. That keeps the set of lines known in advance, so they can
  be pre-generated once and every play is a cache hit.
- **Normalization:** prices, counts and symbols are spelled out in our own
  code (`speech_text.py`), because Flash disables text normalization for
  latency. A round trip proved it: the aired line transcribes back as
  "…para dos por 5,990 pesos".
- **Pre-generation:** `VOICEOVER_PREWARM=true` synthesizes the demo's 7 offer
  lines (562 characters, about 281 credits) once at startup, and they stay
  cached on disk. On a cache hit the voice-over goes inside `creative.ready`
  itself, so it plays with the first frame and no upgrade round trip is
  needed.
- **Connection:** the HTTP connection is kept alive for 120 s (httpx defaults
  to 5 s) and warmed at startup.

**Cost of tuning:** about 175 credits in total, for fixtures, the A/B test and
round trips. Reruns cost 0 TTS characters.
