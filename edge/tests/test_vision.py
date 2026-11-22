from __future__ import annotations

import asyncio
import itertools
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from agent.contract import AGENT_INBOUND, AudienceKeyframe, AudienceSnapshot, DistanceBand
from agent.vision.adapters import _objects, _persons
from agent.vision.capture import RedactedKeyframe
from agent.vision.detector import (
    CompositeDetector,
    DetectorUnavailable,
    FailoverConfig,
    FailoverDetector,
)
from agent.vision.geometry import GeometryConfig, distance_band, head_box, is_looking
from agent.vision.held import HELD_LABEL_PREFIX, HeldProductTracker, MarkerCatalog
from agent.vision.minor import MinorVote
from agent.vision.pipeline import JsonlRecorder, VisionPipeline, keyframe_regions
from agent.vision.snapshot import SnapshotBuilder, SnapshotConfig
from agent.vision.tracking import PersonTracker, TrackerConfig
from agent.vision.types import (
    Box,
    FrameDetections,
    Keypoint,
    MarkerDetection,
    ObjectDetection,
    PersonDetection,
)

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
W, H = 1280, 720
NO_DEBOUNCE = SnapshotConfig(debounce=timedelta(0))
# Unit tests about one frame at a time skip track confirmation; it has its own tests.
FAST = TrackerConfig(min_hits=1)


def person(
    x: float = 400,
    *,
    width: float = 200,
    top: float = 60,
    bottom: float = 700,
    facing: bool = True,
    shoulders: float = 180,
    score: float = 0.9,
    hand: tuple[float, float] | None = None,
) -> PersonDetection:
    """A synthetic pose detection. `facing=False` turns the nose toward one eye.

    `hand` places a visible right wrist; without it neither wrist is visible."""
    cx = x + width / 2
    nose_x = cx if facing else cx + 30
    kps = [Keypoint(0, 0, 0.0)] * 17
    kps[0] = Keypoint(nose_x, top + 60, 0.9)
    kps[1] = Keypoint(cx + 20, top + 40, 0.9)
    kps[2] = Keypoint(cx - 20, top + 40, 0.9)
    kps[3] = Keypoint(cx + 35, top + 45, 0.6)
    kps[4] = Keypoint(cx - 35, top + 45, 0.6)
    kps[5] = Keypoint(cx + shoulders / 2, top + 140, 0.9)
    kps[6] = Keypoint(cx - shoulders / 2, top + 140, 0.9)
    if hand is not None:
        kps[10] = Keypoint(hand[0], hand[1], 0.8)
    return PersonDetection(box=Box(x, top, x + width, bottom), score=score, keypoints=tuple(kps))


def frame(
    at: datetime,
    *people: PersonDetection,
    objects: tuple[ObjectDetection, ...] = (),
    markers: tuple[MarkerDetection, ...] = (),
) -> FrameDetections:
    return FrameDetections(
        measured_at=at, width=W, height=H, persons=people, objects=objects, markers=markers
    )


def ms(value: float) -> datetime:
    return T0 + timedelta(milliseconds=value)


# ---------------------------------------------------------------- geometry


def test_looking_requires_a_centered_nose() -> None:
    assert is_looking(person(facing=True))
    assert not is_looking(person(facing=False))


def test_not_looking_without_eyes_or_with_collapsed_eyes() -> None:
    assert not is_looking(PersonDetection(box=Box(0, 0, 100, 300), score=0.9))
    p = person()
    kps = list(p.keypoints)
    kps[1] = Keypoint(kps[2].x, kps[2].y, 0.9)  # both eyes on the same pixel
    assert not is_looking(PersonDetection(box=p.box, score=0.9, keypoints=tuple(kps)))


def test_low_confidence_eyes_mean_not_looking() -> None:
    p = person()
    kps = list(p.keypoints)
    kps[1] = Keypoint(kps[1].x, kps[1].y, 0.1)
    assert not is_looking(PersonDetection(box=p.box, score=0.9, keypoints=tuple(kps)))


def test_distance_band_from_shoulder_width() -> None:
    assert distance_band(person(shoulders=320), W, H) is DistanceBand.NEAR
    assert distance_band(person(shoulders=180), W, H) is DistanceBand.MID
    assert distance_band(person(shoulders=60), W, H) is DistanceBand.FAR


