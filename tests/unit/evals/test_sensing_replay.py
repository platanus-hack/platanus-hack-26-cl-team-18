"""Recorded device timestamps must be shifted together when replayed live."""

import json
from datetime import UTC, datetime, timedelta

from evals.harness import replay_recording
from scripts.replay_audience import snapshot_for


def test_replay_preserves_sensing_duration_without_reusing_historical_instants(tmp_path):
    measured = datetime(2026, 11, 21, tzinfo=UTC)
    snapshot = snapshot_for("fitness", 1, measured).model_copy(
        update={
            "candidate_at": measured - timedelta(milliseconds=600),
            "detected_at": measured + timedelta(milliseconds=40),
            "published_at": measured + timedelta(milliseconds=41),
        }
    )
    path = tmp_path / "sensing.jsonl"
    path.write_text(json.dumps({"t_s": 0, "snapshot": snapshot.model_dump(mode="json")}) + "\n")
    result = replay_recording(path)
    trace = result.decisions[0].creative.trace
    assert trace.measured_at - trace.candidate_at == timedelta(milliseconds=600)
    assert trace.detected_at - trace.measured_at == timedelta(milliseconds=40)
    assert trace.published_at - trace.measured_at == timedelta(milliseconds=41)
    assert trace.measured_at != measured
