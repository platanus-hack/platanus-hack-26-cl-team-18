"""Failure boundaries that must keep live perception accurate and available."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from agent.vision.adapters import UltralyticsDetector
from agent.vision.detector import CompositeDetector, FailoverDetector
from agent.vision.remote import RemoteGpuDetector
from agent.vision.types import FrameDetections

T0 = datetime(2026, 11, 21, tzinfo=UTC)


def remote(monkeypatch, replies):
    detector = RemoteGpuDetector("ws://unused", encoder=lambda f: (b"jpeg", f.shape[1], f.shape[0]))
    answers = iter(replies)

    class Socket:
        def send(self, packet):
            pass

    monkeypatch.setattr(detector, "_connection", lambda: Socket())
    monkeypatch.setattr(detector, "_drain", lambda _: None)

    def reply(_socket, seq, _deadline):
        detector._in_flight.pop(seq)
        return {"seq": seq, **next(answers)}

    monkeypatch.setattr(detector, "_await", reply)
    return detector


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"persons": {}, "objects": []},
        {"persons": [{"box": [0, 0, 10], "confidence": 0.9}], "objects": []},
        {"persons": None, "objects": []},
        {"persons": [{"box": [0, 0, 10, 10], "confidence": float("nan")}], "objects": []},
        {"persons": [{"box": [0, 0, 10, 10], "confidence": 2}], "objects": []},
        {
            "persons": [],
            "objects": [{"label": "cup", "box": [0, 0, float("inf"), 10], "confidence": 1}],
        },
        {"persons": [], "objects": [], "server_ms": "invalid"},
    ],
)
def test_malformed_remote_reply_uses_local_fallback(monkeypatch, payload):
    detector = remote(monkeypatch, [payload])
    local = SimpleNamespace(
        name="local", detect=lambda _, at: FrameDetections(at, 20, 20, detector="local")
    )
    failover = FailoverDetector(detector, local)
    result = failover.detect(np.zeros((20, 20, 3)), T0)
    assert result.detector == failover.active == "local"


def test_remote_rescans_objects_when_resolution_changes(monkeypatch):
    cup = {"label": "cup", "box": [1, 1, 5, 5], "confidence": 0.8}
    detector = remote(
        monkeypatch,
        [
            {"persons": [], "objects": [cup]},
            {"persons": [], "objects": [{**cup, "label": "bottle"}]},
        ],
    )
    detector.detect(np.zeros((20, 20, 3)), T0)
    result = detector.detect(np.zeros((10, 10, 3)), T0 + timedelta(milliseconds=10))
    assert result.objects[0].box.x2 == 5
    assert result.objects[0].label == "bottle"


def test_remote_reconnect_does_not_reuse_old_object_facts(monkeypatch):
    cup = {"label": "cup", "box": [1, 1, 5, 5], "confidence": 0.8}
    detector = remote(
        monkeypatch,
        [
            {"persons": [], "objects": [cup]},
            {"persons": [], "objects": []},
        ],
    )
    image = np.zeros((20, 20, 3))
    assert detector.detect(image, T0).objects
    detector.close()
    assert detector.detect(image, T0 + timedelta(seconds=10)).objects == ()


def test_local_rescans_objects_when_resolution_changes(monkeypatch):
    import sys

    calls = []

    class Model:
        def __init__(self, name):
            self.name = name

        def predict(self, image, **kwargs):
            calls.append((self.name, image.shape))
            return [SimpleNamespace(boxes=None, keypoints=None)]

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=Model))
    detector = UltralyticsDetector(object_every=100)
    detector.detect(np.zeros((20, 20, 3)), T0)
    detector.detect(np.zeros((10, 10, 3)), T0 + timedelta(milliseconds=10))
    assert [shape for name, shape in calls if name == "yolo26s.pt"] == [(20, 20, 3), (10, 10, 3)]


def test_marker_composition_preserves_latency_breakdown():
    main = SimpleNamespace(
        name="remote",
        detect=lambda _, at: FrameDetections(at, 20, 20, extras={"round_trip_ms": 12}),
    )
    markers = SimpleNamespace(detect=lambda _, at: FrameDetections(at, 20, 20))
    assert CompositeDetector(main, markers).detect(None, T0).extras == {"round_trip_ms": 12}