def _shoulders_px_at(metres: float, hfov_deg: float = 90.0) -> float:
    """0.40 m shoulders seen by a pinhole camera `metres` away, in px of a 1280-wide frame."""
    return W * 0.40 / (2 * metres * math.tan(math.radians(hfov_deg) / 2))


@pytest.mark.parametrize(
    ("metres", "band"),
    [
        (1.0, DistanceBand.NEAR),
        # A judge standing an ordinary 2-2.5 m from the TV reads the ad: not "far" (that
        # bills $0). On the stack's 90° camera the old 0.10 cutoff made this far at ~2 m,
        # though its own comment said ≳ 3 m (measured on stock clips: 48% far at 2.0 m).
        (2.0, DistanceBand.MID),
        (2.4, DistanceBand.MID),
        (3.5, DistanceBand.FAR),
    ],
)
def test_distance_bands_follow_metres_on_the_stack_camera(
    metres: float, band: DistanceBand
) -> None:
    assert distance_band(person(shoulders=_shoulders_px_at(metres)), W, H) is band


def test_a_wider_camera_moves_the_cutoffs_with_its_field_of_view() -> None:
    wide = GeometryConfig(camera_hfov_deg=120.0)
    # The same 2.4 m judge looks smaller on a 120° lens: still mid once the lens is declared.
    shoulders = _shoulders_px_at(2.4, hfov_deg=120.0)
    assert distance_band(person(shoulders=shoulders), W, H, wide) is DistanceBand.MID
    assert distance_band(person(shoulders=_shoulders_px_at(3.5, 120.0)), W, H, wide) is (
        DistanceBand.FAR
    )


def test_distance_band_falls_back_to_box_height() -> None:
    tall = PersonDetection(box=Box(0, 0, 100, 700), score=0.9)
    short = PersonDetection(box=Box(0, 0, 100, 200), score=0.9)
    mid = PersonDetection(box=Box(0, 0, 100, 450), score=0.9)
    assert distance_band(tall, W, H) is DistanceBand.NEAR
    assert distance_band(short, W, H) is DistanceBand.FAR
    assert distance_band(mid, W, H) is DistanceBand.MID


def test_head_box_covers_the_face_and_falls_back_without_keypoints() -> None:
    p = person()
    head = head_box(p)
    for index in (0, 1, 2, 3, 4):
        assert head.contains((p.keypoints[index].x, p.keypoints[index].y))
    bare = head_box(PersonDetection(box=Box(0, 0, 100, 400), score=0.9))
    assert (bare.x1, bare.y1, bare.x2) == (0, 0, 100)
    assert bare.y2 == pytest.approx(112)


def test_head_box_stays_inside_the_person_box() -> None:
    p = person(x=0, top=0)
    head = head_box(p)
    assert head.x1 >= p.box.x1 and head.y1 >= p.box.y1


@pytest.mark.parametrize("corners", [(10, 0, 0, 10), (0, 10, 10, 0)])
def test_box_rejects_inverted_corners(corners: tuple[float, float, float, float]) -> None:
    with pytest.raises(ValueError):
        Box(*corners)


def test_box_iou_and_expand() -> None:
    a, b = Box(0, 0, 10, 10), Box(5, 0, 15, 10)
    assert a.iou(b) == pytest.approx(50 / 150)
    assert a.iou(Box(20, 20, 30, 30)) == 0.0
    assert Box(0, 0, 0, 0).iou(Box(0, 0, 0, 0)) == 0.0
    assert a.expanded(0.5) == Box(-5, -5, 15, 15)


# ---------------------------------------------------------------- tracking


def test_tracks_keep_anonymous_ids_and_accumulate_dwell() -> None:
    tracker = PersonTracker(TrackerConfig(min_hits=1, max_gap=timedelta(seconds=3)))
    first = tracker.update(frame(T0, person(400)))
    later = tracker.update(frame(ms(2000), person(410)))
    assert [t.id for t in first] == ["t1"]
    assert [t.id for t in later] == ["t1"]
    assert later[0].dwell_s == 2.0
    assert later[0].looking


def test_track_expires_after_the_gap_and_ids_are_never_reused() -> None:
    tracker = PersonTracker(FAST)
    tracker.update(frame(T0, person(400)))
    gone = tracker.update(frame(ms(1000)))
    back = tracker.update(frame(ms(1100), person(400)))
    assert gone == []
    assert [t.id for t in back] == ["t2"]


