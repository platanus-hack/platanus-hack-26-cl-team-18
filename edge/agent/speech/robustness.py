"""Speech robustness eval: real speech, degraded the way a venue degrades it.

    cd edge && uv run python -m agent.speech.robustness            # offline, free
    cd edge && uv run python -m agent.speech.robustness --live     # + real Scribe

Offline tier (deterministic, no network): the authored TTS fixtures in
`tests/fixtures` are mixed with venue conditions (far talker, hot clipped mic,
DC offset, white noise, mains hum, crowd babble, a two-part request, a 48 kHz
stereo speakerphone) and streamed through the real `SpeechGate` with each VAD
we can ship (Silero when the model is present, the adaptive energy fallback).
Gates per clip: the gate streams >= 97 % of the voiced frames, opens no more
segments than the line has sentences, closes after the speech, ends within
1.3 s of the last voiced frame, and never opens on noise alone. Venue tier:
pink noise, music and applause at 5-10 dB SNR; a gate reset mid-babble must
still hear the next request; and 60 s of hall ambience with nobody speaking
(stationary noise must open nothing; applause and babble are reported as
opens/min, i.e. billed streaming and chances for a spurious topic). For the
"coverage" column of a no-speech row, read "share of time streamed".

Live tier (`--live`, spends a few cents of realtime STT): the same degraded
clips, with the room still talking for 4 s after the line, go through
`ScribeRealtime` + `SpeechPipeline`. In every condition, babble included, the
final request must carry exactly the line's keyterms, arrive within 3 s of
the end of speech, and alcohol must never be requested early. That is the recognizer's
accuracy under noise, measured on our own path, not a vendor benchmark.

All audio is synthetic (TTS fixtures + generated noise). No microphone audio
is recorded, and transcripts are only compared in memory, never written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import zlib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from agent.speech.sources import PcmConverter
from agent.speech.vad import (
    FRAME_BYTES,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    EnergyVad,
    GateEvent,
    ResilientVad,
    SileroVad,
    SpeechGate,
    VoiceActivityDetector,
)

EDGE = Path(__file__).resolve().parents[2]
FIXTURES = EDGE / "tests" / "fixtures"
DEFAULT_SILERO = EDGE.parent / "var" / "local-demo" / "silero_vad.onnx"
FRAME_S = FRAME_SAMPLES / SAMPLE_RATE

MIN_COVERAGE = 0.97
MAX_END_LAG_MS = 1300.0


@dataclass(frozen=True)
class Line:
    name: str
    fixture: str
    sentences: int  # upper bound on gate segments for this line
    keyterms: tuple[str, ...]  # expected final wanted intents (live tier)
    # Intents actually said, even if later corrected ("una cerveza, mejor un café"):
    # an early partial may show them. Anything else early is a hallucination.
    spoken: tuple[str, ...] = ()


LINES: tuple[Line, ...] = (
    Line("hunger_coffee", "live_line_pcm16k.raw", 2, ("hambre", "café")),
    Line("hunger_coffee_gym", "bench_line_pcm16k.raw", 2, ("hambre", "café", "gym")),
    Line("correction", "correction_line_pcm16k.raw", 2, ("café",), ("cerveza",)),
)


def load_pcm(name: str) -> np.ndarray:
    return np.frombuffer((FIXTURES / name).read_bytes(), dtype=np.int16).astype(np.float64)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x**2))) if x.size else 0.0


def _at_snr(noise: np.ndarray, speech: np.ndarray, snr_db: float) -> np.ndarray:
    voiced = speech[np.abs(speech) > 0.05 * np.abs(speech).max()]
    return noise * (_rms(voiced) / max(_rms(noise), 1e-9)) / (10 ** (snr_db / 20))


def _babble(length: int, rng: np.random.Generator) -> np.ndarray:
    """Overlapping reversed speech: speech-like spectrum, no intelligible keyterms."""
    voices = [load_pcm(line.fixture)[::-1] for line in LINES] + [
        load_pcm("offer_line_pcm16k.raw")[::-1]
    ]
    out = np.zeros(length)
    for voice in voices:
        tiled = np.tile(voice, length // len(voice) + 2)
        start = int(rng.integers(0, len(voice)))
        out += tiled[start : start + length]
    return out


# A condition maps (clean scene, speech-only track, rng) -> degraded scene.
type Degrade = Callable[[np.ndarray, np.ndarray, np.random.Generator], np.ndarray]


@dataclass(frozen=True)
class Condition:
    name: str
    degrade: Degrade
    stationary: bool = True  # stationary noise must never open the gate on its own
    gated: bool = True  # False: reported as a ratchet, not a pass/fail gate


def _hum(n: int) -> np.ndarray:
    t = np.arange(n) / SAMPLE_RATE
    harmonics = np.stack([np.sin(2 * np.pi * 50 * k * t) / k for k in (1, 2, 3)])
    return harmonics.sum(axis=0) * 0.03 * 32768


def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    """1/f noise: HVAC, a fridge, distant traffic."""
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.arange(len(spectrum), dtype=np.float64)
    freqs[0] = 1.0
    return np.fft.irfft(spectrum / np.sqrt(freqs), n)


def _music(n: int, rng: np.random.Generator) -> np.ndarray:
    """Background music: a looped chord progression with harmonics and a beat."""
    t = np.arange(n) / SAMPLE_RATE
    out = np.zeros(n)
    chords = ((220, 277, 330), (196, 247, 294), (175, 220, 262), (165, 208, 247))
    for start in range(0, n, SAMPLE_RATE):
        bar = slice(start, min(start + SAMPLE_RATE, n))
        for f0 in chords[(start // SAMPLE_RATE) % len(chords)]:
            for k in (1, 2, 3):
                out[bar] += np.sin(2 * np.pi * f0 * k * t[bar]) / k
    beat = (np.arange(n) % (SAMPLE_RATE // 2)) < 800
    return out + beat * rng.standard_normal(n) * 2


def _applause(n: int, rng: np.random.Generator) -> np.ndarray:
    """A demo hall: 3 s of applause (dense claps) every 8 s over a low floor."""
    out = rng.standard_normal(n) * 0.05
    decay = np.exp(-np.arange(400) / 60)
    for _ in range(int(n / SAMPLE_RATE * 15)):
        i = int(rng.integers(0, max(n - 400, 1)))
        out[i : i + 400] += (rng.standard_normal(400) * decay * 3)[: len(out[i : i + 400])]
    envelope = np.zeros(n)
    for start in range(0, n, 8 * SAMPLE_RATE):
        envelope[start : start + 3 * SAMPLE_RATE] = 1
    return out * (0.1 + envelope)


CONDITIONS: tuple[Condition, ...] = (
    Condition("clean", lambda x, s, r: x),
    Condition("far_quiet_-24dB", lambda x, s, r: x * 10 ** (-24 / 20)),
    Condition("hot_clipped_+20dB", lambda x, s, r: np.clip(x * 10, -32768, 32767)),
    Condition("dc_offset", lambda x, s, r: x + 3000),
    Condition("white_snr20", lambda x, s, r: x + _at_snr(r.standard_normal(len(x)), s, 20)),
    Condition("white_snr10", lambda x, s, r: x + _at_snr(r.standard_normal(len(x)), s, 10)),
    Condition("mains_hum", lambda x, s, r: x + _hum(len(x))),
    Condition("pink_snr5", lambda x, s, r: x + _at_snr(_pink(len(x), r), s, 5)),
    Condition("music_snr10", lambda x, s, r: x + _at_snr(_music(len(x), r), s, 10)),
    # Claps are impulsive broadband bursts: the energy fallback opens on them.
    Condition(
        "applause_snr5",
        lambda x, s, r: x + _at_snr(_applause(len(x), r), s, 5),
        stationary=False,
        gated=False,
    ),
    # Babble is speech: a VAD is *supposed* to fire on it. Reported, not gated.
    Condition(
        "crowd_babble_snr10",
        lambda x, s, r: x + _at_snr(_babble(len(x), r), s, 10),
        stationary=False,
        gated=False,
    ),
)


@dataclass
class GateResult:
    vad: str
    line: str
    condition: str
    coverage: float
    segments: int
    max_segments: int
    closed: bool
    end_lag_ms: float | None
    noise_opens: int
    gated: bool
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def scene(speech: np.ndarray, *, lead_s: float = 1.0, tail_s: float = 2.0) -> np.ndarray:
    return np.concatenate(
        [np.zeros(int(lead_s * SAMPLE_RATE)), speech, np.zeros(int(tail_s * SAMPLE_RATE))]
    )


def to_frames(x: np.ndarray) -> list[bytes]:
    pcm = np.clip(np.rint(x), -32768, 32767).astype(np.int16)
    pad = (-len(pcm)) % FRAME_SAMPLES
    pcm = np.concatenate([pcm, np.zeros(pad, dtype=np.int16)])
    return [pcm[i : i + FRAME_SAMPLES].tobytes() for i in range(0, len(pcm), FRAME_SAMPLES)]


def voiced_frames(clean: np.ndarray) -> np.ndarray:
    """Ground truth from the clean track: frames within 35 dB of the line's peak level."""
    frames = to_frames(clean)
    levels = np.array([_rms(np.frombuffer(f, dtype=np.int16).astype(np.float64)) for f in frames])
    return levels > levels.max() * 10 ** (-35 / 20)


