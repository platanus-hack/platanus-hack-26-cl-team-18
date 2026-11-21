"""Direct unit coverage for ``run_with_serialization_retry``.

Repositories that own a self-contained transaction wrap it in this helper. These tests pin the retry contract in
isolation, independent of the repository or the message bus's own copy of the
same idea (``test_message_bus_event_serialization_retry.py``): retry on
SerializationFailure/DeadlockDetected, re-raise once attempts are exhausted,
never retry a non-retryable exception, and back off with jitter between
attempts.
"""

from __future__ import annotations

import psycopg2.errors
import pytest

from app.modules.common.application import message_bus
from app.modules.common.application.serialization_retry import (
    DEFAULT_BACKOFF_MAX_SECONDS,
    DEFAULT_BACKOFF_MIN_SECONDS,
    DEFAULT_RETRY_ATTEMPTS,
    run_with_serialization_retry,
)


def test_succeeds_on_first_attempt_without_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "app.modules.common.application.serialization_retry.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )
    calls = {"count": 0}

    def fn() -> str:
        calls["count"] += 1
        return "ok"

    result = run_with_serialization_retry(fn, label="test.first_attempt")

    assert result == "ok"
    assert calls["count"] == 1
    assert sleep_calls == []


def test_retries_serialization_failure_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "app.modules.common.application.serialization_retry.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )
    calls = {"count": 0}

    def fn() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise psycopg2.errors.SerializationFailure("simulated 40001")
        return "ok"

    result = run_with_serialization_retry(
        fn,
        label="test.retry_then_succeed",
        attempts=5,
        backoff_min_seconds=0.001,
        backoff_max_seconds=0.002,
    )

    assert result == "ok"
    assert calls["count"] == 3
    # Backoff only happens between attempts, never after the final success.
    assert len(sleep_calls) == 2
    for backoff in sleep_calls:
        assert 0.001 <= backoff <= 0.002


def test_retries_deadlock_detected_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.common.application.serialization_retry.time.sleep",
        lambda seconds: None,
    )
    calls = {"count": 0}

    def fn() -> str:
        calls["count"] += 1
        if calls["count"] < 2:
            raise psycopg2.errors.DeadlockDetected("simulated 40P01")
        return "ok"

    result = run_with_serialization_retry(fn, label="test.deadlock", attempts=3)

    assert result == "ok"
    assert calls["count"] == 2


def test_reraises_original_exception_after_exhausting_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.common.application.serialization_retry.time.sleep",
        lambda seconds: None,
    )
    calls = {"count": 0}

    def fn() -> str:
        calls["count"] += 1
        raise psycopg2.errors.SerializationFailure("persistent 40001")

    with pytest.raises(psycopg2.errors.SerializationFailure):
        run_with_serialization_retry(fn, label="test.exhausted", attempts=3)

    assert calls["count"] == 3


def test_non_retryable_exception_propagates_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "app.modules.common.application.serialization_retry.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )
    calls = {"count": 0}

    def fn() -> str:
        calls["count"] += 1
        raise ValueError("not a concurrency error")

    with pytest.raises(ValueError, match="not a concurrency error"):
        run_with_serialization_retry(fn, label="test.non_retryable", attempts=5)

    assert calls["count"] == 1
    assert sleep_calls == []


def test_backoff_jitter_stays_within_configured_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "app.modules.common.application.serialization_retry.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )
    calls = {"count": 0}

    def fn() -> str:
        calls["count"] += 1
        if calls["count"] < 4:
            raise psycopg2.errors.SerializationFailure("simulated 40001")
        return "ok"

    run_with_serialization_retry(
        fn,
        label="test.jitter_bounds",
        attempts=5,
        backoff_min_seconds=0.010,
        backoff_max_seconds=0.050,
    )

    assert len(sleep_calls) == 3
    for backoff in sleep_calls:
        assert 0.010 <= backoff <= 0.050


def test_attempts_is_clamped_to_at_least_one() -> None:
    calls = {"count": 0}

    def fn() -> str:
        calls["count"] += 1
        raise psycopg2.errors.SerializationFailure("persistent 40001")

    with pytest.raises(psycopg2.errors.SerializationFailure):
        run_with_serialization_retry(fn, label="test.zero_attempts", attempts=0)

    assert calls["count"] == 1


def test_backoff_ceiling_doubles_per_attempt_up_to_the_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Jitter is drawn from [min, min * 2**(attempt-1)] capped at max."""
    ceilings: list[float] = []
    monkeypatch.setattr(
        "app.modules.common.application.serialization_retry.random.uniform",
        lambda low, high: ceilings.append(high) or high,
    )
    monkeypatch.setattr(
        "app.modules.common.application.serialization_retry.time.sleep",
        lambda seconds: None,
    )

    def fn() -> str:
        raise psycopg2.errors.SerializationFailure("persistent 40001")

    with pytest.raises(psycopg2.errors.SerializationFailure):
        run_with_serialization_retry(
            fn,
            label="test.exponential",
            attempts=6,
            backoff_min_seconds=0.025,
            backoff_max_seconds=0.250,
        )

    assert ceilings == pytest.approx([0.025, 0.050, 0.100, 0.200, 0.250])


def test_default_budget_is_shared_with_the_message_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standalone transaction runners and the bus exhaust after the same budget."""
    monkeypatch.setattr(
        "app.modules.common.application.serialization_retry.time.sleep",
        lambda seconds: None,
    )
    calls = {"count": 0}

    def fn() -> str:
        calls["count"] += 1
        raise psycopg2.errors.SerializationFailure("persistent 40001")

    with pytest.raises(psycopg2.errors.SerializationFailure):
        run_with_serialization_retry(fn, label="test.default_budget")

    assert calls["count"] == DEFAULT_RETRY_ATTEMPTS == message_bus._DEFAULT_RETRY_ATTEMPTS
    assert message_bus._DEFAULT_BACKOFF_MIN_SECONDS == DEFAULT_BACKOFF_MIN_SECONDS
    assert message_bus._DEFAULT_BACKOFF_MAX_SECONDS == DEFAULT_BACKOFF_MAX_SECONDS