def test_a_missed_frame_inside_the_gap_keeps_the_track() -> None:
    tracker = PersonTracker(FAST)
    tracker.update(frame(T0, person(400)))
    held = tracker.update(frame(ms(300)))
    again = tracker.update(frame(ms(500), person(405)))
    assert [t.id for t in held] == ["t1"]
    assert [t.id for t in again] == ["t1"]


def test_two_people_get_two_tracks_and_crossing_keeps_identity() -> None:
    tracker = PersonTracker(FAST)
    assert [t.id for t in tracker.update(frame(T0, person(100), person(800)))] == ["t1", "t2"]
    moved = tracker.update(frame(ms(100), person(820), person(120)))  # detection order swapped
    assert {t.id: t.box.x1 for t in moved} == {"t1": 120, "t2": 820}


def test_low_score_people_are_ignored() -> None:
    assert PersonTracker(FAST).update(frame(T0, person(score=0.2))) == []


def test_far_passers_by_are_not_the_audience() -> None:
    far = person(400, top=300, bottom=400)  # 100 px of a 720 px frame: ~14 %
    near = person(800)
    assert [t.box.x1 for t in PersonTracker(FAST).update(frame(T0, far, near))] == [800]
    assert (
        PersonTracker(TrackerConfig(min_height_fraction=0.0, min_hits=1)).update(frame(T0, far))
        != []
    )


def test_looking_is_held_briefly_through_a_glance_away() -> None:
    tracker = PersonTracker(FAST)
    tracker.update(frame(T0, person(400, facing=True)))
    glance = tracker.update(frame(ms(300), person(400, facing=False)))
    away = tracker.update(frame(ms(900), person(400, facing=False)))
    assert glance[0].looking
    assert not away[0].looking


def test_reset_forgets_tracks_but_not_the_id_counter() -> None:
    tracker = PersonTracker(FAST)
    tracker.update(frame(T0, person(400)))
    tracker.reset()
    assert [t.id for t in tracker.update(frame(ms(10), person(400)))] == ["t2"]


# ---------------------------------------------------------------- held products

CATALOG = MarkerCatalog(brands={1: "cafe-patio", 2: "rayo"})


def marker_at(marker_id: int, x: float, y: float = 400) -> MarkerDetection:
    return MarkerDetection(marker_id=marker_id, box=Box(x - 20, y - 20, x + 20, y + 20))


def test_held_product_needs_several_hits_then_sticks() -> None:
    held = HeldProductTracker(CATALOG, window=5, min_hits=3)
    tracker = PersonTracker(FAST)
    results = []
    for i in range(4):
        tracks = tracker.update(frame(ms(100 * i), person(400)))
        results.append(held.update((marker_at(1, 500),), tracks))
    assert results[0] == [] and results[1] == []
    assert [h.brand_id for h in results[2]] == ["cafe-patio"]
    assert results[3][0].confidence == 1.0


def test_held_product_fades_once_put_down() -> None:
    held = HeldProductTracker(CATALOG, window=4, min_hits=2)
    tracks = PersonTracker(FAST).update(frame(T0, person(400)))
    for _ in range(4):
        held.update((marker_at(1, 500),), tracks)
    after = [held.update((), tracks) for _ in range(4)]
    assert [h.brand_id for h in after[0]] == ["cafe-patio"]
    assert after[-1] == []


def test_marker_far_from_every_person_is_not_held() -> None:
    held = HeldProductTracker(CATALOG, window=3, min_hits=1)
    tracks = PersonTracker(FAST).update(frame(T0, person(100)))
    assert held.update((marker_at(1, 1200),), tracks) == []


def test_unknown_marker_is_ignored() -> None:
    held = HeldProductTracker(CATALOG, window=3, min_hits=1)
    tracks = PersonTracker(FAST).update(frame(T0, person(400)))
    assert held.update((marker_at(9, 500),), tracks) == []


def test_held_tracker_rejects_impossible_windows() -> None:
    with pytest.raises(ValueError):
        HeldProductTracker(CATALOG, window=2, min_hits=3)


def test_catalog_loads_from_json(tmp_path: Path) -> None:
    path = tmp_path / "markers.json"
    path.write_text('{"0": "voltio", "1": "cafe-patio"}', encoding="utf-8")
    assert MarkerCatalog.from_file(path).brand_for(1) == "cafe-patio"