@dataclass
class GateTrace:
    streamed: np.ndarray
    segments: int
    ends: list[int]
    open_at_end: bool


def run_gate(gate: SpeechGate, frames: list[bytes]) -> GateTrace:
    streamed = np.zeros(len(frames), dtype=bool)
    segments = 0
    ends: list[int] = []
    for i, frame in enumerate(frames):
        for out in gate.feed(frame):
            if out.event is GateEvent.SPEECH_START:
                segments += 1
            elif out.event is GateEvent.AUDIO:
                n = len(out.audio) // FRAME_BYTES
                streamed[max(0, i - n + 1) : i + 1] = True
            elif out.event is GateEvent.SPEECH_END:
                ends.append(i)
    return GateTrace(streamed, segments, ends, gate.is_open)


type VadFactory = Callable[[], VoiceActivityDetector]


def _energy_only() -> VoiceActivityDetector:
    """Exactly what `agent.speech.__main__` runs when Silero is unavailable."""
    energy = EnergyVad(adaptive=True)
    return ResilientVad(energy, energy)


def vad_factories(silero_model: Path | None) -> dict[str, VadFactory]:
    factories: dict[str, VadFactory] = {
        "energy_adaptive": _energy_only,
    }
    if silero_model is not None and silero_model.is_file():
        try:
            SileroVad(silero_model)
        except Exception as exc:  # onnxruntime missing: offline eval still runs
            print(f"silero unavailable ({type(exc).__name__}); energy only", file=sys.stderr)
        else:
            factories = {"silero": lambda: ResilientVad(SileroVad(silero_model))} | factories
    return factories


