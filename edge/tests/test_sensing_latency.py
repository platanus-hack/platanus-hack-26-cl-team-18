"""Regression seams for scene freshness, safety and non-blocking enrichment."""

import asyncio
import json
import threading

import pytest

from agent.backend_link import BackendLink
from agent.contract import AudienceKeyframe, AudienceSnapshot
from agent.vision.minor import MinorReading
from agent.vision.snapshot import SnapshotBuilder
from agent.vision.tracking import PersonTracker
from tests.test_vision import RecordingLink, _feed, _pipeline, frame, ms, person


def test_trace_includes_confirmation_and_keeps_latest_frame_time():
    tracker, builder = PersonTracker(), SnapshotBuilder("test")
    result = None
    for t in range(0, 701, 50):
        result = _feed(builder, tracker, ms(t), person()) or result
    assert result.people == 1
    assert result.measured_at == ms(550)
    assert result.candidate_at == ms(0)


def test_positive_safety_vote_does_not_wait_another_debounce():
    tracker, builder = PersonTracker(), SnapshotBuilder("test")
    for t in range(0, 1001, 100):
        _feed(builder, tracker, ms(t), person())
    tracks = tracker.update(frame(ms(1100), person()))
    snapshot = builder.build(
        measured_at=ms(1100), tracks=tracks, objects=(), held=[], minor=MinorReading(True, 0.8)
    )
    assert snapshot is not None and snapshot.minor_likely.value
    snapshot = builder.build(measured_at=ms(1200), tracks=tracks, objects=(), held=[])
    assert snapshot is None or snapshot.minor_likely.value


def test_departure_stops_counting_before_id_association_expires():
    tracker = PersonTracker()
    tracker.update(frame(ms(0), person()))
    [track] = tracker.update(frame(ms(100), person()))
    assert tracker.update(frame(ms(200)))
    assert tracker.update(frame(ms(450))) == []
    [returned] = tracker.update(frame(ms(500), person()))
    assert returned.id == track.id


def test_looking_change_is_not_debounced_twice():
    tracker, builder = PersonTracker(), SnapshotBuilder("test")
    for t in range(0, 1001, 100):
        _feed(builder, tracker, ms(t), person())
    last = None
    for t in range(1100, 1701, 100):
        last = _feed(builder, tracker, ms(t), person(facing=False)) or last
    assert last is not None and last.looking == 0


def test_keyframes_follow_deadline_without_a_snapshot_heartbeat():
    link = RecordingLink()
    pipeline = _pipeline(link, keyframes=lambda *_: b"blurred")
    for t in range(0, 3101, 100):
        pipeline.process(object(), frame(ms(t), person()))
    headers = [m for m, _ in link.sent if isinstance(m, AudienceKeyframe)]
    assert [m.measured_at for m in headers] == [ms(0), ms(1500), ms(3000)]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_slow_encoder_does_not_block_sensor_link_or_queue_frames():
    started, release = threading.Event(), threading.Event()
    link = RecordingLink()

    def encode(*_):
        started.set()
        release.wait(2)
        return b"blurred"

    pipeline = _pipeline(link, keyframes=encode)
    from tests.test_vision import ListSource, ScriptedDetector

    pipeline._source = ListSource([ms(t) for t in range(0, 2001, 100)])
    pipeline._detector = ScriptedDetector([1] * 21)
    task = asyncio.create_task(pipeline.run())
    try:
        async with asyncio.timeout(1):
            while not started.is_set():
                await asyncio.sleep(0.001)
            while pipeline.stats.frames < 10:
                await asyncio.sleep(0.001)
        assert pipeline.stats.snapshots >= 1
    finally:
        release.set()
        await task