def test_shipped_marker_catalog_is_valid() -> None:
    catalog = MarkerCatalog.from_file(Path(__file__).parents[1] / "props" / "markers.json")
    assert len(set(catalog.brands.values())) == len(catalog.brands)
    assert all(0 <= marker_id < 50 for marker_id in catalog.brands)  # DICT_4X4_50


# ---------------------------------------------------------------- snapshots


def _builder(config: SnapshotConfig = NO_DEBOUNCE) -> tuple[SnapshotBuilder, PersonTracker]:
    return SnapshotBuilder("demo-01", config), PersonTracker(FAST)


def _feed(
    builder: SnapshotBuilder,
    tracker: PersonTracker,
    at: datetime,
    *people: PersonDetection,
    objects: tuple[ObjectDetection, ...] = (),
) -> AudienceSnapshot | None:
    return builder.build(
        measured_at=at, tracks=tracker.update(frame(at, *people)), objects=objects, held=[]
    )


def test_snapshot_is_a_valid_contract_message_with_held_products() -> None:
    builder, tracker = _builder()
    adults = frame(T0, person(400), person(800, facing=False))
    tracks = tracker.update(adults)
    held = HeldProductTracker(CATALOG, window=1, min_hits=1).update((marker_at(1, 500),), tracks)
    minor = MinorVote().update(adults.persons, adults.height)
    snapshot = builder.build(
        minor=minor,
        measured_at=T0,
        tracks=tracks,
        objects=(
            ObjectDetection("cup", 0.8, Box(480, 380, 520, 420)),
            ObjectDetection("cup", 0.6, Box(450, 380, 470, 420)),
            ObjectDetection("toaster", 0.9, Box(480, 380, 520, 420)),
        ),
        held=held,
    )
    assert snapshot is not None
    wire = AGENT_INBOUND.validate_json(snapshot.model_dump_json())
    assert isinstance(wire, AudienceSnapshot)
    assert (wire.people, wire.looking) == (2, 1)
    assert {o.label: o.score for o in wire.objects} == {
        "cup": 0.8,
        f"{HELD_LABEL_PREFIX}cafe-patio": 1.0,
    }
    # Two adult-proportioned people: the pose heuristic says "no minor", with a
    # real but small confidence (one evaluated frame of a 12-frame vote).
    assert wire.minor_likely.value is False
    assert 0 < wire.minor_likely.confidence < 1
    assert [t.id for t in wire.tracklets] == ["t1", "t2"]


def test_objects_away_from_every_person_are_dropped() -> None:
    builder, tracker = _builder()
    snapshot = _feed(
        builder,
        tracker,
        T0,
        person(100),
        objects=(
            ObjectDetection("bottle", 0.9, Box(1100, 600, 1150, 700)),  # on the counter
            ObjectDetection("cup", 0.2, Box(150, 300, 170, 330)),  # below the score floor
        ),
    )
    assert snapshot is not None and snapshot.objects == []


def test_empty_scene_still_sends_a_valid_snapshot() -> None:
    builder, tracker = _builder()
    snapshot = _feed(builder, tracker, T0)
    assert snapshot is not None and (snapshot.people, snapshot.looking) == (0, 0)


def test_rate_limit_change_and_heartbeat() -> None:
    builder, tracker = _builder()
    first = _feed(builder, tracker, ms(0), person(400))
    assert first is not None and first.seq == 0
    assert _feed(builder, tracker, ms(50), person(400), person(800)) is None  # > 10 Hz
    changed = _feed(builder, tracker, ms(120), person(400), person(800))
    assert changed is not None and changed.people == 2 and changed.seq == 1
    assert _feed(builder, tracker, ms(400), person(400), person(800)) is None  # unchanged
    heartbeat = _feed(builder, tracker, ms(1200), person(400), person(800))
    assert heartbeat is not None and heartbeat.seq == 2


def test_one_frame_false_positive_is_never_published() -> None:
    builder = SnapshotBuilder("demo-01", SnapshotConfig(debounce=timedelta(milliseconds=500)))
    tracker = PersonTracker()  # production config: tracks need 2 hits
    sent = []
    for i in range(20):  # 50 ms frames; a ghost second person appears in frame 3 only
        people = [person(400)] + ([person(800)] if i == 3 else [])
        sent.append(_feed(builder, tracker, ms(50 * i), *people))
    assert {s.people for s in sent if s is not None} <= {0, 1}