def eval_clip(vad_name: str, make_vad: VadFactory, line: Line, cond: Condition) -> GateResult:
    rng = np.random.default_rng(zlib.crc32(f"{line.name}|{cond.name}".encode()))
    speech = load_pcm(line.fixture)
    clean = scene(speech)
    degraded = cond.degrade(clean, clean, rng)
    truth = voiced_frames(clean)
    trace = run_gate(SpeechGate(make_vad()), to_frames(degraded))
    coverage = float(trace.streamed[truth].mean()) if truth.any() else 1.0
    last_voiced = int(np.flatnonzero(truth)[-1])
    after = [e for e in trace.ends if e >= last_voiced]
    lag = (after[0] - last_voiced) * FRAME_S * 1000 if after else None

    # The same condition with no one speaking: a stationary room must stay closed.
    ambience = cond.degrade(np.zeros(5 * SAMPLE_RATE), clean, rng)
    noise_opens = run_gate(SpeechGate(make_vad()), to_frames(ambience)).segments

    result = GateResult(
        vad_name,
        line.name,
        cond.name,
        round(coverage, 4),
        trace.segments,
        line.sentences,
        not trace.open_at_end,
        None if lag is None else round(lag, 1),
        noise_opens,
        cond.gated,
    )
    if coverage < MIN_COVERAGE:
        result.failures.append(f"coverage {coverage:.1%} < {MIN_COVERAGE:.0%}")
    if trace.segments > line.sentences:
        result.failures.append(f"{trace.segments} segments > {line.sentences}")
    if trace.segments == 0:
        result.failures.append("gate never opened")
    if not result.closed:
        result.failures.append("gate still open after speech")
    if lag is None or lag > MAX_END_LAG_MS:
        result.failures.append(f"end lag {lag} ms > {MAX_END_LAG_MS:.0f}")
    if cond.stationary and noise_opens:
        result.failures.append(f"noise alone opened {noise_opens} segment(s)")
    return result


