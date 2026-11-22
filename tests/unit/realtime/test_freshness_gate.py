"""The backlog a healed link flushes is dropped; a link whose normal lag moved is followed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.realtime.freshness import FreshnessGate

T0 = datetime(2026, 11, 21, 15, tzinfo=UTC)
S = ("audience.snapshot", "demo-01")


def ms(value: float) -> timedelta:
    return timedelta(milliseconds=value)


def test_a_partition_backlog_is_dropped_and_the_first_fresh_reading_passes() -> None:
    gate = FreshnessGate()
    for i in range(10):  # 5 Hz, 20 ms transit
        at = T0 + ms(200 * i)
        assert gate.admit(S, measured_at=at, received_at=at + ms(20))
    # 10 s partition: 50 readings sat in the kernel buffer, then arrive in 30 ms.
    healed = T0 + timedelta(seconds=12)
    backlog = [T0 + timedelta(seconds=2) + ms(200 * i) for i in range(50)]
    admitted = [
        gate.admit(S, measured_at=m, received_at=healed + ms(i * 0.6))
        for i, m in enumerate(backlog)
    ]
    # Only the tail (measured within 2 s of arrival) is fresh enough to decide on.
    assert not any(admitted[:-11]), admitted
    assert gate.admit(S, measured_at=healed + ms(40), received_at=healed + ms(60))


def test_normal_jitter_under_the_bound_is_never_dropped() -> None:
    gate = FreshnessGate()
    for i, transit in enumerate([10, 400, 900, 30, 1200, 5, 1990]):
        at = T0 + ms(200 * i)
        assert gate.admit(S, measured_at=at, received_at=at + ms(transit))
    assert gate.dropped == 0


def test_a_lag_that_stays_high_becomes_the_new_normal() -> None:
    # The remote agent's clock stepped 5 s back (or every path got slower): after a
    # few seconds of consistently late readings, the gate follows instead of
    # locking the camera out for good.
    gate = FreshnessGate()
    assert gate.admit(S, measured_at=T0, received_at=T0)
    results = []
    for i in range(1, 30):
        received = T0 + ms(200 * i)
        late = received - timedelta(seconds=5)
        results.append(gate.admit(S, measured_at=late, received_at=received))
    assert not results[0]
    assert all(results[-10:])


def test_streams_are_independent() -> None:
    gate = FreshnessGate()
    other = ("audience.snapshot", "demo-02")
    assert gate.admit(S, measured_at=T0, received_at=T0)
    assert gate.admit(other, measured_at=T0 - timedelta(seconds=4), received_at=T0)


def test_a_reconnects_first_reading_is_judged_against_the_previous_link(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Measured (evals/network_stress.py halfopen_agent_up_20s): the agent's ping timed
    # out, it reconnected through the same dead path, and when the path healed the new
    # socket's backlog led with a 5 s old reading. A per-socket floor trusted it.
    from fastapi.testclient import TestClient
    from scripts.replay_audience import snapshot_for

    from app.bootstrap.container import build_container
    from app.config.settings import AppSettings
    from app.main import create_app
    from app.modules.common.infrastructure.clock.fixed_clock import FixedClock

    clock = FixedClock(T0)
    settings = AppSettings(app_env="test", demo_simulator=True, media_dir=tmp_path)
    container = build_container(settings, clock=clock)
    with TestClient(create_app(container=container)) as client:
        with client.websocket_connect("/ws/agent") as agent:
            for seq in range(1, 4):
                now = clock.advance(ms(200))
                agent.send_text(snapshot_for("fitness", seq, now).model_dump_json())
            agent.send_text("{}")
            assert agent.receive_json()["type"] == "system.error"  # all three processed
        clock.advance(timedelta(seconds=5))
        with client.websocket_connect("/ws/agent") as agent:
            stale = clock.now_utc() - timedelta(seconds=5)
            agent.send_text(snapshot_for("adult", 1, stale).model_dump_json())
            agent.send_text("{}")
            assert agent.receive_json()["type"] == "system.error"
    assert container.freshness.dropped == 1


def test_one_reading_from_a_clock_ahead_never_makes_on_time_readings_late() -> None:
    # tests/unit/api/test_demo_replay.py: a device 9 s ahead, then back on time with a
    # minor in view. That on-time reading must reach the loop (the safety block).
    gate = FreshnessGate()
    assert gate.admit(S, measured_at=T0, received_at=T0)
    assert gate.admit(S, measured_at=T0 + timedelta(seconds=9.1), received_at=T0 + ms(100))
    assert gate.admit(S, measured_at=T0 + ms(200), received_at=T0 + ms(200))