def test_published_counts_never_change_faster_than_the_debounce() -> None:
    builder, tracker = _builder(SnapshotConfig(debounce=timedelta(milliseconds=500)))
    sent = []
    for i in range(60):  # noisy detections: a second person in irregular bursts
        people = [person(400)] + ([person(800)] if i % 7 in (0, 1, 3) else [])
        sent.append(_feed(builder, tracker, ms(100 * i), *people))
    published = [(s.measured_at, s.people) for s in sent if s is not None]
    change_times = [b[0] for a, b in itertools.pairwise(published) if a[1] != b[1]]
    gaps = [b - a for a, b in itertools.pairwise(change_times)]
    assert all(gap >= timedelta(milliseconds=500) for gap in gaps)


def test_counts_do_not_freeze_when_a_crowd_never_holds_still() -> None:
    """Regression from a real café clip: churn kept the count stuck at 3 for 9 s."""
    builder, tracker = _builder(SnapshotConfig(debounce=timedelta(milliseconds=500)))
    xs = [100, 300, 500, 700, 900, 1100]
    _feed(builder, tracker, ms(0), *(person(x, width=150) for x in xs[:3]))
    published = []
    for i in range(1, 50):  # 5 or 6 people, alternating every 200 ms: never stable 500 ms
        count = 5 if (i // 2) % 2 else 6
        snap = _feed(
            builder,
            PersonTracker(FAST),  # fresh tracker: raw count == detections
            ms(100 * i),
            *(person(x, width=150) for x in xs[:count]),
        )
        if snap is not None:
            published.append(snap.people)
    assert published and published[-1] in (5, 6)  # never frozen at the stale 3


def test_vote_publishes_the_most_frequent_facts() -> None:
    builder, _ = _builder(
        SnapshotConfig(
            debounce=timedelta(milliseconds=500),
            max_stale=timedelta(milliseconds=300),
            vote_window=timedelta(seconds=1),
        )
    )

    def feed(t: int, n: int) -> AudienceSnapshot | None:
        return _feed(builder, PersonTracker(FAST), ms(t), *(person(150 * i) for i in range(n)))

    feed(0, 1)
    last = None
    for i, n in enumerate([4, 4, 3, 4, 4, 3, 4]):  # 4 dominates, never stable 500 ms
        last = feed(100 * (i + 1), n) or last
    assert builder.published is not None and builder.published.people == 4


def test_tracks_need_two_hits_before_they_count() -> None:
    tracker = PersonTracker()
    assert tracker.update(frame(T0, person(400))) == []
    assert [t.id for t in tracker.update(frame(ms(50), person(402)))] == ["t1"]


def test_debounce_publishes_a_stable_change_after_the_window() -> None:
    builder, tracker = _builder(SnapshotConfig(debounce=timedelta(milliseconds=500)))
    _feed(builder, tracker, ms(0), person(400))
    people_seen: dict[int, int] = {}
    for t in range(100, 1000, 100):
        snapshot = _feed(builder, tracker, ms(t), person(400), person(800))
        if snapshot is not None:
            people_seen[t] = snapshot.people
    assert people_seen == {600: 2}  # stable from 100 ms, published at 100 + 500


def test_debounce_ignores_score_wobble() -> None:
    builder, tracker = _builder(SnapshotConfig(debounce=timedelta(milliseconds=500)))
    cup = Box(480, 380, 520, 420)
    _feed(builder, tracker, ms(0), person(400), objects=(ObjectDetection("cup", 0.8, cup),))
    wobble = _feed(
        builder, tracker, ms(150), person(400), objects=(ObjectDetection("cup", 0.6, cup),)
    )
    assert wobble is None  # same facts, different score: no news
    assert builder.published is not None and builder.published.objects == (("cup", 0.6),)


# ---------------------------------------------------------------- failover


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeDetector:
    def __init__(self, name: str, clock: FakeClock, latency_ms: float = 10.0, **extra: Any) -> None:
        self.name = name
        self.clock = clock
        self.latency_ms = latency_ms
        self.down = False
        self.calls = 0
        self.extra = extra

    def detect(self, frame_: object, measured_at: datetime) -> FrameDetections:
        self.calls += 1
        if self.down:
            raise DetectorUnavailable("socket closed")
        self.clock.now += self.latency_ms / 1000
        return FrameDetections(
            measured_at=measured_at,
            width=W,
            height=H,
            detector=self.name,
            inference_ms=self.latency_ms,
            **self.extra,
        )


def failover(
    latency_ms: float = 10.0,
) -> tuple[FailoverDetector, FakeDetector, FakeDetector, FakeClock]:
    clock = FakeClock()
    remote = FakeDetector("remote", clock, latency_ms)
    local = FakeDetector("local", clock, 30.0)
    detector = FailoverDetector(
        remote,
        local,
        FailoverConfig(budget_ms=150, window=10, min_samples=5, recovery_s=10),
        monotonic=clock,
    )
    return detector, remote, local, clock


def test_failover_uses_primary_while_healthy() -> None:
    detector, _remote, local, _ = failover()
    for _ in range(10):
        assert detector.detect(None, T0).detector == "remote"
    assert local.calls == 0


def test_failover_switches_when_primary_is_down_and_recovers_later() -> None:
    detector, remote, _, clock = failover()
    remote.down = True
    assert detector.detect(None, T0).detector == "local"
    assert detector.active == "local"
    remote.down = False
    clock.now += 5
    assert detector.detect(None, T0).detector == "local"  # still inside the recovery wait
    clock.now += 6
    assert detector.detect(None, T0).detector == "remote"
    assert detector.active == "remote"


def test_failover_switches_when_primary_p95_exceeds_budget() -> None:
    detector, _remote, _, _ = failover(latency_ms=200)
    served = [detector.detect(None, T0).detector for _ in range(7)]
    assert served[:5] == ["remote"] * 5
    assert served[5:] == ["local", "local"]
    assert detector.active == "local"


def test_one_slow_call_in_the_default_window_does_not_fail_over() -> None:
    clock = FakeClock()
    remote = FakeDetector("remote", clock)
    detector = FailoverDetector(remote, FakeDetector("local", clock), monotonic=clock)
    for i in range(40):  # default window of 20: one outlier is the max, not the p95
        remote.latency_ms = 400 if i == 25 else 10
        detector.detect(None, T0)
    assert detector.active == "remote"


def test_slow_calls_above_five_percent_fail_over() -> None:
    clock = FakeClock()
    remote = FakeDetector("remote", clock)
    detector = FailoverDetector(remote, FakeDetector("local", clock), monotonic=clock)
    for i in range(20):
        remote.latency_ms = 400 if i in (15, 18) else 10  # 2 of 20 = 10 % slow
        detector.detect(None, T0)
    assert detector.active == "local"


def test_slow_probe_does_not_switch_back() -> None:
    detector, remote, _, clock = failover()
    remote.down = True
    detector.detect(None, T0)
    remote.down = False
    remote.latency_ms = 400
    clock.now += 11
    assert detector.detect(None, T0).detector == "local"
    assert detector.active == "local"


def test_probe_while_still_down_waits_another_recovery_period() -> None:
    detector, remote, local, clock = failover()
    remote.down = True
    detector.detect(None, T0)
    clock.now += 11
    detector.detect(None, T0)  # probe fails
    calls = remote.calls
    clock.now += 5
    detector.detect(None, T0)
    assert remote.calls == calls  # no probe before the next recovery period
    assert local.calls == 3


def test_composite_merges_markers_and_reports_the_active_detector() -> None:
    clock = FakeClock()
    main = FakeDetector("local", clock, persons=(person(400),))
    markers = FakeDetector("aruco", clock, 2.0, markers=(marker_at(1, 500),))
    merged = CompositeDetector(main, markers).detect(None, T0)
    assert len(merged.persons) == 1 and [m.marker_id for m in merged.markers] == [1]
    assert merged.detector == "local"
    assert merged.inference_ms == pytest.approx(12.0)
    assert CompositeDetector(main, None).detect(None, T0).markers == ()


# ---------------------------------------------------------------- ultralytics translation


def _tensor(values: Any) -> SimpleNamespace:
    """Mimics a torch tensor's `.cpu().numpy()`."""
    array = np.asarray(values, dtype=float)
    return SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: array))


