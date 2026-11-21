"""Real ByteTrack on synthetic detections; no model weights, camera or images."""

from dataclasses import replace
from datetime import timedelta

import pytest

pytest.importorskip("ultralytics", reason="needs vision extra")
pytest.importorskip("lap", reason="needs vision extra")

from agent.vision.association import ByteTrackAssociator
from agent.vision.tracking import PersonTracker, TrackerConfig
from tests.test_vision import frame, ms, person


def tracker():
    config = TrackerConfig(min_hits=1)
    return PersonTracker(config, associator=ByteTrackAssociator(config))


def test_bytetrack_recovers_a_weak_detection_without_creating_a_weak_track():
    tracks = tracker()
    [first] = tracks.update(frame(ms(0), person(400)))
    [weak] = tracks.update(frame(ms(100), person(410, score=0.2), person(900, score=0.2)))
    assert weak.id == first.id
    assert weak.box.x1 == 410
    assert weak.dwell_s == 0.1
    assert tracker().update(frame(ms(0), person(score=0.2))) == []


def test_bytetrack_ids_survive_detection_order_changes_and_short_occlusion():
    tracks = tracker()
    first = tracks.update(frame(ms(0), person(100), person(800)))
    assert len(first) == 2
    tracks.update(frame(ms(100)))
    returned = tracks.update(frame(ms(200), person(810), person(110)))
    assert [(t.id, t.box.x1) for t in returned] == [(first[0].id, 110), (first[1].id, 810)]


def test_bytetrack_gap_and_explicit_reset_never_reuse_public_ids():
    tracks = tracker()
    [first] = tracks.update(frame(ms(0), person()))
    [returned] = tracks.update(frame(ms(2000), person()))
    assert returned.id != first.id and returned.dwell_s == 0
    tracks.reset()
    [reset] = tracks.update(frame(ms(2100), person()))
    assert reset.id not in {first.id, returned.id}


def test_bytetrack_does_not_expose_kalman_predicted_pose_for_safety():
    tracks = tracker()
    tracks.update(frame(ms(0), person(400)))
    current = person(415, facing=False)
    [view] = tracks.update(frame(ms(100), current))
    assert view.box is current.box
    [view] = tracks.update(frame(ms(700), current))
    assert not view.looking


def test_bytetrack_resets_association_on_resolution_change():
    tracks = tracker()
    original = frame(ms(0), person())
    [first] = tracks.update(original)
    resized = replace(original, measured_at=ms(100), width=640, height=360)
    [changed] = tracks.update(resized)
    assert changed.id != first.id
    assert changed.first_seen - first.first_seen == timedelta(milliseconds=100)


def test_bytetrack_instances_keep_independent_sessions():
    first, second = tracker(), tracker()
    [one] = first.update(frame(ms(0), person(400)))
    second.update(frame(ms(0), person(400)))
    second.reset()
    [later] = first.update(frame(ms(100), person(410)))
    assert later.id == one.id