def eval_two_part_request(vad_name: str, make_vad: VadFactory) -> GateResult:
    """Two lines 1.5 s apart must commit as separate turns (each reaches the auction)."""
    a, b = load_pcm(LINES[0].fixture), load_pcm(LINES[2].fixture)
    clean = scene(np.concatenate([a, np.zeros(int(1.5 * SAMPLE_RATE)), b]))
    truth = voiced_frames(clean)
    trace = run_gate(SpeechGate(make_vad()), to_frames(clean))
    coverage = float(trace.streamed[truth].mean())
    result = GateResult(
        vad_name,
        "two_part",
        "pause_1.5s",
        round(coverage, 4),
        trace.segments,
        4,
        not trace.open_at_end,
        None,
        0,
        True,
    )
    if trace.segments < 2:
        result.failures.append("two requests merged into one segment")
    if trace.segments > 4:
        result.failures.append(f"{trace.segments} segments > 4")
    if coverage < MIN_COVERAGE:
        result.failures.append(f"coverage {coverage:.1%} < {MIN_COVERAGE:.0%}")
    if not result.closed:
        result.failures.append("gate still open after speech")
    return result


def eval_resampled_device() -> GateResult:
    """A 48 kHz stereo speakerphone: our converter must hand Scribe the same speech."""
    speech = load_pcm(LINES[0].fixture)
    up = np.interp(np.arange(len(speech) * 3) / 3, np.arange(len(speech)), speech)
    stereo = np.stack([up, up * 0.8], axis=1).reshape(-1)
    raw = np.clip(np.rint(stereo), -32768, 32767).astype(np.int16).tobytes()
    convert = PcmConverter(48_000, 2)
    block = 1536 * 2 * 2  # PortAudio blocks of 32 ms at 48 kHz stereo
    out = b"".join(convert(raw[i : i + block]) for i in range(0, len(raw), block))
    got = np.frombuffer(out, dtype=np.int16).astype(np.float64)
    ref = speech * 0.9  # channel mean
    n = min(len(got), len(ref))
    # Best alignment within 0..16 samples (FIR group delay), then signal-to-error ratio.
    best = max(
        (
            10
            * math.log10(
                _rms(ref[20 : n - 20]) ** 2
                / max(_rms(ref[20 : n - 20] - got[20 + d : n - 20 + d]) ** 2, 1e-9)
            ),
            d,
        )
        for d in range(17)
    )
    result = GateResult(
        "converter",
        LINES[0].name,
        "48k_stereo",
        round(len(got) / len(speech), 4),
        0,
        0,
        True,
        None,
        0,
        True,
    )
    if abs(len(got) - len(speech)) > 64:
        result.failures.append(f"length {len(got)} vs {len(speech)} samples")
    if best[0] < 15:
        result.failures.append(f"signal-to-error {best[0]:.1f} dB < 15 dB")
    return result


