"""Sound scene: coarse acoustic labels from synthetic signals, never words or audio."""

from __future__ import annotations

import json
import wave
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from agent.contract import AGENT_INBOUND, AudienceSnapshot
from agent.sound.scene import SAMPLE_RATE, SceneLabel, classify_window, features
from agent.sound.sources import MicSoundScene, StaticSoundScene, WavSoundScene, scene_timeline
from agent.vision.pipeline import VisionPipeline
from agent.vision.snapshot import SnapshotBuilder, SnapshotConfig
from agent.vision.tracking import PersonTracker, TrackerConfig
from agent.vision.types import Box, FrameDetections, PersonDetection

SR = SAMPLE_RATE
T = np.arange(SR) / SR
RNG = np.random.default_rng(7)


def at_level(x: np.ndarray, dbfs: float) -> np.ndarray:
    return x / np.sqrt(np.mean(x**2)) * 10 ** (dbfs / 20)


def highpass(x: np.ndarray, cutoff_hz: float) -> np.ndarray:
    spectrum = np.fft.rfft(x)
    spectrum[np.fft.rfftfreq(len(x), 1 / SR) < cutoff_hz] = 0
    return np.fft.irfft(spectrum, len(x))


def chord(notes: list[float], seconds: np.ndarray) -> np.ndarray:
    return sum(np.sin(2 * np.pi * f * h * seconds) / h for f in notes for h in (1, 2, 3))  # type: ignore[return-value]


def silence() -> np.ndarray:
    return RNG.normal(0, 1e-4, SR)


def speech_band_tone() -> np.ndarray:
    """1 kHz + 500 Hz tones, amplitude-modulated at 4 Hz (the syllable rate)."""
    syllables = (0.5 + 0.5 * np.sin(2 * np.pi * 4 * T)) ** 2
    x = np.sin(2 * np.pi * 1000 * T) * syllables + 0.5 * np.sin(2 * np.pi * 500 * T) * np.roll(
        syllables, 1000
    )
    return at_level(x, -25)


def hiss_bursts() -> np.ndarray:
    """High-passed noise in four 120 ms bursts: steam-wand pulses / clatter."""
    gate = np.zeros(SR)
    for start in (0.05, 0.3, 0.55, 0.8):
        gate[int(start * SR) : int((start + 0.12) * SR)] = 1
    return at_level(highpass(RNG.normal(0, 1, SR), 3000) * gate, -30)


def steady_hiss() -> np.ndarray:
    return at_level(highpass(RNG.normal(0, 1, SR), 3000), -30)


def music() -> np.ndarray:
    """Two sustained major triads with harmonics, a chord change halfway."""
    half = SR // 2
    x = np.concatenate([chord([262, 330, 392], T[:half]), chord([294, 370, 440], T[half:])])
    return at_level(x, -25) + at_level(RNG.normal(0, 1, SR), -45)


def loud_noise() -> np.ndarray:
    return at_level(RNG.normal(0, 1, SR), -20)


CASES = [
    ("silence", silence, "quiet", set()),
    ("speech-band modulated tone", speech_band_tone, "conversation", {"music"}),
    ("hiss bursts", hiss_bursts, "coffee service fixture", {"conversation", "music"}),
    ("steady hiss: no bursts", steady_hiss, None, {"coffee service fixture", "music"}),
    ("music-like tones", music, "music", {"conversation", "coffee service fixture"}),
    ("loud broadband noise", loud_noise, "busy venue", {"music", "conversation"}),
]


@pytest.mark.parametrize(("name", "make", "top", "absent"), CASES, ids=[c[0] for c in CASES])
def test_sound_scene_table(name: str, make, top: str | None, absent: set[str]) -> None:  # type: ignore[no-untyped-def]
    labels = classify_window(make())
    names = [label.label for label in labels]
    if top is None:
        assert not names or names[0] != "coffee service fixture"
    else:
        assert names and names[0] == top, (name, labels)
    assert not absent & set(names), (name, labels)
    assert all(0 < label.score < 1 for label in labels)
    assert len(labels) <= 3