class _Boxes:
    def __init__(self, xyxy: Any, conf: Any, cls: Any = None) -> None:
        self.xyxy = _tensor(xyxy)
        self.conf = _tensor(conf)
        self.cls = _tensor(cls if cls is not None else [0] * len(conf))
        self._n = len(conf)

    def __len__(self) -> int:
        return self._n


def test_pose_results_translate_to_person_detections() -> None:
    kxy = np.zeros((1, 17, 2))
    kxy[0, 0] = (110, 50)
    result = SimpleNamespace(
        boxes=_Boxes([[100, 20, 200, 400]], [0.88]),
        keypoints=SimpleNamespace(xy=_tensor(kxy), conf=_tensor(np.full((1, 17), 0.9))),
    )
    [p] = _persons(result)
    assert p.box == Box(100, 20, 200, 400)
    assert p.score == pytest.approx(0.88)
    assert len(p.keypoints) == 17
    assert (p.keypoints[0].x, p.keypoints[0].y) == (110, 50)
    assert p.keypoints[0].confidence == pytest.approx(0.9)


def test_pose_results_without_keypoint_confidence_default_to_one() -> None:
    result = SimpleNamespace(
        boxes=_Boxes([[0, 0, 10, 10]], [0.5]),
        keypoints=SimpleNamespace(xy=_tensor(np.zeros((1, 17, 2))), conf=None),
    )
    [p] = _persons(result)
    assert all(k.confidence == 1.0 for k in p.keypoints)


