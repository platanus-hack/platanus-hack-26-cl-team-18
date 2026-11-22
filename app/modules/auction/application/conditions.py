"""The venue's latest conditions, in memory, for the hot path to read (< 1 µs).

A background refresh writes the live reading; an operator may pin an override
for the demo ("ola de calor"). The override wins until cleared. A live reading
older than `max_age` is ignored: stale weather is worse than none, because
"none" means every category bids flat, which is the safe default.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from threading import Lock

from app.modules.auction.application.ports.weather import WeatherSource, WeatherUnavailable
from app.modules.auction.domain.conditions import Weather

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE = timedelta(hours=3)


class WeatherOrigin(StrEnum):
    LIVE = "live"
    OVERRIDE = "override"
    NONE = "none"


@dataclass(frozen=True, slots=True, kw_only=True)
class CurrentWeather:
    weather: Weather | None
    origin: WeatherOrigin

    @property
    def tags(self) -> frozenset[str]:
        return frozenset(tag.value for tag in self.weather.tags) if self.weather else frozenset()


class ConditionsBoard:
    """Market-wide weather state shared by every screen runtime. Thread-safe."""

    def __init__(self, *, max_age: timedelta = DEFAULT_MAX_AGE) -> None:
        self._max_age = max_age
        self._live: Weather | None = None
        self._override: Weather | None = None
        self._lock = Lock()
        self._listeners: list[Callable[[], None]] = []

    def current(self, now: datetime) -> CurrentWeather:
        with self._lock:
            if self._override is not None:
                return CurrentWeather(weather=self._override, origin=WeatherOrigin.OVERRIDE)
            live = self._live
        if live is not None and now - live.observed_at <= self._max_age:
            return CurrentWeather(weather=live, origin=WeatherOrigin.LIVE)
        return CurrentWeather(weather=None, origin=WeatherOrigin.NONE)

    def record_live(self, weather: Weather) -> bool:
        """Store a provider reading. True when the tags the auction reads changed."""
        with self._lock:
            before = self._effective_tags()
            self._live = weather
            changed = before != self._effective_tags()
        if changed:
            self._notify()
        return changed

    def override(self, weather: Weather | None) -> bool:
        """Pin (or clear, with None) the operator's weather. True when anything changed."""
        with self._lock:
            changed = self._override != weather
            self._override = weather
        if changed:
            self._notify()
        return changed

    def on_change(self, listener: Callable[[], None]) -> None:
        """Called (outside the lock) when the auction's view of the weather changes."""
        self._listeners.append(listener)

    def _effective_tags(self) -> frozenset[str] | None:
        if self._override is not None:
            return None  # an override hides live changes: nothing to re-auction
        return frozenset(self._live.tags) if self._live is not None else frozenset()

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                logger.exception("conditions listener failed")


async def refresh_weather(board: ConditionsBoard, source: WeatherSource, *, every_s: float) -> None:
    """Warm loop: read the provider every `every_s`; a failure keeps the last reading."""
    while True:
        try:
            reading = await source.current()
            # Off the loop: a change re-auctions every screen (sync kernel, takes locks).
            await asyncio.to_thread(board.record_live, reading)
        except WeatherUnavailable as exc:
            logger.warning("weather unavailable: %s", exc)
        except Exception:
            logger.exception("weather refresh failed")
        await asyncio.sleep(every_s)
