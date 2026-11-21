"""Classify ~1 s of room audio into a few sound-scene labels from coarse features.

Pure and deterministic (numpy only). No speech recognition, no voice features,
no embeddings: the features below describe *how the room sounds* (level, where
the energy sits in the spectrum, how noisy it is, how it pulses), which carries
no words and no identity. Audio windows are never stored; only the labels and
scores leave (as `SoundScene` entries in `audience.snapshot`).

Features (32 ms Hann frames, 16 kHz mono):

- `rms_dbfs`: overall level.
- `centroid_hz`, `flatness`: spectral centroid and flatness (1 = white noise,
  0 = a pure tone), averaged over the non-silent frames; `hf_flatness` is the
  flatness above 4 kHz only (hiss is noise-like there).
- `concentration`: share of energy in the 16 strongest of 256 bins (tones ≈ 1,
  noise and babble low).
- `speech_band`: share of energy in 300–3400 Hz; `high_band`: share above 4 kHz.
- `mod_depth`, `syllabic`: how much the loudness envelope pulses (std/mean)
  and the share of that pulsing at 2–8 Hz, the syllable rate of speech.
- `onset_rate`: sudden loudness jumps (≥ `onset_db` between frames) per second.
- `peaks`: distinct spectral peaks within 20 dB of the strongest (harmonic richness).

Labels, each a min() of linear ramps so every score is explainable:

- **quiet**: level below `quiet_dbfs`. Exclusive.
- **conversation**: speech-band energy that pulses at the syllable rate and is
  not noise-like. Speech *energy*, never words.
- **music**: energy concentrated in a few (3–16) spectral peaks, with a
  steady envelope.
- **busy venue**: loud and spread out (not a few tones), with frequent onsets
  or broadband noise.
- **coffee service fixture**: high-frequency broadband hiss that comes in
  bursts (steam wand pulses, cup and spoon clatter). A **demo heuristic**, not
  a trained detector: any bursty hiss (a fan, a spray) also matches. The label
  matches the backend's `sound_catalog` in `app/bootstrap/demo.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16_000
FRAME = 512  # 32 ms
_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class SceneConfig:
    quiet_dbfs: float = -50.0
    active_dbfs: float = -60.0  # frames below this are ignored for spectral shape
    onset_db: float = 9.0
    min_score: float = 0.5  # labels below this are not reported
    max_labels: int = 3
    max_score: float = 0.95  # a heuristic is never certain


@dataclass(frozen=True, slots=True)
class SceneFeatures:
    rms_dbfs: float
    centroid_hz: float
    flatness: float
    hf_flatness: float
    concentration: float
    speech_band: float
    high_band: float
    mod_depth: float
    syllabic: float
    onset_rate: float
    peaks: int


@dataclass(frozen=True, slots=True)
class SceneLabel:
    label: str
    score: float


def _ramp(value: float, start: float, full: float) -> float:
    t = (value - start) / (full - start)
    return min(1.0, max(0.0, t))


def _db(power: np.ndarray | float) -> np.ndarray:
    return 10 * np.log10(np.asarray(power) + _EPS)


def _flatness(power: np.ndarray) -> float:
    """Geometric over arithmetic mean of the power spectrum, averaged over frames."""
    if power.size == 0:
        return 0.0
    geometric = np.exp(np.mean(np.log(power + _EPS), axis=1))
    return float(np.mean(geometric / (power.mean(axis=1) + _EPS)))


def to_float(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0


def features(
    samples: np.ndarray, sample_rate: int = SAMPLE_RATE, config: SceneConfig | None = None
) -> SceneFeatures:
    """Coarse features of a mono float window in [-1, 1] (≥ 4 frames, ~1 s is best)."""
    config = config or SceneConfig()
    n = len(samples) // FRAME
    if n < 4:
        raise ValueError("need at least 4 frames (~128 ms) of audio")
    raw = np.asarray(samples[: n * FRAME], dtype=np.float64).reshape(n, FRAME)
    frame_power = np.mean(raw**2, axis=1)
    rms_dbfs = float(_db(np.mean(frame_power)))
    spec = np.abs(np.fft.rfft(raw * np.hanning(FRAME), axis=1)) ** 2
    freqs = np.fft.rfftfreq(FRAME, 1 / sample_rate)
    active = _db(frame_power) >= config.active_dbfs
    if not active.any():
        return SceneFeatures(rms_dbfs, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)

    s = spec[active][:, 1:]  # drop DC
    f = freqs[1:]
    total = s.sum(axis=1) + _EPS
    centroid = float(np.mean((s * f).sum(axis=1) / total))
    flatness = _flatness(s)
    hf_flatness = _flatness(s[:, f >= 4000])
    energy = float(total.sum())
    speech_band = float(s[:, (f >= 300) & (f <= 3400)].sum() / energy)
    high_band = float(s[:, f >= 4000].sum() / energy)

    envelope = np.sqrt(frame_power)
    mean_env = float(envelope.mean())
    mod_depth = float(envelope.std() / mean_env) if mean_env > 0 else 0.0
    env_spec = np.abs(np.fft.rfft(envelope - mean_env)) ** 2
    env_freqs = np.fft.rfftfreq(n, FRAME / sample_rate)
    ac = float(env_spec[env_freqs > 0].sum())
    syllabic = float(env_spec[(env_freqs >= 2) & (env_freqs <= 8)].sum() / ac) if ac > _EPS else 0.0

    frame_db = _db(frame_power)
    jumps = (np.diff(frame_db) >= config.onset_db) & (frame_db[1:] >= config.quiet_dbfs)
    onset_rate = float(jumps.sum() / (n * FRAME / sample_rate))

    mean_spec = s.mean(axis=0)
    floor = mean_spec.max() / 100  # within 20 dB of the strongest bin
    is_peak = (
        (mean_spec[1:-1] > mean_spec[:-2])
        & (mean_spec[1:-1] >= mean_spec[2:])
        & (mean_spec[1:-1] >= floor)
    )
    peaks = int(is_peak.sum())
    concentration = float(np.sort(mean_spec)[-16:].sum() / (mean_spec.sum() + _EPS))

    return SceneFeatures(
        rms_dbfs=round(rms_dbfs, 2),
        centroid_hz=round(centroid, 1),
        flatness=round(flatness, 4),
        hf_flatness=round(hf_flatness, 4),
        concentration=round(concentration, 4),
        speech_band=round(speech_band, 4),
        high_band=round(high_band, 4),
        mod_depth=round(mod_depth, 4),
        syllabic=round(syllabic, 4),
        onset_rate=round(onset_rate, 2),
        peaks=peaks,
    )


def classify(feats: SceneFeatures, config: SceneConfig | None = None) -> tuple[SceneLabel, ...]:
    """Explainable thresholds → labels with score ≥ `min_score`, strongest first."""
    config = config or SceneConfig()
    cap = config.max_score
    if feats.rms_dbfs < config.quiet_dbfs:
        score = min(cap, 0.6 + (config.quiet_dbfs - feats.rms_dbfs) / 40)
        return (SceneLabel("quiet", round(score, 2)),)

    concentrated = _ramp(feats.concentration, 0.5, 0.8)
    bursty = _ramp(feats.onset_rate, 0.5, 3.0)
    loud = _ramp(feats.rms_dbfs, -40.0, -25.0)
    scores = {
        "conversation": min(
            _ramp(feats.speech_band, 0.5, 0.8),
            _ramp(feats.syllabic, 0.25, 0.5),
            _ramp(feats.mod_depth, 0.2, 0.5),
            1 - _ramp(feats.flatness, 0.3, 0.5),
        ),
        "music": min(
            concentrated,
            _ramp(feats.peaks, 2, 4),
            1 - _ramp(feats.peaks, 16, 30),
            1 - _ramp(feats.mod_depth, 0.25, 0.5),
            _ramp(feats.centroid_hz, 100, 200),
        ),
        "busy venue": loud * (1 - concentrated) * max(bursty, _ramp(feats.flatness, 0.1, 0.25)),
        # Hiss alone is not enough; it has to come in bursts.
        "coffee service fixture": min(
            _ramp(feats.high_band, 0.25, 0.5), _ramp(feats.hf_flatness, 0.2, 0.4)
        )
        * (0.5 + 0.5 * bursty),
    }
    labels = [
        SceneLabel(label, round(cap * score, 2))
        for label, score in scores.items()
        if cap * score >= config.min_score
    ]
    labels.sort(key=lambda item: (-item.score, item.label))
    return tuple(labels[: config.max_labels])


def classify_window(
    samples: np.ndarray, sample_rate: int = SAMPLE_RATE, config: SceneConfig | None = None
) -> tuple[SceneLabel, ...]:
    return classify(features(samples, sample_rate, config), config)