def test_empty_results_translate_to_nothing() -> None:
    assert _persons(SimpleNamespace(boxes=None, keypoints=None)) == []
    assert _persons(SimpleNamespace(boxes=_Boxes([], []), keypoints=None)) == []
    assert _objects(SimpleNamespace(boxes=_Boxes([], []), names={})) == []


def test_object_results_drop_people_and_keep_names() -> None:
    result = SimpleNamespace(
        boxes=_Boxes([[0, 0, 10, 10], [5, 5, 50, 50]], [0.9, 0.7], [0, 41]),
        names={0: "person", 41: "cup"},
    )
    [cup] = _objects(result)
    assert cup.label == "cup" and cup.box == Box(5, 5, 50, 50)


# ---------------------------------------------------------------- pipeline


class RecordingLink:
    def __init__(self) -> None:
        self.sent: list[tuple[object, bytes | None]] = []

    def send(self, message: object, *, binary: bytes | None = None) -> None:
        self.sent.append((message, binary))


class ListSource:
    """A finite frame source, like a clip: `finished` once it runs out."""

    def __init__(self, stamps: list[datetime]) -> None:
        self._stamps = stamps
        self.finished = False

    def latest(self, after_serial: int) -> tuple[Any, datetime, int] | None:
        if after_serial >= len(self._stamps):
            self.finished = True
            return None
        return object(), self._stamps[after_serial], after_serial + 1


class ScriptedDetector:
    """Returns a scripted number of people per call; can fail on one call."""

    name = "local"

    def __init__(self, script: list[int], fail_at: int | None = None) -> None:
        self._script = script
        self._fail_at = fail_at
        self._i = 0

    def detect(self, frame_: object, measured_at: datetime) -> FrameDetections:
        i, self._i = self._i, self._i + 1
        if i == self._fail_at:
            raise RuntimeError("cuda out of memory")
        people = [person(400), person(800), person(1000, width=150)][: self._script[i]]
        return frame(measured_at, *people)


def _pipeline(link: RecordingLink, **kwargs: Any) -> VisionPipeline:
    defaults: dict[str, Any] = {
        "screen_id": "demo-01",
        "source": ListSource([]),
        "detector": ScriptedDetector([]),
        "link": link,
        "tracker": PersonTracker(FAST),
    }
    return VisionPipeline(**(defaults | kwargs))


def test_pipeline_sends_snapshots_and_throttled_redacted_keyframes() -> None:
    link = RecordingLink()
    region = (0.1, 0.1, 0.4, 0.9)
    pipeline = _pipeline(
        link, keyframes=lambda _f, _d: RedactedKeyframe(b"\xff\xd8jpeg", (region,), (region,))
    )
    for i in range(20):
        people = [person(400), person(800)][: 1 if i < 10 else 2]
        pipeline.process(object(), frame(ms(150 * i), *people))
    snapshots = [m for m, _ in link.sent if isinstance(m, AudienceSnapshot)]
    keyframes = [(m, b) for m, b in link.sent if isinstance(m, AudienceKeyframe)]
    assert snapshots[0].people == 1 and snapshots[-1].people == 2
    assert [s.seq for s in snapshots] == list(range(len(snapshots)))
    assert 1 <= len(keyframes) <= 3  # 2.85 s of frames, ≤ 1 keyframe per 1.5 s
    header, body = keyframes[0]
    assert body == b"\xff\xd8jpeg" and header.byte_length == len(body)
    assert header.redaction == "allowlist"  # the backend drops a scene keyframe without it
    assert [(b.x1, b.y1, b.x2, b.y2) for b in header.kept] == [region]
    assert [(b.x1, b.y1, b.x2, b.y2) for b in header.heads] == [region]
    assert [k.seq for k, _ in keyframes] == list(range(len(keyframes)))
    assert pipeline.stats.frames == 20 and pipeline.stats.keyframes == len(keyframes)


