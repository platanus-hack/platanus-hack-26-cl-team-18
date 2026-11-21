"""Score the perception agent on real recorded video against hand labels.

No mocked frames: a consenting person records a clip, someone labels it by
time window (who is there, who looks, what they hold, whether a minor is
present), the real agent runs the clip in replay mode and writes the
snapshots it would have sent. This module compares the two.

Labels (evals/perception/labels/<clip>.json)::

    {"clip": "gym-duo.mp4", "sha256": "...", "consent": "who and when",
     "windows": [{"start_s": 0, "end_s": 4, "people": 2, "looking": 2,
                  "objects": ["cup"], "minor": false}, ...]}

Recording (JSONL, one per line): ``{"t_s": 1.23, "snapshot": {AudienceSnapshot}}``
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.contract.messages import AudienceSnapshot
from evals.metrics import percentile

SETTLE_S = 0.5  # agent debounce: a window is scored after the scene settles
DETECT_TIMEOUT_S = 3.0


@dataclass(frozen=True, slots=True, kw_only=True)
class LabelWindow:
    start_s: float
    end_s: float
    people: int
    looking: int
    objects: frozenset[str]
    minor: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Sample:
    t_s: float
    snapshot: AudienceSnapshot

    @property
    def objects(self) -> frozenset[str]:
        return frozenset(o.label for o in self.snapshot.objects)


def load_labels(path: Path) -> tuple[LabelWindow, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    windows = tuple(
        LabelWindow(
            start_s=float(w["start_s"]),
            end_s=float(w["end_s"]),
            people=int(w["people"]),
            looking=int(w.get("looking", w["people"])),
            objects=frozenset(w.get("objects", [])),
            minor=bool(w.get("minor", False)),
        )
        for w in raw["windows"]
    )
    for left, right in itertools.pairwise(windows):
        if right.start_s < left.end_s:
            raise ValueError(f"{path.name}: label windows overlap at {right.start_s}s")
    return windows


def load_recording(path: Path) -> tuple[Sample, ...]:
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            body = json.loads(line)
            samples.append(
                Sample(
                    t_s=float(body["t_s"]),
                    snapshot=AudienceSnapshot.model_validate(body["snapshot"]),
                )
            )
    return tuple(sorted(samples, key=lambda s: s.t_s))


def score_clip(
    windows: Sequence[LabelWindow], samples: Sequence[Sample]
) -> dict[str, float | int | None]:
    settled: list[tuple[LabelWindow, Sample]] = [
        (w, s) for w in windows for s in samples if w.start_s + SETTLE_S <= s.t_s < w.end_s
    ]
    people_errors = [abs(s.snapshot.people - w.people) for w, s in settled]
    looking_errors = [abs(s.snapshot.looking - w.looking) for w, s in settled]

    detect_delays: list[float] = []
    missed_changes = 0
    for previous, window in itertools.pairwise(windows):
        if (previous.people, previous.objects) == (window.people, window.objects):
            continue
        hit = next(
            (
                s.t_s - window.start_s
                for s in samples
                if window.start_s <= s.t_s < min(window.end_s, window.start_s + DETECT_TIMEOUT_S)
                and s.snapshot.people == window.people
                and window.objects <= s.objects
            ),
            None,
        )
        if hit is None:
            missed_changes += 1
        else:
            detect_delays.append(hit * 1000)

    labeled = [(w, label) for w in windows for label in w.objects]
    found = sum(
        1 for w, label in labeled if any(label in s.objects for lw, s in settled if lw is w)
    )
    false_objects = sum(len(s.objects - w.objects) for w, s in settled)
    minor_windows = [w for w in windows if w.minor]
    minor_missed = sum(
        1
        for w in minor_windows
        if not any(s.snapshot.minor_likely.value for lw, s in settled if lw is w)
    )
    adult_samples = [(w, s) for w, s in settled if not w.minor and w.people > 0]
    minor_false_alarms = sum(1 for _, s in adult_samples if s.snapshot.minor_likely.value)
    span = samples[-1].t_s - samples[0].t_s if len(samples) > 1 else 0.0
    return {
        "samples": len(samples),
        "snapshot_rate_hz": (len(samples) - 1) / span if span else None,
        "people_exact_rate": _rate(sum(e == 0 for e in people_errors), len(people_errors)),
        "people_mae": _mean(people_errors),
        "looking_mae": _mean(looking_errors),
        "change_detect_p50_ms": percentile(detect_delays, 50),
        "change_detect_p95_ms": percentile(detect_delays, 95),
        "changes_missed": missed_changes,
        "object_recall": _rate(found, len(labeled)),
        "false_objects_per_sample": _rate(false_objects, len(settled)),
        "minor_windows": len(minor_windows),
        "minor_windows_missed": minor_missed,  # safety-critical: must be 0
        "minor_false_alarm_rate": _rate(minor_false_alarms, len(adult_samples)),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: Sequence[int]) -> float | None:
    return sum(values) / len(values) if values else None