def eval_reset_in_babble(vad_name: str, make_vad: VadFactory) -> GateResult:
    """A mic hiccup or Scribe reconnect mid-crowd must not leave speech mode deaf.

    The reset gate waits for silence before listening again; babble never goes
    quiet, so without a bounded wait the next request was never streamed.
    """
    rng = np.random.default_rng(zlib.crc32(b"reset|babble"))
    speech = load_pcm(LINES[0].fixture)
    clean = scene(speech, lead_s=4.0)
    truth = voiced_frames(clean)
    before = _at_snr(_babble(3 * SAMPLE_RATE, rng), clean, 10)
    after = clean + _at_snr(_babble(len(clean), rng), clean, 10)
    gate = SpeechGate(make_vad())
    run_gate(gate, to_frames(before))
    gate.reset()
    trace = run_gate(gate, to_frames(after))
    coverage = float(trace.streamed[truth].mean())
    result = GateResult(
        vad_name,
        LINES[0].name,
        "reset_in_babble",
        round(coverage, 4),
        trace.segments,
        99,
        True,
        None,
        0,
        True,
    )
    if coverage < MIN_COVERAGE:
        result.failures.append(f"coverage {coverage:.1%} < {MIN_COVERAGE:.0%} after a reset")
    return result


# Long ambience, nobody addressing the screen. Stationary noise must never open
# the gate (every open streams billed audio and can produce a spurious topic);
# applause, a PA and babble are reported per minute (a VAD hears them as speech).
VENUE_NOISES: tuple[tuple[str, Callable[[int, np.random.Generator], np.ndarray], bool], ...] = (
    ("pink", _pink, True),
    ("music", _music, True),
    ("applause", _applause, False),
    ("babble", _babble, False),
)
VENUE_NOISE_S = 60
VENUE_NOISE_DBFS = -21.0  # a loud hall at the speakerphone


def eval_venue_noise(
    vad_name: str,
    make_vad: VadFactory,
    name: str,
    make_noise: Callable[..., np.ndarray],
    gated: bool,
) -> GateResult:
    rng = np.random.default_rng(zlib.crc32(f"venue|{name}".encode()))
    n = VENUE_NOISE_S * SAMPLE_RATE
    noise = make_noise(n, rng)
    noise *= 32768 * 10 ** (VENUE_NOISE_DBFS / 20) / max(_rms(noise), 1e-9)
    trace = run_gate(SpeechGate(make_vad()), to_frames(noise))
    opens_per_min = trace.segments * 60 / VENUE_NOISE_S
    result = GateResult(
        vad_name,
        "(no speech)",
        f"{name}_{VENUE_NOISE_S}s",
        round(float(trace.streamed.mean()), 4),
        trace.segments,
        0,
        not trace.open_at_end,
        None,
        trace.segments,  # noise column: opens in the window
        gated,
    )
    if trace.segments:
        result.failures.append(
            f"{opens_per_min:.1f} opens/min, streaming {trace.streamed.mean():.0%} of the time"
        )
    return result


def run_offline(silero_model: Path | None) -> list[GateResult]:
    results: list[GateResult] = []
    for vad_name, make_vad in vad_factories(silero_model).items():
        results.extend(
            eval_clip(vad_name, make_vad, line, cond) for line in LINES for cond in CONDITIONS
        )
        results.append(eval_two_part_request(vad_name, make_vad))
        results.append(eval_reset_in_babble(vad_name, make_vad))
        results.extend(
            eval_venue_noise(vad_name, make_vad, name, make, gated)
            for name, make, gated in VENUE_NOISES
        )
    results.append(eval_resampled_device())
    return results


# ------------------------------------------------------------------ live tier


