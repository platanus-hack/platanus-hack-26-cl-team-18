"""The "En vivo" switch under a failing listener and a double click."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from app.contract.messages import LiveSet, LiveStatus
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from app.realtime.live_switch import LiveSwitchboard

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)


def board() -> LiveSwitchboard:
    return LiveSwitchboard(clock=FixedClock(T0), starts_live=True, speech_screens=("demo-01",))


def test_a_failing_listener_neither_fails_the_switch_nor_skips_the_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    switch = board()
    seen: list[LiveStatus] = []

    def standby_crashes(_before: LiveStatus, _after: LiveStatus) -> None:
        raise RuntimeError("standby blew up")

    switch.on_change(standby_crashes)
    switch.on_change(lambda _before, after: seen.append(after))

    status = switch.set(LiveSet(screen_id="demo-01", live=False))

    assert not status.live and not switch.sensing("demo-01")
    assert [s.revision for s in seen] == [1]  # the announcement still went out
    assert "live switch listener failed" in caplog.text


def test_concurrent_switches_notify_in_revision_order() -> None:
    # A double click: two POSTs in two worker threads. Agents apply pushes in
    # arrival order (no revision check), so notifications must not interleave.
    switch = board()
    seen: list[int] = []
    first_entered, release_first = threading.Event(), threading.Event()

    def listener(_before: LiveStatus, after: LiveStatus) -> None:
        if after.revision == 1:
            first_entered.set()
            assert release_first.wait(2)
        seen.append(after.revision)

    switch.on_change(listener)
    off = threading.Thread(target=switch.set, args=(LiveSet(screen_id="demo-01", live=False),))
    off.start()
    assert first_entered.wait(2)
    on = threading.Thread(target=switch.set, args=(LiveSet(screen_id="demo-01", live=True),))
    on.start()
    on.join(0.2)
    release_first.set()
    off.join(2)
    on.join(2)
    assert seen == [1, 2]
    assert switch.status("demo-01").live and switch.status("demo-01").revision == 2
