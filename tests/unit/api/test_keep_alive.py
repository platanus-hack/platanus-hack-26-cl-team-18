"""The free-plan keep-alive pings on its interval and survives failed pings."""

from __future__ import annotations

import asyncio

import pytest

from app.bootstrap.keep_alive import keep_alive


class _Stop(Exception):
    pass


def _run(pings: list[object], rounds: int) -> tuple[list[str], list[float]]:
    hits: list[str] = []
    slept: list[float] = []
    outcomes = iter(pings)

    async def sleep(seconds: float) -> None:
        if len(slept) == rounds:
            raise _Stop
        slept.append(seconds)

    async def get(url: str) -> int:
        hits.append(url)
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, int)
        return outcome

    with pytest.raises(_Stop):
        asyncio.run(keep_alive("https://svc/health", 600, get=get, sleep=sleep))
    return hits, slept


def test_pings_the_url_once_per_interval() -> None:
    hits, slept = _run([200, 200, 200], rounds=3)
    assert hits == ["https://svc/health"] * 3
    assert slept == [600, 600, 600]


def test_a_failed_or_bad_ping_does_not_stop_the_loop() -> None:
    hits, _ = _run([ConnectionError("down"), 503, 200], rounds=3)
    assert len(hits) == 3
