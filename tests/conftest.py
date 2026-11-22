"""Shared test isolation."""

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _pinned_demo_catalog(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Tests pin the fictional catalog, whatever a developer's .env says.

    AppSettings reads `.env`, and a station set up for the real-brand demo carries
    DEMO_CATALOG=chile there. An env var outranks the file, so this keeps the pinned
    tests on the fictional seed; a test that wants Chile passes demo_catalog="chile".
    """
    monkeypatch.setenv("DEMO_CATALOG", "fictional")
    yield
