"""Long sessions, missing observations and slow replay must preserve fresh facts."""

import sys
from types import SimpleNamespace

import pytest

from agent.vision.__main__ import _TimedDetector
from agent.vision.capture import VideoFileSource
from agent.vision.tracking import PersonTracker
from tests.test_vision import FAST, RecordingLink, _pipeline, frame, ms, person


def test_long_detector_gap_cannot_revive_an_expired_track():
    tracker = PersonTracker(FAST)
    [first] = tracker.update(frame(ms(0), person()))
    [returned] = tracker.update(frame(ms(2000), person()))
    assert returned.id != first.id
    assert returned.dwell_s == 0
    assert returned.first_seen == ms(2000)


def test_stale_frame_cannot_rewind_attention_or_publication():
    link = RecordingLink()
    pipeline = _pipeline(link)
    pipeline.process(None, frame(ms(1000), person()))
    count = len(link.sent)
    assert pipeline.process(None, frame(ms(900))) is None
    assert len(link.sent) == count
    assert pipeline.stats.frames == 1
    assert pipeline.stats.stale_frames == 1


def test_duplicate_frame_does_not_confirm_a_person_twice():
    link = RecordingLink()
    pipeline = _pipeline(link, tracker=PersonTracker())
    pipeline.process(None, frame(ms(0), person()))
    pipeline.process(None, frame(ms(0), person()))
    assert pipeline.stats.frames == 1
    assert pipeline.stats.stale_frames == 1


def fake_video(monkeypatch, *, fps=10, frames=30):
    clock = [0.0]

    class Capture:
        position = 0

        def __init__(self):
            self.reads = []

        def isOpened(self):
            return True

        def get(self, prop):
            return fps if prop == 0 else frames

        def set(self, prop, value):
            self.position = int(value)
            return True

        def read(self):
            index = self.position
            self.position += 1
            self.reads.append(index)
            return (index < frames, index)

        def release(self):
            pass

    capture = Capture()
    cv2 = SimpleNamespace(
        VideoCapture=lambda _: capture,
        CAP_PROP_FPS=0,
        CAP_PROP_FRAME_COUNT=1,
        CAP_PROP_POS_FRAMES=2,
    )
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    monkeypatch.setattr("agent.vision.capture.time.monotonic", lambda: clock[0])
    return clock, capture


def test_live_replay_skips_to_playback_time_after_slow_inference(monkeypatch):
    clock, capture = fake_video(monkeypatch)
    source = VideoFileSource("fake.avi", loop=False)
    assert source.latest(0)[0] == 0
    clock[0] = 1.5
    assert source.latest(1)[0] == 15
    assert capture.reads == [0, 15]
    assert source.latest(2) is None
    clock[0] = 3.1
    assert source.latest(2) is None
    assert source.finished


def test_live_replay_keeps_its_schedule_when_looping(monkeypatch):
    clock, _ = fake_video(monkeypatch)
    source = VideoFileSource("fake.avi")
    source.latest(0)
    clock[0] = 4.5
    assert source.latest(1)[0] == 15


def test_live_replay_clock_starts_after_model_warmup(monkeypatch):
    clock, _ = fake_video(monkeypatch)
    source = VideoFileSource("fake.avi", loop=False)
    clock[0] = 10.0  # loading and warming the models is outside playback time
    assert source.latest(0)[0] == 0
    clock[0] = 11.5
    assert source.latest(1)[0] == 15


@pytest.mark.parametrize("fps, frames", [(float("nan"), 30), (0, 30), (10, 0)])
def test_live_replay_rejects_unusable_video_metadata(monkeypatch, fps, frames):
    fake_video(monkeypatch, fps=fps, frames=frames)
    with pytest.raises(RuntimeError, match="frame rate or duration"):
        VideoFileSource("fake.avi")


def test_live_timing_does_not_retain_one_sample_per_frame():
    detector = SimpleNamespace(name="test", detect=lambda _, at: frame(at))
    timed = _TimedDetector(detector)
    for i in range(10_000):
        timed.detect(None, ms(i))
    assert timed.ms == []


def test_recording_timing_keeps_all_samples_for_exact_percentiles():
    detector = SimpleNamespace(name="test", detect=lambda _, at: frame(at))
    timed = _TimedDetector(detector, collect_samples=True)
    for i in range(3):
        timed.detect(None, ms(i))
    assert len(timed.ms) == 3
