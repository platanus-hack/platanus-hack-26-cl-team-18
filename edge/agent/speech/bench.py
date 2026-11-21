"""Speech latency + accuracy benchmark against the real Scribe Realtime API.

    cd edge && uv run python -m agent.speech.bench [--runs 3] [--variant v2 --variant v3]

Feeds a fixture (TTS audio with per-character timings, `tests/fixtures/`) through
our real VAD gate + ScribeRealtime client in real time, and measures, per
variant:

- keyterm latency: end of the spoken keyterm (from the TTS alignment) -> first
  partial transcript containing it. This is the "it heard me" signal.
- commit latency: our commit -> committed transcript.
- speech-end latency: end of speech in the audio -> committed transcript
  (includes our VAD hangover).
- accuracy: word error rate of the committed transcript vs the reference text.

Costs no TTS characters (the fixture is cached) and a few seconds of realtime
STT per run. Never logs transcript text beyond the benchmark report itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent.speech.keyterms import DEFAULT_KEYTERMS, normalize
from agent.speech.scribe import (
    CommittedTranscript,
    PartialTranscript,
    ScribeConfig,
    ScribeError,
    ScribeEvent,
    ScribeRealtime,
)
from agent.speech.vad import (
    FRAME_BYTES,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    EnergyVad,
    GateEvent,
    SpeechGate,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
FRAME_S = FRAME_SAMPLES / SAMPLE_RATE


@dataclass(frozen=True)
class Fixture:
    text: str
    pcm: bytes
    # keyterm canonical -> audio time (s) when its last character ends
    keyterm_ends: dict[str, float]
    speech_end_s: float

    @classmethod
    def load(cls, name: str = "bench_line") -> Fixture:
        meta = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        pcm = (FIXTURES / f"{name}_pcm16k.raw").read_bytes()
        chars: list[str] = meta["alignment"]["characters"]
        ends: list[float] = meta["alignment"]["character_end_times_seconds"]
        text = "".join(chars)
        keyterm_ends: dict[str, float] = {}
        lowered = text.lower()
        for canonical, variants in DEFAULT_KEYTERMS.variants.items():
            for surface in (canonical, *variants):
                idx = lowered.find(surface.lower())
                if idx >= 0 and canonical not in keyterm_ends:
                    keyterm_ends[canonical] = ends[idx + len(surface) - 1]
        return cls(text=text, pcm=pcm, keyterm_ends=keyterm_ends, speech_end_s=max(ends))


@dataclass(frozen=True)
class Variant:
    name: str
    model_id: str = "scribe_v2_realtime"
    hangover_frames: int = 16  # explicit: variants compare against the old defaults
    extra: tuple[tuple[str, str], ...] = ()
    commit_strategy: str = "manual"  # anything else: the server commits, we don't


VARIANTS = {
    "v2": Variant("v2"),
    "v2-fast-commit": Variant("v2-fast-commit", hangover_frames=10),
    "v3": Variant("v3", model_id="scribe_v3_live"),
    "v3-fast-commit": Variant("v3-fast-commit", model_id="scribe_v3_live", hangover_frames=10),
    "v3-turn": Variant("v3-turn", model_id="scribe_v3_live", commit_strategy="turn_prediction"),
    "v3-server-vad": Variant(
        "v3-server-vad",
        model_id="scribe_v3_live",
        commit_strategy="vad",
        extra=(("vad_silence_threshold_secs", "0.3"),),
    ),
}


@dataclass
class RunResult:
    keyterm_latency_ms: dict[str, float] = field(default_factory=dict)
    commit_latency_ms: float | None = None
    speech_end_latency_ms: float | None = None
    first_partial_ms: float | None = None
    transcript: str = ""
    error: str | None = None
    segments: int = 0


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference).split(), normalize(hypothesis).split()
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / max(1, len(ref))


async def run_once(fixture: Fixture, variant: Variant, api_key: str) -> RunResult:
    result = RunResult()
    t0: float | None = None
    commits: list[float] = []  # when we committed each speech segment
    finals: list[tuple[float, str]] = []  # committed transcripts as they arrived
    fed_all = False
    done = asyncio.Event()

    def on_event(event: ScribeEvent) -> None:
        nonlocal result
        now = time.perf_counter()
        if t0 is None:
            return
        if isinstance(event, PartialTranscript) and event.text:
            if result.first_partial_ms is None:
                result.first_partial_ms = 1000 * (now - t0)
            heard = f" {normalize(event.text)} "
            for canonical, end_s in fixture.keyterm_ends.items():
                if canonical in result.keyterm_latency_ms:
                    continue
                if any(
                    f" {normalize(v)} " in heard
                    for v in (canonical, *DEFAULT_KEYTERMS.variants[canonical])
                ):
                    result.keyterm_latency_ms[canonical] = 1000 * (now - (t0 + end_s))
        elif isinstance(event, CommittedTranscript):
            finals.append((now, event.text))
            if fed_all and len(finals) >= len(commits):
                done.set()
        elif isinstance(event, ScribeError):
            result.error = event.kind
            done.set()

    config = ScribeConfig(
        api_key=api_key,
        model_id=variant.model_id,
        keyterms=DEFAULT_KEYTERMS.prompt_terms(),
        extra_params=variant.extra,
        commit_strategy=variant.commit_strategy,
    )
    server_commits = variant.commit_strategy != "manual"
    scribe = ScribeRealtime(config, on_event, idle_close_s=None)
    gate = SpeechGate(EnergyVad(), hangover_frames=variant.hangover_frames)
    task = asyncio.create_task(scribe.run())
    try:
        await scribe.wait_connected(10)
        frames = [
            fixture.pcm[i : i + FRAME_BYTES].ljust(FRAME_BYTES, b"\0")
            for i in range(0, len(fixture.pcm), FRAME_BYTES)
        ]
        frames += [bytes(FRAME_BYTES)] * 40
        t0 = time.perf_counter()
        for index, frame in enumerate(frames):
            for out in gate.feed(frame):
                if out.event is GateEvent.AUDIO:
                    scribe.send_audio(out.audio)
                elif out.event is GateEvent.SPEECH_END:
                    if not server_commits:
                        scribe.commit()
                    commits.append(time.perf_counter())  # when our VAD would have committed
            # real time, against the absolute clock (no drift)
            await asyncio.sleep(max(0.0, t0 + (index + 1) * FRAME_S - time.perf_counter()))
        fed_all = True
        if server_commits:
            await asyncio.sleep(3)  # let the server decide; it commits on its own
        elif len(finals) < len(commits):
            await asyncio.wait_for(done.wait(), 10)
        result.transcript = " ".join(text for _, text in finals if text.strip())
        if finals and commits:
            result.commit_latency_ms = 1000 * statistics.median(
                f - c for (f, _), c in zip(finals, commits, strict=False)
            )
            result.speech_end_latency_ms = 1000 * (finals[-1][0] - (t0 + fixture.speech_end_s))
        result.segments = len(commits)
    except TimeoutError:
        result.error = result.error or "timeout"
    finally:
        task.cancel()
    return result


def _median(values: list[float]) -> str:
    return f"{statistics.median(values):6.0f}" if values else "     -"


async def main_async(args: argparse.Namespace) -> int:
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        print("ELEVENLABS_API_KEY is not set")
        return 2
    fixture = Fixture.load()
    print(f"fixture: {len(fixture.pcm) / 32000:.2f}s audio, keyterms at {fixture.keyterm_ends}")
    header = f"{'variant':<16} {'runs':>4} {'keyterm→partial':>16} {'1st partial':>12} {'commit→final':>13} {'speech end→final':>17} {'WER':>6}"
    print(header)
    for name in args.variant:
        variant = VARIANTS[name]
        results = [await run_once(fixture, variant, api_key) for _ in range(args.runs)]
        ok = [r for r in results if r.error is None]
        kt = [v for r in ok for v in r.keyterm_latency_ms.values()]
        wer = [word_error_rate(fixture.text, r.transcript) for r in ok]
        print(
            f"{name:<16} {len(ok):>2}/{len(results):<1} {_median(kt):>16} {_median([r.first_partial_ms for r in ok if r.first_partial_ms]):>12}"
            f" {_median([r.commit_latency_ms for r in ok if r.commit_latency_ms]):>13} {_median([r.speech_end_latency_ms for r in ok if r.speech_end_latency_ms]):>17}"
            f" {statistics.mean(wer) if wer else float('nan'):6.2f}"
            + (
                f"  errors: {[r.error for r in results if r.error]}"
                if len(ok) < len(results)
                else ""
            )
        )
        if args.show_transcripts and ok:
            print(f"    committed ({ok[-1].segments} segments): {ok[-1].transcript!r}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent.speech.bench")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--variant", action="append", choices=sorted(VARIANTS), default=None)
    parser.add_argument("--show-transcripts", action="store_true")
    args = parser.parse_args()
    args.variant = args.variant or ["v2", "v3"]
    from agent.speech.__main__ import _load_dotenv

    _load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    _load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
