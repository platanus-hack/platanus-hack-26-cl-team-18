import json

from evals.video import minors


def test_uncertainty_protects_a_child_window_without_claiming_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(minors, "VIDEO_OUT", tmp_path)
    folder = tmp_path / "cropped-child"
    folder.mkdir()
    (folder / "recording.agent.jsonl").write_text(
        json.dumps(
            {
                "t_s": 0,
                "snapshot": {
                    "people": 2,
                    "minor_likely": {"value": False, "confidence": 0, "uncertain": True},
                },
            }
        )
        + "\n"
    )
    # A live agent refreshes its facts every second; keep the protected sample fresh.
    path = folder / "recording.agent.jsonl"
    heartbeat = json.loads(path.read_text())
    heartbeat["t_s"] = 1
    with path.open("a") as stream:
        stream.write(json.dumps(heartbeat) + "\n")
    metrics, _ = minors.score({"cropped-child": [{"start_s": 0, "end_s": 2, "minor": True}]})
    assert metrics["minor_sample_recall"] == 0
    assert metrics["minor_sample_protection"] == 1
    assert metrics["minor_windows_unprotected"] == 0


def test_a_child_window_without_samples_or_recording_cannot_silently_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(minors, "VIDEO_OUT", tmp_path)
    folder = tmp_path / "empty"
    folder.mkdir()
    (folder / "recording.agent.jsonl").write_text("")
    metrics, _ = minors.score(
        {
            "empty": [{"start_s": 0, "end_s": 2, "minor": True}],
            "missing": [{"start_s": 0, "end_s": 2, "minor": True}],
        }
    )
    assert metrics["minor_recordings_missing"] == 1
    assert metrics["minor_windows_without_samples"] == 1
    assert metrics["minor_windows_unprotected"] == 1


def test_a_stale_protected_snapshot_cannot_cover_a_later_child_window(tmp_path, monkeypatch):
    monkeypatch.setattr(minors, "VIDEO_OUT", tmp_path)
    folder = tmp_path / "stale"
    folder.mkdir()
    (folder / "recording.agent.jsonl").write_text(
        json.dumps({"t_s": 0, "snapshot": {"people": 2, "minor_likely": {"value": True}}}) + "\n"
    )
    metrics, _ = minors.score({"stale": [{"start_s": 10, "end_s": 12, "minor": True}]})
    assert metrics["minor_sample_protection"] == 0
    assert metrics["minor_windows_without_samples"] == 1
    assert metrics["minor_samples_missing"] > 0
