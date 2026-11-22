"""Real OpenCV, no camera: runs when the `vision` extra is installed, skips otherwise.

`uv sync --extra dev --extra vision && uv run pytest tests/test_vision_opencv.py`
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from agent.contract import AudienceSnapshot
from agent.vision.types import Box

cv2 = pytest.importorskip("cv2", reason="needs `uv sync --extra vision`")

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
W, H = 1280, 720


def _black(region: np.ndarray) -> bool:
    """Black up to JPEG ringing at the box edges (the source pixels are 0)."""
    return float(region.mean()) < 3 and int(region.max()) <= 40


def _marker_frame(marker_id: int, at: tuple[int, int] = (600, 300), size: int = 120) -> np.ndarray:
    aruco = cv2.aruco
    image = aruco.generateImageMarker(
        aruco.getPredefinedDictionary(aruco.DICT_4X4_50), marker_id, size
    )
    canvas = np.full((H, W, 3), 255, np.uint8)
    x, y = at
    canvas[y : y + size, x : x + size] = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return canvas


def test_aruco_detector_finds_every_shipped_marker() -> None:
    from agent.vision.adapters import ArucoMarkerDetector

    catalog = json.loads((Path(__file__).parents[1] / "props" / "markers.json").read_text())
    detector = ArucoMarkerDetector()
    for marker_id in map(int, catalog):
        [marker] = detector.detect(_marker_frame(marker_id), T0).markers
        assert marker.marker_id == marker_id
        assert marker.box.center == pytest.approx((660, 360), abs=2)


def test_aruco_detector_finds_a_small_marker_and_ignores_a_blank_frame() -> None:
    from agent.vision.adapters import ArucoMarkerDetector

    detector = ArucoMarkerDetector()
    small = detector.detect(_marker_frame(3, size=48), T0)  # ~4 cm at 1.5 m on 720p
    assert [m.marker_id for m in small.markers] == [3]
    blank = np.full((H, W, 3), 255, np.uint8)
    assert detector.detect(blank, T0).markers == ()


def test_preview_blacks_out_the_head_region_and_downscales() -> None:
    from agent.vision.capture import head_masked_frame

    noisy = np.random.default_rng(0).integers(0, 255, (H, W, 3), dtype=np.uint8)
    jpeg = head_masked_frame(noisy, [Box(100, 100, 400, 400)], long_side=W)
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (H, W, 3)
    assert _black(decoded[110:390, 110:390])  # opaque, not a reversible blur
    assert decoded[150:350, 700:900].astype(float).std() > 30
    small = cv2.imdecode(np.frombuffer(head_masked_frame(noisy, []), np.uint8), cv2.IMREAD_COLOR)
    assert max(small.shape[:2]) == 512


def test_preview_clips_boxes_outside_the_frame() -> None:
    from agent.vision.capture import head_masked_frame

    frame = np.zeros((100, 100, 3), np.uint8)
    assert head_masked_frame(frame, [Box(-50, -50, 20, 20), Box(90, 90, 500, 500)])


def test_allowlist_keyframe_hides_a_person_the_detector_missed() -> None:
    """docs/privacy.md G1: only regions the detector vouched for leave the device."""
    from agent.vision.capture import allowlist_keyframe

    noisy = np.random.default_rng(1).integers(40, 255, (H, W, 3), dtype=np.uint8)
    body, head = Box(100, 100, 400, 700), Box(150, 100, 350, 300)
    keyframe = allowlist_keyframe(noisy, [body], [head], long_side=W)
    decoded = cv2.imdecode(np.frombuffer(keyframe.jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert _black(decoded[110:290, 160:340])  # the detected head
    assert decoded[350:650, 120:380].astype(float).std() > 30  # the body (held product): kept
    assert _black(decoded[100:700, 700:1200])  # a missed face over here
    assert keyframe.kept == (pytest.approx((100 / W, 100 / H, 401 / W, 701 / H)),)
    assert keyframe.heads == (pytest.approx((150 / W, 100 / H, 351 / W, 301 / H)),)


def test_allowlist_keyframe_of_nothing_is_all_black() -> None:
    from agent.vision.capture import allowlist_keyframe

    noisy = np.random.default_rng(2).integers(0, 255, (H, W, 3), dtype=np.uint8)
    keyframe = allowlist_keyframe(noisy, [], [Box(-5, -5, -1, -1)])
    decoded = cv2.imdecode(np.frombuffer(keyframe.jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert _black(decoded) and keyframe.kept == () and keyframe.heads == ()


def test_clip_source_samples_on_clip_time_and_finishes(tmp_path: Path) -> None:
    from agent.vision.capture import ClipSource

    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
    for i in range(30):  # 3 s at 10 fps
        writer.write(np.full((48, 64, 3), i * 8, np.uint8))
    writer.release()
    source = ClipSource(str(path), sample_fps=5.0, clip_start=T0)
    stamps = []
    serial = 0
    while (item := source.latest(serial)) is not None:
        _, measured_at, serial = item
        stamps.append((measured_at - T0).total_seconds())
    source.close()
    assert source.finished
    assert stamps == pytest.approx([0.2 * i for i in range(15)])


def test_clip_source_honours_duration(tmp_path: Path) -> None:
    from agent.vision.capture import ClipSource

    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
    for _ in range(30):
        writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()
    source = ClipSource(str(path), sample_fps=10.0, duration_s=1.0, clip_start=T0)
    count = 0
    serial = 0
    while (item := source.latest(serial)) is not None:
        serial = item[2]
        count += 1
    assert count == 11  # t = 0.0 … 1.0 inclusive


def test_unopenable_video_fails_loudly(tmp_path: Path) -> None:
    from agent.vision.capture import ClipSource

    with pytest.raises(RuntimeError):
        ClipSource(str(tmp_path / "missing.mp4"))


def test_live_video_replay_seeks_to_current_frame_after_inference_stalls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.vision.capture import VideoFileSource

    path = tmp_path / "paced.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 48))
    for i in range(30):
        writer.write(np.full((48, 64, 3), i * 8, np.uint8))
    writer.release()
    clock = [0.0]
    monkeypatch.setattr("agent.vision.capture.time.monotonic", lambda: clock[0])
    source = VideoFileSource(str(path), loop=False)
    try:
        first = source.latest(0)
        assert first is not None and first[0].mean() == pytest.approx(0, abs=2)
        clock[0] = 1.5
        current = source.latest(1)
        assert current is not None and current[0].mean() == pytest.approx(120, abs=2)
        clock[0] = 3.0
        assert source.latest(2) is None and source.finished
    finally:
        source.close()


def test_snapshot_import_is_the_vendored_contract() -> None:
    assert AudienceSnapshot.model_fields["people"].annotation is not None
