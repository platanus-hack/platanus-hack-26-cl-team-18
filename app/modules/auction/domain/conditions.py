"""Venue conditions (time of day × weather) → per-category context multipliers.

Context is a relevance signal, like speech intent: a hot afternoon makes the ice
cream ad worth more to the screen, so it ranks higher. It never raises what a
brand can be charged (`valuation.price_ceiling`): bids stay contractual maxima.

The table is pure data (one market's rules); the caller resolves the daypart from
the venue's local hour and reads the latest weather from memory. Nothing here
does IO, so the same conditions always produce the same multipliers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.modules.auction.domain.opportunity import ONE, CategoryMultiplier

MIN_MULTIPLIER = Decimal("0.2")
MAX_MULTIPLIER = Decimal(5)
_CENT = Decimal("0.01")
# °C thresholds for the temperature band (venue air temperature).
COLD_BELOW = Decimal(12)
WARM_FROM = Decimal(24)
HOT_FROM = Decimal(29)
# mm in the last hour that counts as "it's raining" for a shopper.
RAIN_FROM_MM = Decimal("0.2")


class WeatherTag(StrEnum):
    COLD = "cold"
    MILD = "mild"
    WARM = "warm"
    HOT = "hot"
    RAIN = "rain"


@dataclass(frozen=True, slots=True, kw_only=True)
class Weather:
    """One weather reading at the venue: air temperature and recent precipitation."""

    temperature_c: Decimal
    precipitation_mm: Decimal
    observed_at: datetime  # the provider's observation time (or the operator's override)

    def __post_init__(self) -> None:
        if not self.temperature_c.is_finite() or not -60 <= self.temperature_c <= 60:
            raise ValueError("Temperature must be within [-60, 60] °C")
        if not self.precipitation_mm.is_finite() or self.precipitation_mm < 0:
            raise ValueError("Precipitation must be a non-negative amount")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")

    @property
    def band(self) -> WeatherTag:
        if self.temperature_c < COLD_BELOW:
            return WeatherTag.COLD
        if self.temperature_c >= HOT_FROM:
            return WeatherTag.HOT
        if self.temperature_c >= WARM_FROM:
            return WeatherTag.WARM
        return WeatherTag.MILD

    @property
    def raining(self) -> bool:
        return self.precipitation_mm >= RAIN_FROM_MM

    @property
    def tags(self) -> frozenset[WeatherTag]:
        return frozenset({self.band, WeatherTag.RAIN} if self.raining else {self.band})


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextRule:
    """One row: when `daypart` and/or `weather` holds, `category` is worth × `multiplier`."""

    category: str
    multiplier: Decimal
    daypart: str | None = None
    weather: WeatherTag | None = None

    def __post_init__(self) -> None:
        if (self.daypart is None) == (self.weather is None):
            raise ValueError("A context rule keys on exactly one of daypart or weather")
        if not self.category:
            raise ValueError("A context rule names a category")
        if (
            not self.multiplier.is_finite()
            or not MIN_MULTIPLIER <= self.multiplier <= MAX_MULTIPLIER
        ):
            raise ValueError("Context rule multipliers must be within [0.2, 5]")

    @property
    def reason(self) -> str:
        return f"{self.daypart or self.weather}×{self.multiplier.normalize():f}"

    def applies(self, daypart: str | None, weather: frozenset[WeatherTag]) -> bool:
        if self.daypart is not None:
            return self.daypart == daypart
        return self.weather in weather


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextTable:
    """A market's context rules. Matching rules for a category multiply, then clamp."""

    rules: tuple[ContextRule, ...] = ()

    def __post_init__(self) -> None:
        keys = [(rule.daypart, rule.weather, rule.category) for rule in self.rules]
        if len(set(keys)) != len(keys):
            raise ValueError("Each (condition, category) pair appears once")

    def multipliers(
        self, *, daypart: str | None, weather: Weather | None
    ) -> tuple[CategoryMultiplier, ...]:
        """One factor per affected category, sorted by category (stable for the console)."""
        tags = weather.tags if weather is not None else frozenset()
        by_category: dict[str, list[ContextRule]] = {}
        for rule in self.rules:
            if rule.applies(daypart, tags):
                by_category.setdefault(rule.category, []).append(rule)
        factors = []
        for category in sorted(by_category):
            matched = by_category[category]
            product = ONE
            for rule in matched:
                product *= rule.multiplier
            clamped = min(max(product, MIN_MULTIPLIER), MAX_MULTIPLIER).quantize(_CENT)
            if clamped == ONE:
                continue  # e.g. afternoon ×0.8 and hot ×1.25 cancel out: no factor to show
            factors.append(
                CategoryMultiplier(
                    category=category,
                    multiplier=clamped,
                    # Daypart first, then weather: the order the console reads them.
                    reasons=tuple(
                        rule.reason for rule in sorted(matched, key=lambda r: r.daypart is None)
                    ),
                )
            )
        return tuple(factors)
