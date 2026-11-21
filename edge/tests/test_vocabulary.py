"""The YOLOE-26 object vocabulary and the child-prop safety vote (invariant 2: block only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.contract import AudienceSnapshot
from agent.vision.minor import (
    NO_READING,
    ChildPropConfig,
    ChildPropVote,
    MinorReading,
    strongest,
)
from agent.vision.pipeline import VisionPipeline
from agent.vision.snapshot import SnapshotBuilder, SnapshotConfig
from agent.vision.tracking import PersonTracker, TrackerConfig
from agent.vision.types import Box, FrameDetections, ObjectDetection, PersonDetection
from agent.vision.vocabulary import (
    CHILD_PROP_LABELS,
    OBJECT_LABELS,
    OBJECT_PROMPTS,
    OBJECT_VOCABULARY,
    baked_weights_path,
    canonical_label,
    vocabulary_fingerprint,
)

EDGE = Path(__file__).resolve().parents[1]
GPU_COPY = EDGE.parent / "gpu" / "service" / "vocabulary.py"
T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
W, H = 1280, 720
PERSON = PersonDetection(box=Box(400, 100, 700, 700), score=0.9)


# ------------------------------------------------------------- the vocabulary


def test_gpu_service_uses_the_same_vocabulary_file() -> None:
    if not GPU_COPY.exists():  # edge/ checked out alone
        pytest.skip("gpu/ is not next to edge/")
    assert GPU_COPY.read_text(encoding="utf-8") == (
        EDGE / "agent" / "vision" / "vocabulary.py"
    ).read_text(encoding="utf-8"), "copy edge/agent/vision/vocabulary.py to gpu/service/"


def test_prompts_are_unique_and_never_name_a_person() -> None:
    assert len(OBJECT_PROMPTS) == len(set(OBJECT_PROMPTS))
    forbidden = ("person", "man", "woman", "child", "kid", "boy", "girl", "face", "shirt")
    for prompt in OBJECT_PROMPTS:
        assert not set(prompt.split()) & set(forbidden), prompt


def test_labels_keep_the_catalog_coco_names_and_add_store_categories() -> None:
    assert {"cup", "bottle", "wine glass", "laptop", "cell phone", "sports ball"} <= OBJECT_LABELS
    assert {"energy drink can", "beer bottle", "pastry", "stroller"} <= OBJECT_LABELS
    assert CHILD_PROP_LABELS <= OBJECT_LABELS
    assert canonical_label("coffee cup") == "cup"
    assert canonical_label("smartphone") == "cell phone"
    assert canonical_label("bus") == "bus"  # outside the vocabulary: unchanged


def test_baked_weights_name_changes_with_the_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    before = baked_weights_path("/models/yoloe-26s-seg.pt")
    assert before.parent == Path("/models")
    assert before.name == f"yoloe-26s-seg-vocab-{vocabulary_fingerprint()}.pt"
    import agent.vision.vocabulary as vocabulary

    monkeypatch.setattr(vocabulary, "OBJECT_PROMPTS", (*OBJECT_PROMPTS, "umbrella"))
    assert baked_weights_path("/models/yoloe-26s-seg.pt") != before


def test_vocabulary_is_a_tuple_of_prompt_label_pairs() -> None:
    assert all(isinstance(p, str) and isinstance(label, str) for p, label in OBJECT_VOCABULARY)


# ------------------------------------------------------------- child-prop vote


def stroller(x: float = 650, score: float = 0.6) -> ObjectDetection:
    return ObjectDetection(label="stroller", score=score, box=Box(x, 450, x + 200, 700))


def test_a_stroller_next_to_a_person_flags_minor_after_the_vote() -> None:
    vote = ChildPropVote(CHILD_PROP_LABELS, ChildPropConfig(window=4, min_hits=2))
    assert vote.update([stroller()], [PERSON.box]) == NO_READING
    reading = vote.update([stroller()], [PERSON.box])
    assert reading.value is True
    assert 0 < reading.confidence < 1


@pytest.mark.parametrize(
    "objects",
    [
        [stroller(x=1100)],  # far from everyone: on the floor, not with the group
        [stroller(score=0.1)],  # too uncertain
        [ObjectDetection(label="cup", score=0.9, box=Box(650, 450, 700, 520))],  # not a child prop
    ],
)
def test_props_that_dont_count(objects: list[ObjectDetection]) -> None:
    vote = ChildPropVote(CHILD_PROP_LABELS, ChildPropConfig(window=4, min_hits=2))
    for _ in range(4):
        reading = vote.update(objects, [PERSON.box])
    assert reading == NO_READING


def test_without_people_a_prop_never_flags() -> None:
    vote = ChildPropVote(CHILD_PROP_LABELS, ChildPropConfig(window=4, min_hits=2))
    for _ in range(4):
        reading = vote.update([stroller()], [])
    assert reading == NO_READING


def test_strongest_prefers_any_block() -> None:
    clear = MinorReading(False, 0.8)
    block = MinorReading(True, 0.3)
    assert strongest(clear, block) == block
    assert strongest(clear, NO_READING) == clear
    assert strongest() == NO_READING


# ------------------------------------------------------------- through the snapshot


class Link:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def send(self, message: object, *, binary: bytes | None = None) -> None:
        self.sent.append(message)


def test_pipeline_publishes_new_store_labels_and_the_stroller_block() -> None:
    link = Link()
    pipeline = VisionPipeline(
        screen_id="demo-01",
        source=None,  # type: ignore[arg-type]  # process() only
        detector=None,  # type: ignore[arg-type]
        link=link,
        tracker=PersonTracker(TrackerConfig(min_hits=1)),
        snapshots=SnapshotBuilder("demo-01", SnapshotConfig()),
    )
    can = ObjectDetection(label="energy drink can", score=0.4, box=Box(600, 300, 650, 400))
    for i in range(30):  # 3 s: a person with a can, then a stroller rolls in
        objects = (can, stroller()) if i >= 10 else (can,)
        frame = FrameDetections(
            measured_at=T0 + timedelta(milliseconds=100 * i),
            width=W,
            height=H,
            persons=(PERSON,),
            objects=objects,
        )
        pipeline.process(object(), frame)
    snapshots = [m for m in link.sent if isinstance(m, AudienceSnapshot)]
    assert any("energy drink can" in {o.label for o in s.objects} for s in snapshots)
    before = [s for s in snapshots if s.measured_at < T0 + timedelta(seconds=1)]
    assert before and not any(s.minor_likely.value for s in before)
    assert snapshots[-1].minor_likely.value is True


# ------------------------------------------------------------- the counter probe


def test_probe_marks_what_the_agent_would_publish() -> None:
    from collections import deque

    from agent.vision.probe import render, summarize

    near_cup = ObjectDetection(label="cup", score=0.8, box=Box(500, 300, 560, 380))
    far_can = ObjectDetection(label="energy drink can", score=0.5, box=Box(1150, 600, 1200, 700))
    faint = ObjectDetection(label="stroller", score=0.1, box=Box(450, 400, 650, 700))
    frames: deque[FrameDetections] = deque(
        [
            FrameDetections(T0, W, H, persons=(PERSON,), objects=(near_cup, far_can, faint)),
            FrameDetections(T0, W, H, persons=(PERSON,), objects=(near_cup,)),
        ]
    )
    rows = summarize(frames)
    assert rows == [
        ("cup", 0.8, True, True),
        ("energy drink can", 0.5, True, False),
        ("stroller", 0.1, False, True),
    ]
    text = render(rows, people=1, ms=120, name="local")
    assert "PUBLISHED" in text and "no person near" in text and "below floor" in text