LIVE_CONDITIONS = (
    "clean",
    "far_quiet_-24dB",
    "hot_clipped_+20dB",
    "white_snr10",
    "crowd_babble_snr10",
)


@dataclass
class LiveResult:
    line: str
    condition: str
    expected: tuple[str, ...]
    heard: tuple[str, ...]
    first_hit_ms: float | None  # from speech onset; negative = fired before anyone spoke
    final_ms: float | None  # speech end -> the complete final request (no end-of-clip flush)
    false_early: tuple[str, ...]  # keyterms emitted before onset or outside the line's intents
    passed: bool  # right final intents, final within MAX_FINAL_MS, no early alcohol

    @property
    def clean(self) -> bool:
        return self.passed and not self.false_early


MAX_FINAL_MS = 3000.0
RESTRICTED = frozenset({"cerveza"})


def _final_request(messages: list, keyterms: object) -> tuple[str, ...]:  # type: ignore[type-arg]
    """Merge one viewer's finals across segments: later rejections win."""
    heard: dict[str, None] = {}
    for message in messages:
        if message.is_final and message.text:
            meaning = keyterms.understand(message.text)  # type: ignore[attr-defined]
            for rejected in meaning.rejected:
                heard.pop(rejected, None)
            heard.update(dict.fromkeys(meaning.wanted))
    return tuple(sorted(heard))


async def _live_clip(
    api_key: str, line: Line, cond: Condition, silero_model: Path | None
) -> LiveResult:
    from agent.contract import AudienceUtterance
    from agent.speech.keyterms import DEFAULT_KEYTERMS
    from agent.speech.pipeline import SpeechPipeline
    from agent.speech.scribe import ScribeConfig, ScribeRealtime

    rng = np.random.default_rng(zlib.crc32(f"{line.name}|{cond.name}".encode()))
    lead_s, tail_s = 1.0, 4.0  # the room keeps talking after the shopper stops
    speech = load_pcm(line.fixture)
    end_s = lead_s + len(speech) / SAMPLE_RATE
    clean = scene(speech, lead_s=lead_s, tail_s=tail_s)
    frames = to_frames(cond.degrade(clean, clean, rng))
    sent: list[AudienceUtterance] = []
    pipeline: SpeechPipeline | None = None
    scribe = ScribeRealtime(
        ScribeConfig(api_key=api_key, keyterms=DEFAULT_KEYTERMS.prompt_terms()),
        lambda event: pipeline.on_event(event) if pipeline else None,
        idle_close_s=None,
    )
    vad = next(iter(vad_factories(silero_model).values()))()
    pipeline = SpeechPipeline(
        screen_id="eval",
        gate=SpeechGate(vad),
        transcriber=scribe,
        keyterms=DEFAULT_KEYTERMS,
        emit=sent.append,
    )
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(scribe.run())
    started = loop.time()
    first_hit: float | None = None
    final_at: float | None = None
    expected = tuple(sorted(line.keyterms))
    false_early: dict[str, None] = {}
    seen = 0
    try:
        for frame in frames:
            pipeline.on_frame(frame)
            now_s = loop.time() - started
            for message in sent[seen:]:
                for term in message.keyterms_hit:
                    if now_s < lead_s or term not in (*line.keyterms, *line.spoken):
                        false_early[term] = None
                if message.keyterms_hit and first_hit is None:
                    first_hit = (now_s - lead_s) * 1000
            if (
                seen != len(sent)
                and final_at is None
                and now_s > end_s
                and _final_request(sent, DEFAULT_KEYTERMS) == expected
            ):
                final_at = (now_s - end_s) * 1000
            seen = len(sent)
            await asyncio.sleep(FRAME_S)
        pipeline.finish()
        try:
            async with asyncio.timeout(8):
                while scribe.stats.commits and not any(m.is_final for m in sent):
                    await asyncio.sleep(0.05)
                await asyncio.sleep(0.5)  # late segment finals
        except TimeoutError:
            pass
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    got = _final_request(sent, DEFAULT_KEYTERMS)
    return LiveResult(
        line.name,
        cond.name,
        line.keyterms,
        got,
        None if first_hit is None else round(first_hit),
        None if final_at is None else round(final_at),
        tuple(false_early),
        got == expected
        and final_at is not None
        and final_at <= MAX_FINAL_MS
        and not RESTRICTED.intersection(false_early),
    )


