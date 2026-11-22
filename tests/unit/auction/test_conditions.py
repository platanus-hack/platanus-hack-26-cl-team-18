"""Time of day × weather → context multipliers (pure table) and the in-memory board."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.modules.auction.application.conditions import ConditionsBoard, WeatherOrigin
from app.modules.auction.application.ports.weather import WeatherUnavailable
from app.modules.auction.domain.conditions import (
    ContextRule,
    ContextTable,
    Weather,
    WeatherTag,
)
from app.modules.auction.infrastructure.open_meteo import OpenMeteoWeather, parse_current

T0 = datetime(2026, 11, 22, 18, tzinfo=UTC)


def weather(celsius: str, mm: str = "0", at: datetime = T0) -> Weather:
    return Weather(temperature_c=Decimal(celsius), precipitation_mm=Decimal(mm), observed_at=at)


@pytest.mark.parametrize(
    ("celsius", "mm", "tags"),
    [
        ("5", "0", {WeatherTag.COLD}),
        ("11.9", "0", {WeatherTag.COLD}),
        ("12", "0", {WeatherTag.MILD}),
        ("24", "0", {WeatherTag.WARM}),
        ("29", "0", {WeatherTag.HOT}),
        ("9", "1.5", {WeatherTag.COLD, WeatherTag.RAIN}),
        ("20", "0.1", {WeatherTag.MILD}),  # a drizzle under 0.2 mm is not "raining"
    ],
)
def test_weather_bands(celsius: str, mm: str, tags: set[WeatherTag]) -> None:
    assert weather(celsius, mm).tags == tags


TABLE = ContextTable(
    rules=(
        ContextRule(daypart="afternoon", category="ice_cream", multiplier=Decimal("1.5")),
        ContextRule(weather=WeatherTag.HOT, category="ice_cream", multiplier=Decimal("1.6")),
        ContextRule(weather=WeatherTag.HOT, category="coffee", multiplier=Decimal("0.8")),
        ContextRule(daypart="afternoon", category="coffee", multiplier=Decimal("1.25")),
        ContextRule(weather=WeatherTag.RAIN, category="coffee", multiplier=Decimal("4")),
        ContextRule(weather=WeatherTag.COLD, category="coffee", multiplier=Decimal("2")),
    )
)


def factors(daypart: str | None, reading: Weather | None) -> dict[str, tuple[str, tuple[str, ...]]]:
    return {
        f.category: (str(f.multiplier), f.reasons)
        for f in TABLE.multipliers(daypart=daypart, weather=reading)
    }


def test_daypart_and_weather_stack_with_reasons_daypart_first() -> None:
    assert factors("afternoon", weather("32")) == {
        "ice_cream": ("2.40", ("afternoon×1.5", "hot×1.6")),
        # 1.25 × 0.8 = 1: the two cancel out, so no factor is shown or applied
    }


def test_no_weather_reading_means_daypart_only() -> None:
    assert factors("afternoon", None) == {
        "ice_cream": ("1.50", ("afternoon×1.5",)),
        "coffee": ("1.25", ("afternoon×1.25",)),
    }


def test_the_product_is_clamped_to_the_auction_bounds() -> None:
    # cold × rain × afternoon = 2 × 4 × 1.25 = 10 → 5
    assert factors("afternoon", weather("8", "3"))["coffee"][0] == "5.00"


def test_rules_reject_ambiguous_or_out_of_range_rows() -> None:
    with pytest.raises(ValueError):
        ContextRule(category="coffee", multiplier=Decimal(1))
    with pytest.raises(ValueError):
        ContextRule(
            daypart="night", weather=WeatherTag.HOT, category="coffee", multiplier=Decimal(1)
        )
    with pytest.raises(ValueError):
        ContextRule(daypart="night", category="coffee", multiplier=Decimal("6"))
    with pytest.raises(ValueError):
        ContextTable(rules=(TABLE.rules[0], TABLE.rules[0]))


def test_board_ignores_stale_live_weather_and_override_wins() -> None:
    board = ConditionsBoard(max_age=timedelta(hours=3))
    changes: list[int] = []
    board.on_change(lambda: changes.append(1))
    assert board.current(T0).origin is WeatherOrigin.NONE

    assert board.record_live(weather("31", at=T0)) is True
    assert board.current(T0 + timedelta(hours=1)).origin is WeatherOrigin.LIVE
    assert board.current(T0 + timedelta(hours=4)).weather is None  # stale: flat bids

    # Same band, new reading: nothing for the auction to re-clear.
    assert board.record_live(weather("33", at=T0)) is False
    assert board.override(weather("8", "2")) is True
    assert board.current(T0).origin is WeatherOrigin.OVERRIDE
    assert board.current(T0).tags == {"cold", "rain"}
    assert board.override(None) is True
    assert board.current(T0).weather == weather("33", at=T0)
    assert len(changes) == 3


def test_open_meteo_payload_becomes_weather() -> None:
    reading = parse_current(
        {"current": {"time": "2026-11-22T18:00", "temperature_2m": 30.4, "precipitation": 0.0}}
    )
    assert reading == weather("30.4")
    with pytest.raises(WeatherUnavailable):
        parse_current({"current": {"time": "2026-11-22T18:00"}})


def test_open_meteo_sends_only_the_venue_coordinates() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "current": {"time": "2026-11-22T18:00", "temperature_2m": 12, "precipitation": 1}
            },
        )

    source = OpenMeteoWeather(
        latitude=-33.4489,
        longitude=-70.6693,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert asyncio.run(source.current()).tags == {WeatherTag.MILD, WeatherTag.RAIN}
    assert set(seen[0].url.params) == {"latitude", "longitude", "current", "timezone"}

    failing = OpenMeteoWeather(
        latitude=0,
        longitude=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))),
    )
    with pytest.raises(WeatherUnavailable):
        asyncio.run(failing.current())
