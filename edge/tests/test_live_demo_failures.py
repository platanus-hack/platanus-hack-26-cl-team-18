"""Failure modes that would break a live demo: each test reproduces one, then pins the fix."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent.contract import AudienceSnapshot
from agent.vision.geometry import head_box
from agent.vision.pipeline import VisionConfig, VisionPipeline
from agent.vision.tracking import PersonTracker
from agent.vision.types import Box, FrameDetections, Keypoint, PersonDetection
from tests.test_vision import FAST, T0, failover, frame, person

# ---------------------------------------------------------------- remote GPU failover


def test_unexpected_primary_error_fails_over_to_local() -> None:
    """A bug or an unclassified error in the GPU client must cost the remote, not perception."""
    detector, remote, local, _ = failover()

    def broken(_frame: object, _at: datetime) -> FrameDetections:
        raise RuntimeError("unexpected client failure")

    remote.detect = broken  # type: ignore[method-assign]
    assert detector.detect(None, T0).detector == "local"
    assert detector.active == "local"
    detector.detect(None, T0)
    assert local.calls == 2  # stays on local until the recovery probe


# ---------------------------------------------------------------- face blur


def test_head_box_with_face_keypoints_outside_the_person_box_still_blurs_the_top() -> None:
    """Stray face keypoints must not make the blur box invalid (that drops the keyframe)."""
    kps = [Keypoint(0, 0, 0.0)] * 17
    kps[0] = Keypoint(310, 60, 0.9)
    kps[1] = Keypoint(305, 55, 0.9)
    kps[2] = Keypoint(315, 55, 0.9)
    p = PersonDetection(box=Box(100, 50, 200, 400), score=0.9, keypoints=tuple(kps))
    head = head_box(p)
    assert (head.x1, head.y1, head.x2) == (100, 50, 200)
    assert head.y2 > head.y1


# ---------------------------------------------------------------- vision loop


class _Link:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, message: Any, *, binary: bytes | None = None) -> None:
        self.sent.append(message)


class _Camera:
    """A wall-clock camera that can be unplugged."""

    def __init__(self) -> None:
        self.serial = 0
        self.unplugged = False

    def latest(self, after_serial: int) -> tuple[Any, datetime, int] | None:
        if self.unplugged:
            return None
        self.serial += 1
        return object(), datetime.now(UTC), self.serial


class _Detector:
    name = "fake"

    def detect(self, _frame: object, measured_at: datetime) -> FrameDetections:
        return frame(measured_at, person(400))


async def _run_for(pipeline: VisionPipeline, seconds: float) -> None:
    task = asyncio.create_task(pipeline.run())
    await asyncio.sleep(seconds)
    assert not task.done(), "the vision loop stopped"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_one_bad_frame_does_not_stop_the_vision_loop() -> None:
    class _FlakyTracker(PersonTracker):
        def __init__(self) -> None:
            super().__init__(FAST)
            self.calls = 0

        def update(self, frame: FrameDetections):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                raise ValueError("box corners are inverted")
            return super().update(frame)

    link = _Link()
    tracker = _FlakyTracker()
    pipeline = VisionPipeline(
        screen_id="demo-01",
        source=_Camera(),
        detector=_Detector(),
        link=link,
        tracker=tracker,
        config=VisionConfig(idle_sleep_s=0.001),
    )
    asyncio.run(_run_for(pipeline, 0.2))
    assert tracker.calls > 1
    assert any(isinstance(m, AudienceSnapshot) for m in link.sent)


def test_blind_camera_publishes_an_empty_room_once_then_goes_quiet() -> None:
    """An unplugged camera must not leave the last group on screen forever."""
    link = _Link()
    camera = _Camera()
    pipeline = VisionPipeline(
        screen_id="demo-01",
        source=camera,
        detector=_Detector(),
        link=link,
        tracker=PersonTracker(FAST),
        config=VisionConfig(idle_sleep_s=0.001, blind_after=timedelta(milliseconds=100)),
    )

    async def scenario() -> None:
        task = asyncio.create_task(pipeline.run())
        await asyncio.sleep(0.15)
        snapshots = [m for m in link.sent if isinstance(m, AudienceSnapshot)]
        assert snapshots and snapshots[-1].people == 1
        camera.unplugged = True
        await asyncio.sleep(1.2)  # blind_after + tracker gap + 500 ms debounce
        snapshots = [m for m in link.sent if isinstance(m, AudienceSnapshot)]
        assert snapshots[-1].people == 0
        assert snapshots[-1].tracklets == []
        sent = len(link.sent)
        await asyncio.sleep(0.3)
        assert len(link.sent) == sent  # quiet afterwards: the console sees the camera lost
        assert [s.seq for s in snapshots] == sorted({s.seq for s in snapshots})
        camera.unplugged = False
        await asyncio.sleep(0.8)  # the person is debounced back in
        back = [m for m in link.sent[sent:] if isinstance(m, AudienceSnapshot)]
        assert back and back[-1].people == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


# ---------------------------------------------------------------- camera capture


class _FakeCapture:
    """cv2.VideoCapture stand-in driven by a shared script of read results."""

    def __init__(self, script: _Script) -> None:
        self._script = script
        self._grabbed: Any = None
        script.opened += 1

    def set(self, *_args: object) -> bool:
        return True

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, Any]:
        return self._script.next()

    def grab(self) -> bool:
        ok, self._grabbed = self._script.next()
        return ok

    def retrieve(self) -> tuple[bool, Any]:
        frame = self._grabbed
        if frame is not None:
            frame.decode_started = datetime.now(UTC)
        time.sleep(self._script.decode_s)
        return True, frame

    def release(self) -> None:
        self._script.released_on.append(threading.current_thread().name)


class _Script:
    def __init__(self) -> None:
        self.mode = "ok"
        self.opened = 0
        self.lock = threading.Lock()
        self.released_on: list[str] = []
        self.read_s = 0.002
        self.decode_s = 0.0

    def next(self) -> tuple[bool, Any]:
        time.sleep(self.read_s)
        with self.lock:
            mode = self.mode
        if mode == "raise":
            raise RuntimeError("cv2.error: device vanished")
        if mode == "unplugged":
            return False, None
        if mode == "empty":
            return True, _Frame(0)
        if mode == "none":
            return True, None
        if mode == "replugged" and self.opened < 2:
            return False, None  # the old handle stays dead until reopened
        return True, _Frame(1)


class _Frame:
    def __init__(self, size: int) -> None:
        self.size = size
        self.decode_started: datetime | None = None


@pytest.fixture
def fake_cv2(monkeypatch: pytest.MonkeyPatch) -> _Script:
    script = _Script()
    module = types.SimpleNamespace(
        VideoCapture=lambda _index: _FakeCapture(script),
        VideoWriter=types.SimpleNamespace(fourcc=lambda *_: 0),
        CAP_PROP_FOURCC=0,
        CAP_PROP_FRAME_WIDTH=1,
        CAP_PROP_FRAME_HEIGHT=2,
        CAP_PROP_FPS=3,
        CAP_PROP_BUFFERSIZE=4,
    )
    monkeypatch.setitem(sys.modules, "cv2", module)
    return script


def _wait_for(condition: Any, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_camera_reopens_after_being_unplugged(fake_cv2: _Script) -> None:
    from agent.vision.capture import LatestFrameCamera

    camera = LatestFrameCamera(0, reopen_after_s=0.05)
    try:
        assert _wait_for(lambda: camera.latest(0) is not None)
        with fake_cv2.lock:
            fake_cv2.mode = "replugged"  # read fails until the device is reopened
        time.sleep(0.02)
        item = camera.latest(0)
        assert item is not None
        serial = item[2]
        assert _wait_for(lambda: camera.latest(serial) is not None), "never reopened"
        assert fake_cv2.opened >= 2
    finally:
        camera.close()


def test_camera_is_released_by_its_own_thread_after_the_last_read(fake_cv2: _Script) -> None:
    from agent.vision.capture import LatestFrameCamera

    camera = LatestFrameCamera(0)
    assert _wait_for(lambda: camera.latest(0) is not None)
    camera.close()
    assert fake_cv2.released_on == ["camera"]  # never from close() under a live read


def test_camera_close_never_releases_under_a_wedged_read(fake_cv2: _Script) -> None:
    from agent.vision.capture import LatestFrameCamera

    camera = LatestFrameCamera(0)
    assert _wait_for(lambda: camera.latest(0) is not None)
    fake_cv2.read_s = 0.5  # the next read wedges past close()'s join
    time.sleep(0.05)
    camera.close(timeout=0.05)
    assert fake_cv2.released_on == []
    assert _wait_for(lambda: fake_cv2.released_on == ["camera"]), "released once the read returned"


def test_camera_stamps_measured_at_at_grab_not_after_decode(fake_cv2: _Script) -> None:
    from agent.vision.capture import LatestFrameCamera

    fake_cv2.decode_s = 0.05
    camera = LatestFrameCamera(0)
    try:
        assert _wait_for(lambda: camera.latest(0) is not None)
        item = camera.latest(0)
        assert item is not None
        frame, measured_at, _ = item
        assert frame.decode_started is not None
        assert measured_at <= frame.decode_started  # the decode isn't in measured_at
    finally:
        camera.close()


@pytest.mark.parametrize("mode", ["raise", "empty", "none"])
def test_camera_survives_errors_and_never_publishes_empty_frames(
    fake_cv2: _Script, mode: str
) -> None:
    from agent.vision.capture import LatestFrameCamera

    with fake_cv2.lock:
        fake_cv2.mode = mode
    camera = LatestFrameCamera(0, reopen_after_s=0.05)
    try:
        time.sleep(0.1)
        assert camera.latest(0) is None
        with fake_cv2.lock:
            fake_cv2.mode = "ok"
        assert _wait_for(lambda: camera.latest(0) is not None), "capture thread died"
    finally:
        camera.close()