async def run_live(api_key: str, silero_model: Path | None) -> list[LiveResult]:
    by_name = {c.name: c for c in CONDITIONS}
    limit = asyncio.Semaphore(4)

    async def one(line: Line, cond: Condition) -> LiveResult:
        async with limit:
            return await _live_clip(api_key, line, cond, silero_model)

    return list(
        await asyncio.gather(
            *(one(line, by_name[name]) for line in LINES for name in LIVE_CONDITIONS)
        )
    )


def _api_key() -> str:
    if key := os.environ.get("ELEVENLABS_API_KEY"):
        return key
    for env in (EDGE / ".env", EDGE.parent / ".env"):
        if env.exists():
            for raw in env.read_text(encoding="utf-8").splitlines():
                name, _, value = raw.partition("=")
                if name.strip() == "ELEVENLABS_API_KEY" and value.strip():
                    return value.strip().strip('"').strip("'")
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.speech.robustness")
    parser.add_argument("--silero-model", type=Path, default=DEFAULT_SILERO)
    parser.add_argument("--live", action="store_true", help="also run real Scribe (paid)")
    parser.add_argument("--json", type=Path, help="write the scorecard as JSON")
    args = parser.parse_args(argv)

    offline = run_offline(args.silero_model)
    gate_fail = [r for r in offline if r.gated and not r.passed]
    print(
        f"{'vad':16} {'line':18} {'condition':20} {'cov':>6} {'seg':>4} {'lag ms':>7} {'noise':>5}"
    )
    for r in offline:
        mark = "ok " if r.passed else ("FAIL" if r.gated else "warn")
        lag = "-" if r.end_lag_ms is None else f"{r.end_lag_ms:.0f}"
        print(
            f"{r.vad:16} {r.line:18} {r.condition:20} {r.coverage:6.1%} {r.segments:>4} "
            f"{lag:>7} {r.noise_opens:>5}  {mark} {'; '.join(r.failures)}"
        )
    print(
        f"\noffline: {len(offline) - len(gate_fail)}/{len(offline)} pass (gated failures: {len(gate_fail)})"
    )

    live: list[LiveResult] = []
    if args.live:
        key = _api_key()
        if not key:
            print("--live needs ELEVENLABS_API_KEY", file=sys.stderr)
            return 2
        live = asyncio.run(run_live(key, args.silero_model))
        print(f"\n{'line':18} {'condition':20} {'heard':20} {'hit ms':>7} {'final ms':>8} early")
        for lr in live:
            mark = "ok" if lr.clean else ("warn" if lr.passed else "FAIL")
            print(
                f"{lr.line:18} {lr.condition:20} {','.join(lr.heard) or '-':20} "
                f"{lr.first_hit_ms if lr.first_hit_ms is not None else '-':>7} "
                f"{lr.final_ms if lr.final_ms is not None else '-':>8} "
                f"{','.join(lr.false_early) or '-':10} {mark}"
            )
        print(
            f"\nlive: {sum(lr.passed for lr in live)}/{len(live)} pass "
            f"({sum(lr.clean for lr in live)} with no early mistakes; gates: right final "
            f"intents, final <= {MAX_FINAL_MS:.0f} ms after speech, never early alcohol)"
        )

    if args.json:
        args.json.write_text(
            json.dumps(
                {"offline": [asdict(r) for r in offline], "live": [asdict(r) for r in live]},
                indent=1,
                ensure_ascii=False,
            )
        )
    live_fail = [lr for lr in live if not lr.passed]
    return 1 if gate_fail or live_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
