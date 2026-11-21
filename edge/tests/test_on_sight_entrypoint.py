"""Backend mode uses the live pipeline rather than inventing attention from debug tracks."""

import sys
from unittest.mock import AsyncMock

from agent.vision import __main__ as live
from agent.vision import replay


def test_on_sight_backend_selects_pose_pipeline_without_loading_debug_model(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["on-sight", "clip.mp4", "--backend", "ws://localhost/ws/agent", "--screen-id", "north"],
    )
    run = AsyncMock(return_value=0)
    monkeypatch.setattr(live, "run", run)

    def no_debug_model(*args):
        raise AssertionError("Backend mode must not construct the detection-only debug model")

    monkeypatch.setattr(replay, "LocalDetector", no_debug_model)
    replay.main()
    [args] = run.call_args.args
    assert args.screen == "north"
    assert args.video == "clip.mp4"
    assert args.pose_model == "yolo26n-pose.pt"
    assert args.object_model == "yolo26s.pt"
