"""Held swaps and lease renewals run on timer threads; the agent's clock may be off."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.contract.messages import AudienceSnapshot, MinorLikely
from app.realtime.input_activity import InputActivity
from app.realtime.scheduler import thread_timer

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def test_a_timer_callback_that_raises_is_logged_with_its_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A held release or lease renewal that raises must reach our logs, not just
    # threading.excepthook's bare stderr print, or nobody sees why an ad never aired.
    done = threading.Event()

    def release() -> None:
        try:
            raise RuntimeError("release failed")
        finally:
            done.set()

    hooked: list[object] = []
    previous = threading.excepthook
    threading.excepthook = hooked.append
    try:
        with caplog.at_level(logging.ERROR):
            thread_timer(0.0, release)
            assert done.wait(2)
            for _ in range(100):
                if caplog.records:
                    break
                threading.Event().wait(0.01)
    finally:
        threading.excepthook = previous
    assert any(r.getMessage() == "scheduled callback failed" for r in caplog.records)
    assert hooked == []


def snapshot(measured_at: datetime) -> AudienceSnapshot:
    return AudienceSnapshot(
        screen_id="demo-01",
        seq=1,
        measured_at=measured_at,
        people=1,
        looking=1,
        minor_likely=MinorLikely(value=False, confidence=0.9),
    )


def test_health_names_the_agent_clock_skew_that_silently_drops_snapshots() -> None:
    # Two laptops (ADR 0008): an agent clock 30 s behind makes the loop drop every
    # snapshot as stale. /health must say why the camera never counts as recent.
    activity = InputActivity()
    activity.observe(snapshot(T0 - timedelta(seconds=30)), T0)
    status = activity.snapshot(at=T0, speech_screens=[], players=[])["demo-01"]
    assert status["camera_recent"] is False
    assert status["camera_clock_skew_s"] == 30.0


def test_no_snapshot_means_no_skew_reading() -> None:
    status = InputActivity().snapshot(at=T0, speech_screens=[], players=["demo-01"])["demo-01"]
    assert status["camera_clock_skew_s"] is None
