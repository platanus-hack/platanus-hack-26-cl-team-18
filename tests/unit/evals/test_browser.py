"""Browser glass-to-glass analysis (the live run needs Chromium: make eval-browser)."""

from evals.browser import BrowserRun, Moment, analyse


def moment(i: int, *, first: float | None, full: float | None, reported: float | None) -> Moment:
    m = Moment(index=i, scene="s", measured_ms=1_000.0)
    m.received_ms, m.ready_ms, m.ws_ms = 1_001.0, 1_003.0, 1_013.0
    m.first_paint_ms = None if first is None else 1_000.0 + first
    m.full_ms = None if full is None else 1_000.0 + full
    m.reported_ms = None if reported is None else 1_000.0 + reported
    return m


def test_glass_to_glass_stages_report_agreement_and_hard_cuts() -> None:
    run = BrowserRun(
        moments=[
            moment(0, first=40, full=265, reported=270),  # a real 225 ms crossfade
            moment(1, first=30, full=30, reported=280),  # popped in: a hard cut, misreported
            moment(2, first=None, full=None, reported=None),  # never reached the screen
        ],
        frames=600,
        blank_frames=0,
        duration_s=5,
    )
    result = analyse(run)
    metrics = result["metrics"]
    assert metrics["browser_glass_p50_ms"] == 147.5
    assert metrics["browser_glass_first_paint_p50_ms"] == 35
    assert metrics["browser_hard_cuts"] == 1
    assert metrics["browser_missed_swaps"] == 1
    assert metrics["browser_report_vs_probe_p95_ms"] > 200  # the misreport is caught
    assert result["stages"]["ready → creative.ready in the browser"]["p50"] == 10
