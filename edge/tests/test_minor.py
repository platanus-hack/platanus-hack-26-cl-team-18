"""The pose heuristic behind `minor_likely` (invariant 2: it only ever blocks)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agent.contract import AGENT_INBOUND, AudienceSnapshot, MinorLikely, Tracklet
from agent.vision.minor import NO_READING, MinorConfig, MinorReading, MinorVote, child_evidence
from agent.vision.pipeline import VisionPipeline
from agent.vision.snapshot import SnapshotBuilder, SnapshotConfig
from agent.vision.tracking import PersonTracker, TrackerConfig
from agent.vision.types import Box, FrameDetections, Keypoint, PersonDetection

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
W, H = 1280, 720
CONFIG = MinorConfig()


def body(
    x: float,
    *,
    top: float = 60,
    bottom: float = 700,
    width: float = 200,
    shoulders: float = 180,
    ears: float = 70,
    ear_conf: float = 0.9,
    keypoints: bool = True,
) -> PersonDetection:
    """A standing person. Adults: ears/shoulders ≈ 0.39. Children: ≈ 0.6+."""
    cx = x + width / 2
    kps = [Keypoint(0, 0, 0.0)] * 17
    kps[0] = Keypoint(cx, top + 50, 0.9)
    kps[1] = Keypoint(cx + 15, top + 35, 0.9)
    kps[2] = Keypoint(cx - 15, top + 35, 0.9)
    kps[3] = Keypoint(cx + ears / 2, top + 40, ear_conf)
    kps[4] = Keypoint(cx - ears / 2, top + 40, ear_conf)
    kps[5] = Keypoint(cx + shoulders / 2, top + 110, 0.9)
    kps[6] = Keypoint(cx - shoulders / 2, top + 110, 0.9)
    return PersonDetection(
        box=Box(x, top, x + width, bottom), score=0.9, keypoints=tuple(kps) if keypoints else ()
    )


ADULT = body(200)
ADULT_2 = body(800, top=80)  # a slightly shorter adult, same floor line
CHILD = body(500, top=300, width=120, shoulders=110)  # 400 px vs 640 px, big head


def child_cases() -> list[tuple[str, list[PersonDetection], PersonDetection | None, float | None]]:
    return [
        ("adult alone: no peer, adult head", [ADULT], ADULT, 0.0),
        ("two adults of similar height", [ADULT, ADULT_2], ADULT_2, 0.0),
        ("short adult (0.82 x tallest)", [ADULT, body(800, top=175)], None, 0.0),
        ("child next to an adult: both cues", [ADULT, CHILD], CHILD, 1.0),
        (
            "child-height, no keypoints: stature only, discounted",
            [ADULT, body(500, top=300, width=120, keypoints=False)],
            None,
            0.75,
        ),
        (
            "child farther back (other floor line), adult head: no evidence",
            [ADULT, body(500, top=200, bottom=500, width=120)],
            None,
            0.0,
        ),
        (
            "box cut by the frame top, no usable ears",
            [ADULT, body(500, top=2, ear_conf=0.1)],
            None,
            None,
        ),
    ]


@pytest.mark.parametrize(
    ("name", "people", "subject", "expected"), child_cases(), ids=[c[0] for c in child_cases()]
)
def test_child_evidence_table(
    name: str,
    people: list[PersonDetection],
    subject: PersonDetection | None,
    expected: float | None,
) -> None:
    target = subject or people[-1]
    evidence = child_evidence(target, people, H, CONFIG)
    if expected is None:
        assert evidence is None
    else:
        assert evidence == pytest.approx(expected, abs=0.01)


def test_adult_only_frames_never_flag_and_report_a_real_confidence() -> None:
    vote = MinorVote()
    readings = [vote.update([ADULT, ADULT_2], H) for _ in range(30)]
    assert not any(r.value for r in readings)
    assert 0 < readings[-1].confidence < 1


def test_child_next_to_adults_flags_only_after_the_vote_window() -> None:
    vote = MinorVote()
    readings = [vote.update([ADULT, CHILD, ADULT_2], H) for _ in range(12)]
    flips = [i for i, r in enumerate(readings) if r.value]
    assert flips[0] == CONFIG.min_hits - 1  # true on the 7th frame, not before
    assert all(r.value for r in readings[flips[0] :])
    assert 0 < readings[-1].confidence < 1
    assert readings[-1].confidence <= CONFIG.max_confidence


def test_a_single_frame_blip_never_flips() -> None:
    vote = MinorVote()
    sequence = [[ADULT, ADULT_2]] * 5 + [[ADULT, CHILD]] + [[ADULT, ADULT_2]] * 10
    assert not any(vote.update(people, H).value for people in sequence)


def test_a_child_who_leaves_clears_the_flag_after_the_window() -> None:
    vote = MinorVote()
    for _ in range(12):
        vote.update([ADULT, CHILD], H)
    after = [vote.update([ADULT], H).value for _ in range(12)]
    assert after[0] is True and after[-1] is False


def test_empty_scene_says_nothing() -> None:
    reading = MinorVote().update([], H)
    assert (reading.value, reading.confidence) == (False, 0.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_hits": 0},
        {"min_hits": 13, "window": 12},
        {"max_confidence": 1.0},
        {"person_threshold": 0.0},
        {"height_ratio_child": 0.9},
        {"head_ratio_child": 0.4},
    ],
)
def test_config_rejects_nonsense(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        MinorConfig(**kwargs)  # type: ignore[arg-type]


def test_threshold_is_configurable() -> None:
    lone_height_cue = [ADULT, body(500, top=300, width=120, keypoints=False)]  # evidence 0.75
    strict = MinorVote(MinorConfig(person_threshold=0.8, window=3, min_hits=2))
    lenient = MinorVote(MinorConfig(person_threshold=0.5, window=3, min_hits=2))
    for _ in range(3):
        strict_reading = strict.update(lone_height_cue, H)
        lenient_reading = lenient.update(lone_height_cue, H)
    assert strict_reading.value is False and lenient_reading.value is True


# ------------------------------------------------------------- through the snapshot


class Link:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def send(self, message: object, *, binary: bytes | None = None) -> None:
        self.sent.append(message)


def _frame(at: datetime, *people: PersonDetection) -> FrameDetections:
    return FrameDetections(measured_at=at, width=W, height=H, persons=people)


def _pipeline(link: Link) -> VisionPipeline:
    return VisionPipeline(
        screen_id="demo-01",
        source=None,  # type: ignore[arg-type]  # process() only
        detector=None,  # type: ignore[arg-type]
        link=link,
        tracker=PersonTracker(TrackerConfig(min_hits=1)),
        snapshots=SnapshotBuilder("demo-01", SnapshotConfig()),
    )


def test_confirmed_minor_is_sent_without_waiting_for_the_general_debounce() -> None:
    link = Link()
    pipeline = _pipeline(link)
    for i in range(10):  # 1 s of adults
        pipeline.process(object(), _frame(T0 + timedelta(milliseconds=100 * i), ADULT, ADULT_2))
    child_from = T0 + timedelta(seconds=1)
    for i in range(20):  # a child joins for 2 s
        at = child_from + timedelta(milliseconds=100 * i)
        pipeline.process(object(), _frame(at, ADULT, CHILD, ADULT_2))
    snapshots = [m for m in link.sent if isinstance(m, AudienceSnapshot)]
    flagged = [s for s in snapshots if s.minor_likely.value]
    assert flagged
    # The seventh hit at 10 Hz lands 600 ms after the first hit.
    assert flagged[0].measured_at - child_from == timedelta(milliseconds=600)
    assert 0 < flagged[-1].minor_likely.confidence < 1
    # The count (3) is published first; the flag flips later on its own (it is in Facts.key).
    first_three = next(s for s in snapshots if s.people == 3)
    assert first_three.minor_likely.value is False

    wire = json.loads(flagged[-1].model_dump_json())
    assert set(wire) == set(AudienceSnapshot.model_fields)
    assert set(wire["minor_likely"]) == set(MinorLikely.model_fields)
    assert all(set(t) == set(Tracklet.model_fields) for t in wire["tracklets"])
    assert isinstance(AGENT_INBOUND.validate_json(flagged[-1].model_dump_json()), AudienceSnapshot)


def test_minor_block_bypasses_rate_limit_and_clears_only_after_stability() -> None:
    builder = SnapshotBuilder("demo-01")

    def observe(ms: int, minor: MinorReading) -> AudienceSnapshot | None:
        return builder.build(
            measured_at=T0 + timedelta(milliseconds=ms),
            tracks=[],
            objects=(),
            held=[],
            minor=minor,
        )

    assert observe(0, NO_READING) is not None
    blocked = observe(25, MinorReading(value=True, confidence=0.9))
    assert blocked is not None and blocked.minor_likely.value
    assert observe(100, NO_READING) is None
    assert observe(599, NO_READING) is None
    cleared = observe(600, NO_READING)
    assert cleared is not None and not cleared.minor_likely.value
