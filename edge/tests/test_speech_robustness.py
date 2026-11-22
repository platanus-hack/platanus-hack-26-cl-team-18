"""Hardware-edge robustness for speech mode: VAD, mic capture, and the offline eval.

Every failure here is one we'd otherwise meet on stage: a 48 kHz-only
speakerphone, an unplugged USB cable, a loud hall, a broken ONNX model.
"""

from __future__ import annotations

import asyncio
import math
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

from agent.speech import robustness
from agent.speech.__main__ import _vad
from agent.speech.sources import (
    MicSource,
    PcmConverter,
    PcmFrameBuffer,
    Resampler,
    parse_device,
)
from agent.speech.vad import (
    FRAME_BYTES,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    EnergyVad,
    GateEvent,
    ResilientVad,
    SileroVad,
    SpeechGate,
    frame_dbfs,
)

SILENCE = bytes(FRAME_BYTES)


def tone(amplitude: float = 0.3, hz: float = 220.0) -> bytes:
    t = np.arange(FRAME_SAMPLES) / SAMPLE_RATE
    return (np.sin(2 * np.pi * hz * t) * amplitude * 32767).astype(np.int16).tobytes()


def noise(dbfs: float, rng: np.random.Generator) -> bytes:
    x = rng.standard_normal(FRAME_SAMPLES) * 32768 * 10 ** (dbfs / 20)
    return np.clip(x, -32768, 32767).astype(np.int16).tobytes()


class Scripted:
    """A VAD that returns the probabilities it's told to, and records resets."""

    def __init__(self, probabilities: list[float]) -> None:
        self._p = list(probabilities)
        self.resets = 0

    def speech_probability(self, frame: bytes) -> float:
        return self._p.pop(0) if self._p else 0.0

    def reset(self) -> None:
        self.resets += 1


def events(gate: SpeechGate, n: int) -> list[GateEvent]:
    return [o.event for _ in range(n) for o in gate.feed(SILENCE)]


# ------------------------------------------------------------------ gate hysteresis


def test_open_gate_survives_soft_syllables_between_end_and_start_thresholds() -> None:
    # 0.4 is below the 0.5 start threshold but above the 0.35 end threshold.
    vad = Scripted([0.9] * 3 + [0.4] * 20 + [0.0] * 10)
    gate = SpeechGate(vad, start_frames=3, hangover_frames=10, preroll_frames=3)
    kinds = events(gate, 33)
    assert kinds.count(GateEvent.SPEECH_START) == 1
    assert kinds.index(GateEvent.SPEECH_END) == len(kinds) - 1  # closed only after real silence


def test_soft_frames_cannot_open_a_closed_gate() -> None:
    gate = SpeechGate(Scripted([0.4] * 30), start_frames=3)
    assert events(gate, 30) == []


def test_gate_treats_a_nan_probability_as_silence() -> None:
    gate = SpeechGate(Scripted([math.nan] * 10), start_frames=1, preroll_frames=1)
    assert events(gate, 10) == []


def test_gate_reset_clears_the_model_state() -> None:
    vad = Scripted([])
    SpeechGate(vad).reset()
    assert vad.resets == 1


def test_a_reset_in_a_hall_that_never_goes_quiet_does_not_deafen_the_gate() -> None:
    # A mic hiccup or a Scribe reconnect resets the gate, which then waits for
    # silence so the damaged tail is never transcribed. Crowd babble is speech to
    # any VAD, so that silence never came: measured offline, speech mode stayed
    # deaf for the rest of the demo at every babble SNR, with Silero and energy.
    gate = SpeechGate(Scripted([0.9] * 2000), start_frames=3, hangover_frames=10)
    gate.reset()
    kinds = [o.event for i in range(200) for o in gate.feed(SILENCE)]
    assert GateEvent.SPEECH_START in kinds
    # It still skips the damaged tail: the gate stays shut for about a second.
    first = next(i for i in range(200) if _opens_after_reset(i))
    assert 20 <= first <= 45, first


def _opens_after_reset(frames: int) -> bool:
    gate = SpeechGate(Scripted([0.9] * (frames + 1)), start_frames=3, hangover_frames=10)
    gate.reset()
    opened = False
    for _ in range(frames + 1):
        opened = any(o.event is GateEvent.SPEECH_START for o in gate.feed(SILENCE)) or opened
    return opened


