"""A coarse, explainable "a child is probably here" flag from pose geometry.

Invariant 2 (CLAUDE.md): `minor_likely` may only **block** age-restricted ads
(alcohol, gambling, unhealthy food). It never targets anyone. A false positive
is therefore the safe failure; a false negative leaves the warm VLM path as the
second line. This module supplements that path (ADR 0008 D13).

No new model, no face analysis, no age estimate. Two body-size cues per tracked
person, each a linear ramp from "no evidence" to "full evidence":

1. **Relative stature.** People whose feet stand on the same floor line (box
   bottoms within `ground_tolerance` of the frame height) are at a comparable
   distance, so their pixel heights compare like their statures. A person
   shorter than `height_ratio_adult` × the tallest such peer starts to count;
   at `height_ratio_child` the cue is full. Only boxes not cut by the frame
   edge are compared.
2. **Head-to-shoulder width.** Children have big heads for their shoulders:
   ear-to-ear over shoulder-to-shoulder is about 0.36–0.44 for adults and
   0.5–0.65 for young children. The cue ramps from `head_ratio_adult` to
   `head_ratio_child`. Needs both ears and both shoulders.

A person is child-sized when the weighted cues (one cue alone is discounted)
reach `person_threshold`. The frame result is only "some tracked person is
child-sized" plus the strongest evidence; the moment flag is a vote: it is true
when at least `min_hits` of the last `window` frames had a hit, so a single
frame blip never flips it. The confidence is capped at `max_confidence` (< 1):
this is a heuristic, not a measurement.

Privacy: per-person evidence lives only inside `update()`. Nothing per person
is kept, logged or sent; only the moment's boolean and confidence leave.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from agent.vision.types import PersonDetection

# COCO-17 keypoint indexes (agent.vision.types.Kp covers the head and shoulders only).
_LEFT_EAR, _RIGHT_EAR, _LEFT_SHOULDER, _RIGHT_SHOULDER = 3, 4, 5, 6


@dataclass(frozen=True, slots=True)
class MinorConfig:
    min_kp_confidence: float = 0.5
    # Cue 1, relative stature. A box closer than this to the frame edge (fraction
    # of the frame height) is cut off, and its height says nothing about stature.
    edge_margin: float = 0.02
    # Box bottoms within this fraction of the frame height stand at a comparable depth.
    ground_tolerance: float = 0.06
    height_ratio_adult: float = 0.80  # ≥ 0.80 × the tallest peer: no evidence
    height_ratio_child: float = 0.65  # ≤ 0.65 × the tallest peer: full evidence
    # Cue 2, ear-to-ear / shoulder-to-shoulder width.
    head_ratio_adult: float = 0.48  # adults ≈ 0.36–0.44, plus keypoint noise
    head_ratio_child: float = 0.58
    min_shoulder_px: float = 40.0  # narrower shoulders are too noisy to measure
    # Combination: weight of the stature cue when both are available; a single cue
    # is multiplied by the discount, so one cue alone can still reach the threshold.
    height_weight: float = 0.6
    single_cue_discount: float = 0.75
    person_threshold: float = 0.5
    # Temporal vote, in frames.
    window: int = 12
    min_hits: int = 7
    max_confidence: float = 0.9

    def __post_init__(self) -> None:
        if not 0 < self.min_hits <= self.window:
            raise ValueError("need 0 < min_hits <= window")
        if not 0 < self.max_confidence < 1:
            raise ValueError("max_confidence must be in (0, 1): this is a heuristic")
        if not 0 < self.person_threshold <= 1:
            raise ValueError("person_threshold must be in (0, 1]")
        if self.height_ratio_child >= self.height_ratio_adult:
            raise ValueError("height_ratio_child must be below height_ratio_adult")
        if self.head_ratio_child <= self.head_ratio_adult:
            raise ValueError("head_ratio_child must be above head_ratio_adult")


@dataclass(frozen=True, slots=True)
class MinorReading:
    """The moment-level flag: the only thing that leaves this module."""

    value: bool
    confidence: float


NO_READING = MinorReading(value=False, confidence=0.0)


def _ramp(value: float, start: float, full: float) -> float:
    """0 at `start`, 1 at `full`, linear in between (works for either direction)."""
    t = (value - start) / (full - start)
    return min(1.0, max(0.0, t))


def _uncut(person: PersonDetection, frame_height: int, config: MinorConfig) -> bool:
    margin = frame_height * config.edge_margin
    return person.box.y1 > margin and person.box.y2 < frame_height - margin


def stature_cue(
    person: PersonDetection,
    peers: Sequence[PersonDetection],
    frame_height: int,
    config: MinorConfig,
) -> float | None:
    if frame_height <= 0 or not _uncut(person, frame_height, config):
        return None
    tolerance = frame_height * config.ground_tolerance
    heights = [
        peer.box.height
        for peer in peers
        if peer is not person
        and _uncut(peer, frame_height, config)
        and abs(peer.box.y2 - person.box.y2) <= tolerance
    ]
    if not heights or max(heights) <= 0:
        return None
    ratio = person.box.height / max(heights)
    return _ramp(ratio, config.height_ratio_adult, config.height_ratio_child)


def head_cue(person: PersonDetection, config: MinorConfig) -> float | None:
    points = person.keypoints
    if len(points) <= _RIGHT_SHOULDER:
        return None
    ears = points[_LEFT_EAR], points[_RIGHT_EAR]
    shoulders = points[_LEFT_SHOULDER], points[_RIGHT_SHOULDER]
    if any(p.confidence < config.min_kp_confidence for p in (*ears, *shoulders)):
        return None
    shoulder_width = abs(shoulders[0].x - shoulders[1].x)
    if shoulder_width < config.min_shoulder_px:
        return None
    ratio = abs(ears[0].x - ears[1].x) / shoulder_width
    return _ramp(ratio, config.head_ratio_adult, config.head_ratio_child)


def child_evidence(
    person: PersonDetection,
    peers: Sequence[PersonDetection],
    frame_height: int,
    config: MinorConfig,
) -> float | None:
    """Evidence in [0, 1] that this person is child-sized, or None with no usable cue."""
    stature = stature_cue(person, peers, frame_height, config)
    head = head_cue(person, config)
    if stature is not None and head is not None:
        return config.height_weight * stature + (1 - config.height_weight) * head
    single = stature if stature is not None else head
    return None if single is None else single * config.single_cue_discount


@dataclass(frozen=True, slots=True)
class _FrameVote:
    evaluated: bool  # at least one tracked person had a usable cue
    hit: bool
    strength: float  # strongest evidence among child-sized people (0 when no hit)


class MinorVote:
    """Per-frame child-size cues → a stable moment-level `MinorReading`."""

    def __init__(self, config: MinorConfig | None = None) -> None:
        self._config = config or MinorConfig()
        self._frames: deque[_FrameVote] = deque(maxlen=self._config.window)

    def update(self, persons: Sequence[PersonDetection], frame_height: int) -> MinorReading:
        """Feed the tracked (near) people of one frame; returns the voted reading."""
        config = self._config
        evidence = [
            e
            for person in persons
            if (e := child_evidence(person, persons, frame_height, config)) is not None
        ]
        hits = [e for e in evidence if e >= config.person_threshold]
        self._frames.append(
            _FrameVote(evaluated=bool(evidence), hit=bool(hits), strength=max(hits, default=0.0))
        )
        return self.reading()

    def reading(self) -> MinorReading:
        config = self._config
        hit_frames = [f for f in self._frames if f.hit]
        if len(hit_frames) >= config.min_hits:
            mean_strength = sum(f.strength for f in hit_frames) / len(hit_frames)
            share = len(hit_frames) / config.window
            return MinorReading(True, round(config.max_confidence * share * mean_strength, 2))
        clean = sum(1 for f in self._frames if f.evaluated and not f.hit)
        return MinorReading(False, round(config.max_confidence * clean / config.window, 2))

    def reset(self) -> None:
        self._frames.clear()