def test_quiet_is_exclusive() -> None:
    assert classify_window(silence()) == (SceneLabel("quiet", 0.95),)


def test_features_need_enough_audio() -> None:
    with pytest.raises(ValueError):
        features(np.zeros(1000))


def test_timeline_hops_every_half_second() -> None:
    signal = np.concatenate([silence(), loud_noise(), loud_noise()])
    timeline = scene_timeline(signal)
    assert [end for end, _ in timeline] == [1.0, 1.5, 2.0, 2.5, 3.0]
    assert timeline[0][1][0].label == "quiet" and timeline[-1][1][0].label == "busy venue"


def test_mic_keeps_only_the_latest_labels_in_a_one_second_ring() -> None:
    mic = MicSoundScene()  # never started: blocks are fed by hand, no sounddevice
    now = datetime(2026, 11, 21, tzinfo=UTC)
    assert mic.current(now) == ()
    for block in np.split(silence(), 32):  # 1 s in PortAudio-sized blocks
        mic.feed(block)
    assert mic.current(now)[0].label == "quiet"
    for block in np.split(hiss_bursts(), 32):
        mic.feed(block)
    assert mic.current(now)[0].label == "coffee service fixture"
    mic.close()
    assert mic.current(now) == ()
    assert not mic._ring.any()  # the buffer is zeroed on close


def _write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SR)
        wav.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


def test_wav_replays_on_the_vision_clock(tmp_path: Path) -> None:
    path = tmp_path / "room.wav"
    _write_wav(path, np.concatenate([silence(), hiss_bursts(), hiss_bursts()]))
    scene = WavSoundScene(path)
    t0 = datetime(2026, 11, 21, tzinfo=UTC)
    assert scene.current(t0) == ()  # no full window yet
    assert scene.current(t0 + timedelta(seconds=1.2))[0].label == "quiet"
    assert scene.current(t0 + timedelta(seconds=2.6))[0].label == "coffee service fixture"
    assert scene.current(t0 + timedelta(seconds=9)) == ()  # file over


# ------------------------------------------------------------- through the snapshot


class Link:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def send(self, message: object, *, binary: bytes | None = None) -> None:
        self.sent.append(message)


PERSON = PersonDetection(box=Box(400, 60, 600, 700), score=0.9)


def _run(sound: StaticSoundScene, script: Sequence[tuple[str, ...]]) -> list[AudienceSnapshot]:
    link = Link()
    pipeline = VisionPipeline(
        screen_id="demo-01",
        source=None,  # type: ignore[arg-type]  # process() only
        detector=None,  # type: ignore[arg-type]
        link=link,
        tracker=PersonTracker(TrackerConfig(min_hits=1)),
        snapshots=SnapshotBuilder("demo-01", SnapshotConfig()),
        sound=sound,
    )
    t0 = datetime(2026, 11, 21, tzinfo=UTC)
    for i, labels in enumerate(script):
        sound.labels = tuple(SceneLabel(label, 0.8) for label in labels)
        frame = FrameDetections(
            measured_at=t0 + timedelta(milliseconds=100 * i),
            width=1280,
            height=720,
            persons=(PERSON,),
        )
        pipeline.process(object(), frame)
    return [m for m in link.sent if isinstance(m, AudienceSnapshot)]


def test_a_sound_label_blip_is_debounced_and_a_stable_one_is_published() -> None:
    blip = _run(StaticSoundScene(), [()] * 5 + [("music",)] + [()] * 10)
    assert all(s.sound_scene == [] for s in blip)
    stable = _run(StaticSoundScene(), [()] * 5 + [("coffee service fixture",)] * 10)
    published = [s for s in stable if s.sound_scene]
    assert published and published[0].sound_scene[0].label == "coffee service fixture"
    wire = published[0].model_dump_json()
    assert isinstance(AGENT_INBOUND.validate_json(wire), AudienceSnapshot)
    assert set(json.loads(wire)) == set(AudienceSnapshot.model_fields)
    assert set(json.loads(wire)["sound_scene"][0]) == {"label", "score"}
