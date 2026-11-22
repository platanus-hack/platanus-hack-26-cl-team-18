"""Where the venue's weather comes from (warm path only; the hot path reads memory)."""

from __future__ import annotations

from typing import Protocol

from app.modules.auction.domain.conditions import Weather


class WeatherUnavailable(Exception):
    """The provider could not give a usable reading; the last good one stays in use."""


class WeatherSource(Protocol):
    async def current(self) -> Weather: ...
