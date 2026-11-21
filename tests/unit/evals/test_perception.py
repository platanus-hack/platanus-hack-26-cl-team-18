import json
from datetime import UTC, datetime

from evals.perception import LabelWindow, Sample, load_labels, score_clip

from app.contract.messages import AudienceSnapshot, DetectedObject, MinorLikely

T0 = datetime(2026, 11, 21, tzinfo=UTC)


def sample(t: float, people: int, objects: tuple[str, ...] = (), minor: bool = False) -> Sample:
    return Sample(
        t_s=t,
        snapshot=AudienceSnapshot(
            screen_id="demo-01",
            seq=int(t * 10) + 1,
            measured_at=T0,
            people=people,
            looking=people,
            objects=[DetectedObject(label=o, score=0.9) for o in objects],
            minor_likely=MinorLikely(value=minor, confidence=0.8),
        ),
    )


WINDOWS = (
    LabelWindow(start_s=0, end_s=2, people=0, looking=0, objects=frozenset(), minor=False),
    LabelWindow(start_s=2, end_s=5, people=2, looking=2, objects=frozenset({"cup"}), minor=False),
    LabelWindow(start_s=5, end_s=8, people=1, looking=1, objects=frozenset(), minor=True),
)


def test_scores_counts_detection_delay_objects_and_minor_safety() -> None:
    samples = [sample(t / 2, 0) for t in range(4)]  # 0.0..1.5
    samples += [
        sample(2.0, 0),
        sample(2.5, 2, ("cup",)),
        sample(3.0, 2, ("cup",)),
        sample(4.0, 3, ("cup",)),
    ]
    samples += [sample(5.5, 1), sample(6.0, 1), sample(7.0, 1)]  # minor never flagged
    scores = score_clip(WINDOWS, samples)
    assert scores["change_detect_p50_ms"] == 500.0  # both changes seen 0.5 s after they happen
    assert scores["object_recall"] == 1.0
    assert scores["minor_windows"] == 1
    assert scores["minor_windows_missed"] == 1  # the safety gate must catch this
    assert scores["people_mae"] is not None and scores["people_mae"] > 0
    assert scores["changes_missed"] == 0


def test_labels_reject_overlapping_windows(tmp_path) -> None:
    path = tmp_path / "clip.json"
    path.write_text(
        json.dumps(
            {
                "windows": [
                    {"start_s": 0, "end_s": 3, "people": 1},
                    {"start_s": 2, "end_s": 4, "people": 0},
                ]
            }
        )
    )
    try:
        load_labels(path)
    except ValueError as exc:
        assert "overlap" in str(exc)
    else:
        raise AssertionError("overlapping windows must be rejected")