@pytest.mark.anyio
async def test_reconnect_sends_latest_state_and_preserves_events_and_pairs():
    from tests.test_backend_link import T0, FakeBackend, utterance, wait_for

    backend = FakeBackend()
    link = BackendLink(await backend.start())
    for seq in range(64):
        link.send(
            AudienceSnapshot(
                screen_id="test",
                seq=seq,
                measured_at=T0,
                people=0,
                looking=0,
                minor_likely={"value": False, "confidence": 0},
            )
        )
    link.send(utterance(1))
    for seq in range(2):
        link.send(
            AudienceKeyframe(screen_id="test", seq=seq, measured_at=T0, byte_length=1),
            binary=bytes([seq]),
        )
    task = asyncio.create_task(link.run())
    try:
        await wait_for(lambda: len(backend.frames) >= 4)
        frames = backend.frames
        assert json.loads(frames[0])["seq"] == 63
        assert json.loads(frames[1])["type"] == "audience.utterance"
        assert json.loads(frames[2])["seq"] == 1 and frames[3] == b"\x01"
        assert link.stats.dropped == 64
    finally:
        task.cancel()
        await backend.stop()


def test_unavailable_primary_at_start_uses_warm_fallback_without_a_second_timeout():
    from agent.vision.detector import FailoverDetector
    from tests.test_vision import ScriptedDetector

    primary, secondary = ScriptedDetector([]), ScriptedDetector([1])
    detector = FailoverDetector(primary, secondary, primary_ready=False, monotonic=lambda: 1)
    assert detector.detect(object(), ms(0)).persons
    assert primary._i == 0


def test_local_prewarm_initializes_both_models_and_resets_object_deadline(monkeypatch):
    import sys
    from types import SimpleNamespace

    import numpy as np

    from agent.vision.adapters import UltralyticsDetector

    calls = []

    class Model:
        def __init__(self, name):
            self.name = name

        def predict(self, *args, **kwargs):
            calls.append(self.name)
            return [SimpleNamespace(boxes=None, keypoints=None)]

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=Model))
    detector = UltralyticsDetector(
        object_interval=__import__("datetime").timedelta(milliseconds=200)
    )
    detector.warmup()
    assert calls == ["yolo26n-pose.pt", "yolo26s.pt"]
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    for t in [0, 50, 190, 200, 350, 450]:
        detector.detect(image, ms(t))
    assert calls.count("yolo26s.pt") == 4  # warmup + 0, 200, 450


@pytest.mark.anyio
async def test_stale_reconnect_state_expires_but_events_keep_their_order():
    from tests.test_backend_link import T0, FakeBackend, utterance, wait_for

    clock = [0.0]
    backend = FakeBackend()
    link = BackendLink(await backend.start(), monotonic=lambda: clock[0])
    link.send(
        AudienceSnapshot(
            screen_id="test",
            seq=0,
            measured_at=T0,
            people=0,
            looking=0,
            minor_likely={"value": False, "confidence": 0},
        )
    )
    link.send(utterance(10))
    clock[0] = 2.0
    task = asyncio.create_task(link.run())
    try:
        await wait_for(lambda: len(backend.frames) >= 1)
        assert json.loads(backend.frames[0])["type"] == "audience.utterance"
        assert link.stats.dropped == 1
    finally:
        task.cancel()
        await backend.stop()


@pytest.mark.anyio
async def test_inflight_state_is_not_allowed_to_remove_a_newer_pending_message():
    from agent.contract import AudienceSnapshot
    from tests.test_backend_link import T0, utterance

    entered, release = asyncio.Event(), asyncio.Event()
    sent = []

    class Socket:
        async def send(self, text):
            sent.append(json.loads(text))
            if len(sent) == 1:
                entered.set()
                await release.wait()

        async def recv(self):
            await asyncio.Future()

    link = BackendLink("ws://unused", max_pending=2)

    def snapshot(seq):
        return AudienceSnapshot(
            screen_id="test",
            seq=seq,
            measured_at=T0,
            people=0,
            looking=0,
            minor_likely={"value": False, "confidence": 0},
        )

    link.send(snapshot(0))
    task = asyncio.create_task(link._pump(Socket()))
    try:
        await entered.wait()
        link.send(snapshot(1))
        link.send(snapshot(2))
        link.send(utterance(3))
        release.set()
        async with asyncio.timeout(1):
            while link.stats.sent < 3:
                await asyncio.sleep(0.001)
        assert [(s["type"], s["seq"]) for s in sent] == [
            ("audience.snapshot", 0),
            ("audience.snapshot", 2),
            ("audience.utterance", 3),
        ]
    finally:
        task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await task