def test_end_threshold_must_not_exceed_the_start_threshold() -> None:
    with pytest.raises(ValueError):
        SpeechGate(Scripted([]), threshold=0.5, end_threshold=0.6)


# ------------------------------------------------------------------ energy VAD


def test_dc_offset_alone_is_not_speech() -> None:
    offset = np.full(FRAME_SAMPLES, 4000, dtype=np.int16).tobytes()
    assert frame_dbfs(offset) < -100
    assert EnergyVad().speech_probability(offset) == 0.0


def test_adaptive_floor_ignores_stationary_room_noise() -> None:
    rng = np.random.default_rng(1)
    energy = EnergyVad(adaptive=True)
    gate = SpeechGate(ResilientVad(energy, energy))
    opened = [o.event for _ in range(300) for o in gate.feed(noise(-38, rng))]
    # A fixed -42 dBFS threshold would hold this gate open for the whole run.
    assert GateEvent.SPEECH_START not in opened


def test_adaptive_floor_does_not_climb_while_someone_talks() -> None:
    rng = np.random.default_rng(2)
    energy = EnergyVad(adaptive=True)
    for _ in range(60):
        energy.speech_probability(noise(-60, rng))
    before = energy.threshold_dbfs
    for i in range(100):  # 3.2 s of speech with short gaps between words
        energy.speech_probability(tone(0.3) if i % 8 else noise(-60, rng))
    assert energy.threshold_dbfs - before < 3


def test_adaptive_floor_follows_a_room_that_gets_louder() -> None:
    rng = np.random.default_rng(3)
    energy = EnergyVad(adaptive=True)
    for _ in range(60):
        energy.speech_probability(noise(-65, rng))
    for _ in range(200):  # music starts: 6.4 s, longer than the 4 s window
        energy.speech_probability(noise(-30, rng))
    assert energy.speech_probability(noise(-30, rng)) < 0.5


# ------------------------------------------------------------------ resilient VAD


class Exploding:
    def __init__(self, after: int) -> None:
        self.calls = 0
        self._after = after

    def speech_probability(self, frame: bytes) -> float:
        self.calls += 1
        if self.calls > self._after:
            raise RuntimeError("onnx session died")
        return 0.9


def test_model_failure_degrades_to_the_energy_vad_without_raising() -> None:
    vad = ResilientVad(Exploding(after=2))
    for _ in range(30):  # the agent has been listening to a quiet room
        vad.speech_probability(SILENCE)
    assert vad.speech_probability(tone()) == 0.9
    assert vad.speech_probability(tone()) == 0.9
    assert vad.speech_probability(tone()) == 1.0  # energy fallback now
    assert vad.failed_over
    assert vad.speech_probability(SILENCE) == 0.0


@pytest.mark.parametrize("garbage", [math.nan, -1.0, 7.0, math.inf])
def test_garbage_probability_degrades_to_the_fallback(garbage: float) -> None:
    vad = ResilientVad(Scripted([garbage]))
    vad.speech_probability(tone())
    assert vad.failed_over


def test_near_silence_never_reaches_the_model() -> None:
    model = Exploding(after=0)  # would raise if asked
    vad = ResilientVad(model, min_dbfs=-60)
    assert vad.speech_probability(SILENCE) == 0.0
    assert model.calls == 0 and not vad.failed_over


def test_the_fallback_is_calibrated_before_it_is_needed() -> None:
    rng = np.random.default_rng(4)
    fallback = EnergyVad(adaptive=True)
    vad = ResilientVad(Scripted([0.1] * 200), fallback)
    for _ in range(150):
        vad.speech_probability(noise(-40, rng))
    assert fallback.threshold_dbfs > -40  # it learned the room while Silero ran


def test_a_missing_or_corrupt_silero_model_falls_back_instead_of_crashing(tmp_path: Path) -> None:
    bad = tmp_path / "silero.onnx"
    bad.write_bytes(b"not a model")
    for model in (bad, None):
        vad = _vad(model)
        assert vad.speech_probability(SILENCE) == 0.0
        assert vad.speech_probability(tone()) > 0.5


SILERO = robustness.DEFAULT_SILERO


