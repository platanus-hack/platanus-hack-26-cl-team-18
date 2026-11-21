from __future__ import annotations

from app.config.logging_context import bind_context, clear_context, get_context, set_context


def test_set_get_and_clear_context_round_trip() -> None:
    clear_context()

    set_context(
        correlation_id="corr-1",
        organization_id=123,
        actor_type="user",
        ignored_key="ignored",
        request_id=None,
    )

    assert get_context() == {
        "correlation_id": "corr-1",
        "organization_id": "123",
        "actor_type": "user",
    }

    clear_context()

    assert get_context() == {}


def test_bind_context_restores_previous_values() -> None:
    clear_context()
    set_context(request_id="outer-request")

    with bind_context(request_id="inner-request", source="test"):
        assert get_context() == {
            "request_id": "inner-request",
            "source": "test",
        }

    assert get_context() == {
        "request_id": "outer-request",
    }

    clear_context()
