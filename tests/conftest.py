"""Shared test isolation."""

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _pinned_demo_catalog(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[None]:
    """Tests pin the fictional catalog, whatever a developer's .env says.

    AppSettings reads `.env`, and a station set up for the real-brand demo carries
    DEMO_CATALOG=chile there. An env var outranks the file, so this keeps the pinned
    tests on the fictional seed; a test that wants Chile passes demo_catalog="chile".
    """
    monkeypatch.setenv("DEMO_CATALOG", "fictional")
    for name in ("AGENT_TOKEN", "SCREEN_TOKEN", "CONSOLE_TOKEN"):
        monkeypatch.setenv(name, "")
    # `make station` now configures a real local Postgres. Unit/sandbox tests must
    # never inherit it from .env; explicit database settings in failure-injection
    # tests still take precedence. Integration tests opt into their real DSN.
    if request.node.get_closest_marker("integration") is None:
        monkeypatch.setenv("DATABASE_URL", "")
    yield