def test_keyframe_keeps_people_and_padded_objects_and_masks_every_head() -> None:
    cup = ObjectDetection("cup", 0.9, Box(600, 300, 700, 400))
    someone = person(400)
    kept, heads = keyframe_regions(frame(T0, someone, objects=(cup,)))
    assert kept[0] == someone.box
    assert kept[1] == Box(590, 290, 710, 410)  # 10 % padding keeps the label readable
    assert heads == [head_box(someone)]


def test_pipeline_sends_no_keyframe_for_an_empty_scene() -> None:
    link = RecordingLink()
    pipeline = _pipeline(link, keyframes=lambda _f, _d: RedactedKeyframe(b"x", (), ()))
    pipeline.process(object(), frame(T0))
    assert not any(isinstance(m, AudienceKeyframe) for m, _ in link.sent)


def test_pipeline_survives_a_failing_keyframe_encoder() -> None:
    link = RecordingLink()

    def broken(_f: object, _d: FrameDetections) -> RedactedKeyframe:
        raise RuntimeError("encoder")

    pipeline = _pipeline(link, keyframes=broken)
    assert pipeline.process(object(), frame(T0, person(400))) is not None
    assert [type(m) for m, _ in link.sent] == [AudienceSnapshot]


def test_pipeline_reports_held_products_from_markers() -> None:
    link = RecordingLink()
    pipeline = _pipeline(link, held=HeldProductTracker(CATALOG, window=3, min_hits=2))
    for i in range(8):  # held after 2 hits, published once stable for the 500 ms debounce
        holder = person(400, hand=(500, 430))  # the wrist just below the cup's marker
        pipeline.process(object(), frame(ms(200 * i), holder, markers=(marker_at(1, 500),)))
    labels = {o.label for m, _ in link.sent if isinstance(m, AudienceSnapshot) for o in m.objects}
    assert f"{HELD_LABEL_PREFIX}cafe-patio" in labels


def test_run_ends_with_a_finite_source_and_skips_failed_frames() -> None:
    link = RecordingLink()
    pipeline = _pipeline(
        link,
        source=ListSource([ms(200 * i) for i in range(8)]),
        detector=ScriptedDetector([1, 1, 1, 2, 2, 2, 2, 2], fail_at=2),
        snapshots=SnapshotBuilder("demo-01", SnapshotConfig(debounce=timedelta(milliseconds=500))),
    )
    asyncio.run(asyncio.wait_for(pipeline.run(), 5))
    assert pipeline.stats.frames == 7  # the failed frame is skipped, not fatal
    people = [m.people for m, _ in link.sent if isinstance(m, AudienceSnapshot)]
    assert people[0] == 1 and people[-1] == 2


def test_jsonl_recorder_writes_the_eval_replay_contract(tmp_path: Path) -> None:
    out = tmp_path / "rec" / "clip.jsonl"
    forwarded = RecordingLink()
    recorder = JsonlRecorder(out, clip_start=T0, forward=forwarded)
    builder, tracker = _builder()
    for t in (0, 500, 1700):
        snapshot = _feed(builder, tracker, ms(t), person(400))
        if snapshot is not None:
            recorder.send(snapshot)
    recorder.send(
        AudienceKeyframe(screen_id="demo-01", seq=0, measured_at=T0, byte_length=1), binary=b"x"
    )
    recorder.close()
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [line["t_s"] for line in lines] == [0.0, 1.7]
    assert all(set(line) == {"t_s", "snapshot"} for line in lines)
    for line in lines:
        assert isinstance(AGENT_INBOUND.validate_python(line["snapshot"]), AudienceSnapshot)
    assert recorder.lines == 2
    assert len(forwarded.sent) == 3  # snapshots and the keyframe still reach the backend
