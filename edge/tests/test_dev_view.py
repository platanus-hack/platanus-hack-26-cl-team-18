"""`dev.perception`: normalized, rate-limited, anonymous geometry for the /dev console."""

from __future__ import annotations

import argparse
from datetime import timedelta

import pytest

from agent.contract import AGENT_INBOUND, DevPerception
from agent.vision.__main__ import camera_preview_on, dev_stream_on
from agent.vision.dev_view import DevViewBuilder
from agent.vision.snapshot import SnapshotBuilder
from agent.vision.types import Box, MarkerDetection, ObjectDetection
from tests.test_vision import NO_DEBOUNCE, RecordingLink, _pipeline, frame, ms, person


def _views(link: RecordingLink) -> list[DevPerception]:
    return [m for m, _ in link.sent if isinstance(m, DevPerception)]


def test_pipeline_sends_normalized_boxes_at_most_five_times_a_second() -> None:
    link = RecordingLink()
    pipeline = _pipeline(
        link,
        snapshots=SnapshotBuilder("demo-01", NO_DEBOUNCE),
        dev_view=DevViewBuilder("demo-01", brand_for={7: "cafe-patio"}.get),
    )
    cup = ObjectDetection(label="cup", score=0.8, box=Box(500, 400, 560, 470))
    shelf = ObjectDetection(label="bottle", score=0.7, box=Box(1200, 10, 1270, 90))
    marker = MarkerDetection(marker_id=7, box=Box(450, 300, 480, 330))
    for i in range(10):  # 10 frames over 450 ms
        pipeline.process(
            None, frame(ms(i * 50), person(400), objects=(cup, shelf), markers=(marker,))
        )

    views = _views(link)
    assert [v.seq for v in views] == [0, 1, 2]  # 0, 200 and 400 ms
    view = views[0]
    assert (view.frame_w, view.frame_h, view.raw_people) == (1280, 720, 1)
    (someone,) = view.people
    assert someone.track_id and someone.looking
    assert someone.box.x1 == pytest.approx(400 / 1280) and someone.box.y2 == pytest.approx(
        700 / 720
    )
    near = {obj.label: obj.near_person for obj in view.objects}
    assert near == {"cup": True, "bottle": False}
    assert [(m.marker_id, m.brand_ref) for m in view.markers] == [(7, "cafe-patio")]
    # It round-trips the backend's inbound contract.
    assert AGENT_INBOUND.validate_json(view.model_dump_json()) == view


def test_without_a_dev_view_nothing_extra_is_sent() -> None:
    link = RecordingLink()
    pipeline = _pipeline(link, snapshots=SnapshotBuilder("demo-01", NO_DEBOUNCE))
    pipeline.process(None, frame(ms(0), person(400)))
    assert _views(link) == []


def test_out_of_frame_boxes_are_clamped() -> None:
    builder = DevViewBuilder("demo-01")
    wild = ObjectDetection(label="cup", score=1.0, box=Box(-50, -20, 1400, 900))
    view = builder.build(frame(ms(0), objects=(wild,)), [], [])
    assert view is not None
    box = view.objects[0].box
    assert (box.x1, box.y1, box.x2, box.y2) == (0.0, 0.0, 1.0, 1.0)
    assert builder.build(frame(ms(100)), [], []) is None
    builder.reset()
    assert builder.build(frame(ms(100) + timedelta(milliseconds=1)), [], []) is not None


@pytest.mark.parametrize(
    ("flag", "env", "offline", "record", "on"),
    [
        (True, "", False, None, True),
        (False, "1", False, None, True),
        (False, "", False, None, False),
        (True, "", True, None, False),
        (True, "1", False, "out.jsonl", False),
    ],
)
def test_dev_stream_only_for_live_backends(
    monkeypatch: pytest.MonkeyPatch,
    flag: bool,
    env: str,
    offline: bool,
    record: str | None,
    on: bool,
) -> None:
    monkeypatch.setenv("EDGE_DEV_STREAM", env)
    args = argparse.Namespace(dev_stream=flag, offline=offline, record=record)
    assert dev_stream_on(args) is on


def test_camera_preview_rides_along_only_with_an_encoder() -> None:
    seen: list[object] = []

    def encode(img: object, _detections: object) -> bytes:
        seen.append(img)
        return b"\xff\xd8jpeg"

    with_preview = DevViewBuilder("demo-01", preview=encode)
    view = with_preview.build(frame(ms(0), person(400)), [], [], frame="the-frame")
    assert view is not None and view.preview_jpeg_base64 == "/9hqcGVn"
    assert seen == ["the-frame"]
    assert AGENT_INBOUND.validate_json(view.model_dump_json()) == view

    plain = DevViewBuilder("demo-01").build(frame(ms(0)), [], [], frame="the-frame")
    assert plain is not None and plain.preview_jpeg_base64 is None


def test_a_failing_preview_still_sends_the_boxes() -> None:
    def broken(_img: object, _detections: object) -> bytes:
        raise RuntimeError("encoder down")

    view = DevViewBuilder("demo-01", preview=broken).build(frame(ms(0)), [], [], frame=object())
    assert view is not None and view.preview_jpeg_base64 is None


@pytest.mark.parametrize(
    ("flag", "env", "dev", "on"),
    [
        (True, "", "1", True),
        (False, "1", "1", True),
        (False, "", "1", False),
        (True, "1", "", False),
    ],
)
def test_camera_preview_needs_the_dev_stream(
    monkeypatch: pytest.MonkeyPatch, flag: bool, env: str, dev: str, on: bool
) -> None:
    monkeypatch.setenv("EDGE_CAMERA_PREVIEW", env)
    monkeypatch.setenv("EDGE_DEV_STREAM", dev)
    args = argparse.Namespace(camera_preview=flag, dev_stream=False, offline=False, record=None)
    assert camera_preview_on(args) is on