def test_trace_includes_attention_hold_and_presence_hold():
    tracker, builder = PersonTracker(), SnapshotBuilder("test")
    for t in range(0, 1001, 100):
        _feed(builder, tracker, ms(t), person())
    turned = []
    for t in range(1100, 1801, 100):
        snapshot = _feed(builder, tracker, ms(t), person(facing=False))
        if snapshot and not snapshot.looking:
            turned.append(snapshot)
    assert turned[0].candidate_at == ms(1100)
    departed = []
    for t in range(1900, 3001, 100):
        snapshot = _feed(builder, tracker, ms(t))
        if snapshot and not snapshot.people:
            departed.append(snapshot)
    assert departed[0].candidate_at == ms(1900)


def test_two_frame_passerby_never_becomes_a_published_audience():
    tracker, builder = PersonTracker(), SnapshotBuilder("test")
    snapshots = []
    for t in range(0, 1601, 100):
        people = [person()] if t in (100, 200) else []
        snapshot = _feed(builder, tracker, ms(t), *people)
        if snapshot:
            snapshots.append(snapshot)
    assert snapshots and all(s.people == 0 for s in snapshots)


@pytest.mark.anyio
async def test_async_keyframe_delivers_only_the_encoder_output_with_matching_header():
    from tests.test_backend_link import wait_for

    link = RecordingLink()
    pipeline = _pipeline(link, keyframes=lambda *_: b"redacted")
    pipeline.process(object(), frame(ms(0), person()))
    await wait_for(lambda: pipeline.stats.keyframes == 1)
    [(header, binary)] = [(m, b) for m, b in link.sent if isinstance(m, AudienceKeyframe)]
    assert binary == b"redacted" and header.byte_length == 8


def test_remote_object_requests_follow_capture_deadlines_without_frame_count_dependency(
    monkeypatch,
):
    from datetime import timedelta

    import numpy as np

    from agent.vision.remote import HEADER_PREFIX, RemoteGpuDetector

    requested = []

    class Socket:
        def send(self, packet):
            length = HEADER_PREFIX.unpack(packet[:4])[0]
            requested.append(json.loads(packet[4 : 4 + length])["objects"])

    detector = RemoteGpuDetector(
        "ws://unused",
        encoder=lambda _: (b"jpeg", 10, 10),
        object_every=100,
        object_interval=timedelta(milliseconds=200),
    )
    monkeypatch.setattr(detector, "_connection", lambda: Socket())
    monkeypatch.setattr(detector, "_drain", lambda _: None)

    def reply(_socket, seq, _deadline):
        detector._in_flight.pop(seq)
        return {"seq": seq, "persons": [], "objects": []}

    monkeypatch.setattr(detector, "_await", reply)
    for t in [0, 50, 190, 200, 350, 450]:
        detector.detect(np.zeros((10, 10, 3), dtype=np.uint8), ms(t))
    assert requested == [True, False, False, True, False, True]


def test_clip_recording_does_not_mix_wall_clock_boundaries_with_clip_time(tmp_path):
    from datetime import UTC, datetime

    from agent.vision.pipeline import JsonlRecorder
    from tests.test_backend_link import T0

    at = ms(600)
    snapshot = AudienceSnapshot(
        screen_id="test",
        seq=0,
        measured_at=at,
        candidate_at=ms(0),
        detected_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
        people=0,
        looking=0,
        minor_likely={"value": False, "confidence": 0},
    )
    path = tmp_path / "clip.jsonl"
    recorder = JsonlRecorder(path, T0)
    recorder.send(snapshot)
    recorder.close()
    saved = json.loads(path.read_text())["snapshot"]
    assert saved["detected_at"] is None and saved["published_at"] is None
    assert saved["candidate_at"] == snapshot.model_dump(mode="json")["candidate_at"]