@pytest.mark.skipif(not SILERO.is_file(), reason="Silero model not downloaded")
def test_silero_reset_makes_scoring_independent_of_history() -> None:
    pytest.importorskip("onnxruntime")
    vad = SileroVad(SILERO)
    speech = robustness.to_frames(robustness.load_pcm("live_line_pcm16k.raw"))
    first = [vad.speech_probability(f) for f in speech[:40]]
    vad.reset()
    again = [vad.speech_probability(f) for f in speech[:40]]
    assert np.allclose(first, again, atol=1e-5)
    assert max(first) > 0.5


# ------------------------------------------------------------------ resampling


def _tone_at(rate: int, hz: float, seconds: float = 0.5) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return np.sin(2 * np.pi * hz * t) * 10_000


def _peak_hz(x: np.ndarray, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / rate)[spectrum.argmax()])


@pytest.mark.parametrize("rate", [48_000, 44_100, 32_000, 22_050, 8_000])
def test_resampler_keeps_speech_band_tones_at_their_pitch(rate: int) -> None:
    out = Resampler(rate).process(_tone_at(rate, 1000)).astype(np.float64)
    assert abs(len(out) - SAMPLE_RATE * 0.5) <= 2
    assert abs(_peak_hz(out[200:], SAMPLE_RATE) - 1000) < 20
    assert 0.8 < np.abs(out[200:]).max() / 10_000 < 1.1


def test_resampler_rejects_aliases_above_the_new_nyquist() -> None:
    # 12 kHz would fold back to 4 kHz (mid speech band) without the low-pass.
    out = Resampler(48_000).process(_tone_at(48_000, 12_000)).astype(np.float64)
    assert np.abs(out[200:]).max() < 10_000 * 10 ** (-30 / 20)


def test_resampler_output_does_not_depend_on_block_boundaries() -> None:
    x = _tone_at(48_000, 700) + _tone_at(48_000, 2300) * 0.3
    whole = Resampler(48_000).process(x)
    chunked_resampler = Resampler(48_000)
    sizes = [1536, 7, 999, 1536, 3, 4000]
    parts, at = [], 0
    while at < len(x):
        size = sizes[len(parts) % len(sizes)]
        parts.append(chunked_resampler.process(x[at : at + size]))
        at += size
    chunked = np.concatenate(parts)
    n = min(len(whole), len(chunked))
    assert abs(len(whole) - len(chunked)) <= 1
    assert np.abs(whole[:n].astype(int) - chunked[:n].astype(int)).max() <= 1


def test_converter_downmixes_stereo_and_carries_split_samples() -> None:
    convert = PcmConverter(SAMPLE_RATE, 2)
    stereo = np.array([1000, 3000, -2000, -4000], dtype=np.int16).tobytes()
    out = convert(stereo[:3]) + convert(stereo[3:])  # split inside a sample
    assert np.frombuffer(out, dtype=np.int16).tolist() == [2000, -3000]


def test_eval_resampled_speakerphone_path_passes() -> None:
    assert robustness.eval_resampled_device().passed


# ------------------------------------------------------------------ mic capture


class FakeStream:
    """Stands in for sounddevice.RawInputStream: a thread calling back with blocks."""

    def __init__(self, *, callback, samplerate, channels, blocksize, blocks, **_) -> None:  # type: ignore[no-untyped-def]
        self._callback = callback
        self._block = bytes(blocksize * channels * 2)
        self._blocks = blocks
        self._stop = threading.Event()
        self.samplerate = samplerate

    def __enter__(self) -> FakeStream:
        def run() -> None:
            for _ in range(self._blocks):
                if self._stop.wait(0.002):
                    return
                self._callback(self._block, 0, None, 0)
            # then the device goes silent: unplugged, no error raised

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()


