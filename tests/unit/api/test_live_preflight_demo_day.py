"""The preflight names what breaks a demo before the judges see it."""

from __future__ import annotations

from scripts.live_preflight import (
    DEMO_MIN_REMAINING_CLP,
    budget_failures,
    health_failures,
    live_input_failures,
)


def _line(name: str, remaining: str) -> dict[str, object]:
    return {"brand_name": name, "remaining": {"amount": remaining, "currency": "CLP"}}


def test_a_campaign_a_rehearsal_drained_is_named_before_the_demo() -> None:
    # Budgets persist across restarts (Postgres): a drained brand silently stops bidding.
    book = {"lines": [_line("Voltio", "120.000000"), _line("Café Patio", "50000")]}
    failures = budget_failures(book)
    assert len(failures) == 1 and "Voltio" in failures[0] and "top-up" in failures[0]
    assert budget_failures({"lines": [_line("Rayo", str(DEMO_MIN_REMAINING_CLP))]}) == []


def test_a_skewed_agent_clock_is_a_failure_before_snapshots_are_dropped() -> None:
    report = {
        "inputs": {"demo-01": {"camera_recent": True, "camera_clock_skew_s": -3.2}},
        "screens_connected": ["demo-01"],
    }
    failures = live_input_failures(report, "demo-01")
    assert failures and "clock" in failures[0]
    report["inputs"]["demo-01"]["camera_clock_skew_s"] = 0.1
    assert live_input_failures(report, "demo-01") == []


def test_records_in_memory_despite_a_database_and_missing_photos_are_reported() -> None:
    report = {
        "records": {"campaigns": "memory_fallback", "writer": {"dropped": 0}},
        "ads": {"media": {"missing_campaign_photos": ["cl-agua"]}},
    }
    failures = health_failures(report)
    assert any("Postgres" in f for f in failures)
    assert any("cl-agua" in f for f in failures)
    assert health_failures({"records": {"campaigns": "postgres", "writer": {"dropped": 0}}}) == []


def test_dropped_record_rows_are_reported() -> None:
    report = {"records": {"campaigns": "postgres", "writer": {"dropped": 12}}}
    assert any("12" in f for f in health_failures(report))
