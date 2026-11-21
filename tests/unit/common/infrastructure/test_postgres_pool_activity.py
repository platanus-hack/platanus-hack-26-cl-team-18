import logging

from app.modules.common.infrastructure.postgres.pool_activity import PoolActivity


# bounded pool-operation attribution is separate from commit latency.
def test_pool_activity_reports_bounded_windows_with_role_and_no_query_data(caplog) -> None:
    now = [0.0]
    activity = PoolActivity(role="warm_worker", monotonic=lambda: now[0])
    caplog.set_level(logging.INFO)
    for _ in range(120):
        activity.record("checkout")
        activity.record("reset")
    assert not caplog.records
    now[0] = 60.0
    activity.record("checkout")
    record = caplog.records[-1]
    assert record.getMessage() == "postgres_pool_activity"
    assert record.pool_role == "warm_worker"
    assert record.interval_seconds == 60
    assert record.operations == {"checkout": 121, "reset": 120}
    now[0] = 120.0
    activity.record("discard")
    assert caplog.records[-1].operations == {"discard": 1}
    assert len(caplog.records) == 2


def test_call_timings_share_the_bounded_window_and_reset(caplog):
    now = [0.0]
    activity = PoolActivity(role="api", monotonic=lambda: now[0])
    caplog.set_level(logging.INFO)
    for _ in range(100):
        activity.record_call("commit", elapsed=0.002, failed=False)
    activity.record_call("commit", elapsed=0.010, failed=True)
    assert not caplog.records
    now[0] = 60.0
    activity.record("reset")
    calls = caplog.records[-1].calls
    assert calls == {"commit": {"count": 101, "failed": 1, "total_ms": 210, "max_ms": 10}}
    now[0] = 120.0
    activity.record("checkout")
    assert caplog.records[-1].calls == {}
    assert calls["commit"]["count"] == 101  # previous emitted snapshot stays fixed


def test_connection_occupancy_survives_reporting_windows_and_discards(caplog):
    now = [0.0]
    activity = PoolActivity(role="auction", monotonic=lambda: now[0])
    caplog.set_level(logging.INFO)
    activity.acquired(101, waited_seconds=0.025)
    now[0] = 60.0
    activity.acquired(102, waited_seconds=0.001)
    first = caplog.records[-1]
    assert first.pool_occupancy == {"checked_out": 2, "high_water": 2}
    now[0] = 60.5
    activity.returned(101)
    activity.returned(101)  # a discard after failed cleanup must not count twice
    now[0] = 120.0
    activity.returned(102)
    second = caplog.records[-1]
    assert second.pool_occupancy == {"checked_out": 0, "high_water": 2}
    assert second.calls["connection_hold"]["count"] == 2
    assert second.calls["connection_hold"]["max_ms"] == 60500
    assert first.pool_occupancy["checked_out"] == 2
    assert "101" not in str(second.__dict__)  # no connection IDs in telemetry


def test_wait_and_hold_histograms_are_bounded_and_use_milliseconds(caplog):
    now = [0.0]
    activity = PoolActivity(role="api", monotonic=lambda: now[0])
    caplog.set_level(logging.INFO)
    for identifier in range(10000):
        activity.acquired(identifier, waited_seconds=0.1)
        activity.returned(identifier)
    now[0] = 60.0
    activity.record("reset")
    emitted = caplog.records[-1]
    assert emitted.calls["pool_wait"]["count"] == 10000
    assert emitted.pool_timing_buckets["pool_wait"] == {"100": 10000}
    assert emitted.pool_timing_buckets["connection_hold"] == {"1": 10000}
    assert emitted.pool_occupancy == {"checked_out": 0, "high_water": 1}