class FakeDevice:
    def __init__(self, *, rates: set[int], fail_opens: int = 0, blocks: int = 20) -> None:
        self.rates = rates
        self.fail_opens = fail_opens
        self.blocks = blocks
        self.opened: list[tuple[int, int]] = []

    def query(self, device: object) -> dict[str, object]:
        return {"default_samplerate": 48_000.0, "max_input_channels": 2}

    @contextmanager
    def _noop(self):  # type: ignore[no-untyped-def]
        yield

    def open(self, **kwargs: object) -> FakeStream:
        if self.fail_opens:
            self.fail_opens -= 1
            raise OSError("device busy")
        if kwargs["samplerate"] not in self.rates:
            raise ValueError("Invalid sample rate")
        self.opened.append((kwargs["samplerate"], kwargs["channels"]))  # type: ignore[arg-type]
        return FakeStream(blocks=self.blocks, **kwargs)  # type: ignore[arg-type]


async def _take(source: MicSource, n: int, timeout: float = 5.0) -> list[bytes]:
    frames: list[bytes] = []

    async def collect() -> None:
        async for frame in source.frames():
            frames.append(frame)
            if len(frames) >= n:
                return

    await asyncio.wait_for(collect(), timeout)
    return frames


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_mic_that_refuses_16khz_is_opened_at_its_native_rate_and_resampled() -> None:
    device = FakeDevice(rates={48_000})
    mic = MicSource(open_stream=device.open, query_device=device.query, stall_s=1)
    frames = await _take(mic, 10)
    assert device.opened[0] == (48_000, 1)
    assert mic.format == (48_000, 1)
    assert all(len(f) == FRAME_BYTES for f in frames)


@pytest.mark.anyio
async def test_mic_prefers_16khz_mono_when_the_device_supports_it() -> None:
    device = FakeDevice(rates={16_000, 48_000})
    mic = MicSource(open_stream=device.open, query_device=device.query)
    await _take(mic, 5)
    assert device.opened == [(16_000, 1)]


@pytest.mark.anyio
async def test_a_stalled_mic_is_reopened_and_reported_as_a_discontinuity() -> None:
    device = FakeDevice(rates={16_000}, blocks=5)  # 5 blocks, then silence forever
    mic = MicSource(
        open_stream=device.open, query_device=device.query, stall_s=0.1, backoff=(0.01,)
    )
    frames = await _take(mic, 12)
    assert len(frames) == 12
    assert len(device.opened) >= 3
    assert mic.reopens >= 2 and mic.discontinuities >= 2


@pytest.mark.anyio
async def test_a_busy_device_is_retried_instead_of_ending_speech_mode() -> None:
    device = FakeDevice(rates={16_000}, fail_opens=4)  # every format candidate, once
    mic = MicSource(open_stream=device.open, query_device=device.query, backoff=(0.01,))
    await _take(mic, 5)
    assert mic.reopens == 1  # one failed round of format candidates, then success
    assert device.opened == [(16_000, 1)]


@pytest.mark.anyio
async def test_a_failing_device_query_still_tries_16khz() -> None:
    device = FakeDevice(rates={16_000})

    def broken(_: object) -> dict[str, object]:
        raise RuntimeError("PortAudio not initialized")

    mic = MicSource(open_stream=device.open, query_device=broken)
    await _take(mic, 3)
    assert device.opened == [(16_000, 1)]


def test_frame_buffer_clear_drops_partial_audio_from_a_dead_stream() -> None:
    buffer = PcmFrameBuffer(4)
    buffer.push(bytes(FRAME_BYTES + 10))
    buffer.clear()
    buffer.push(bytes(FRAME_BYTES - 10))
    assert buffer.pop() is None  # the 10 stale bytes were not stitched on


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("3", 3), (" 12 ", 12), ("Jabra", "Jabra"), ("", None), (None, None), (2, 2)],
)
def test_device_argument_accepts_an_index_or_a_name(raw: object, expected: object) -> None:
    assert parse_device(raw) == expected  # type: ignore[arg-type]


# ------------------------------------------------------------------ offline eval gate


@pytest.mark.timeout(300)  # Silero on CPU (edge[audio] + the model) takes ~90 s
def test_offline_robustness_eval_has_no_gated_failures() -> None:
    """Real TTS speech x venue conditions x shipped VADs (docs: agent/speech/robustness.py)."""
    results = robustness.run_offline(SILERO)
    failures = [
        f"{r.vad}/{r.line}/{r.condition}: {'; '.join(r.failures)}"
        for r in results
        if r.gated and not r.passed
    ]
    assert not failures, "\n".join(failures)
    assert {r.vad for r in results} >= {"energy_adaptive"}
