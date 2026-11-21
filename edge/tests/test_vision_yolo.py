"""Real YOLO26 on real photos: runs when the `vision` extra is installed, skips otherwise.

Uses the sample images that ship inside the ultralytics package (no camera, no
clip). The first run downloads the YOLO26 and YOLOE-26 weights into the cwd and
bakes the object vocabulary once (MobileCLIP, ~250 MB);
set `YOLO_POSE` / `YOLO_OBJECTS` to point at exported weights instead.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

ultralytics = pytest.importorskip("ultralytics", reason="needs `uv sync --extra vision`")
cv2 = pytest.importorskip("cv2", reason="needs `uv sync --extra vision`")

from ultralytics.utils import ASSETS  # noqa: E402

from agent.vision.adapters import UltralyticsDetector  # noqa: E402
from agent.vision.geometry import is_looking  # noqa: E402
from agent.vision.pipeline import VisionPipeline  # noqa: E402
from agent.vision.snapshot import SnapshotBuilder, SnapshotConfig  # noqa: E402
from agent.vision.tracking import PersonTracker  # noqa: E402
from agent.vision.vocabulary import OBJECT_LABELS  # noqa: E402

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


@pytest.fixture(scope="module")
def detector() -> UltralyticsDetector:
    return UltralyticsDetector(
        pose_model=os.environ.get("YOLO_POSE", "yolo26n-pose.pt"),
        object_model=os.environ.get("YOLO_OBJECTS", "yoloe-26s-seg.pt"),
        object_every=1,
        device="cpu",
    )


def test_bus_photo_finds_the_people_with_pose(detector: UltralyticsDetector) -> None:
    image = cv2.imread(str(ASSETS / "bus.jpg"))
    result = detector.detect(image, T0)
    assert (result.width, result.height) == (image.shape[1], image.shape[0])
    assert len(result.persons) >= 3  # four people stand in front of the bus
    assert all(len(p.keypoints) == 17 for p in result.persons)
    assert all(0 < p.score <= 1 for p in result.persons)
    assert result.inference_ms > 0


def test_frontal_faces_count_as_looking(detector: UltralyticsDetector) -> None:
    image = cv2.imread(str(ASSETS / "zidane.jpg"))  # two men facing the camera
    result = detector.detect(image, T0)
    assert len(result.persons) >= 2
    assert sum(is_looking(p) for p in result.persons) >= 1


def test_objects_come_only_from_the_vocabulary(detector: UltralyticsDetector) -> None:
    image = cv2.imread(str(ASSETS / "bus.jpg"))
    labels = {o.label for o in detector.detect(image, T0).objects}
    assert labels <= OBJECT_LABELS  # YOLOE only knows the prompts; no "bus", no "person"


def test_full_pipeline_on_a_real_photo_produces_a_valid_snapshot(
    detector: UltralyticsDetector,
) -> None:
    image = cv2.imread(str(ASSETS / "zidane.jpg"))
    sent: list[object] = []

    class Link:
        def send(self, message: object, *, binary: bytes | None = None) -> None:
            sent.append(message)

    pipeline = VisionPipeline(
        screen_id="demo-01",
        source=None,  # type: ignore[arg-type]  # process() is driven directly
        detector=detector,
        link=Link(),
        tracker=PersonTracker(),
        snapshots=SnapshotBuilder("demo-01", SnapshotConfig()),
    )
    from datetime import timedelta

    for i in range(10):  # the same photo for 1 s: tracks confirm, facts settle
        at = T0 + timedelta(milliseconds=100 * i)
        pipeline.process(image, detector.detect(image, at))
    last = sent[-1]
    assert getattr(last, "people", 0) >= 2
    assert getattr(last, "looking", 0) >= 1
