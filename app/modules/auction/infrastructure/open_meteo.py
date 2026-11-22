"""Open-Meteo current weather (no API key) → `Weather` (anti-corruption layer).

Only the venue's coordinates leave the process: no screen id, no audience data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.modules.auction.application.ports.weather import WeatherUnavailable
from app.modules.auction.domain.conditions import Weather

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def parse_current(payload: Any) -> Weather:
    """The `current` block of an Open-Meteo forecast response."""
    try:
        current = payload["current"]
        observed = datetime.fromisoformat(current["time"])
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)  # timezone=UTC was requested
        return Weather(
            temperature_c=Decimal(str(current["temperature_2m"])),
            precipitation_mm=Decimal(str(current.get("precipitation") or 0)),
            observed_at=observed,
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise WeatherUnavailable(f"unexpected Open-Meteo payload: {type(exc).__name__}") from exc


class OpenMeteoWeather:
    def __init__(
        self,
        *,
        latitude: float,
        longitude: float,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self._params = {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "current": "temperature_2m,precipitation",
            "timezone": "UTC",
        }
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"User-Agent": "screen-weather/1"}
        )

    async def current(self) -> Weather:
        try:
            response = await self._client.get(OPEN_METEO_URL, params=self._params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise WeatherUnavailable(f"Open-Meteo request failed: {type(exc).__name__}") from exc
        return parse_current(response.json())

    async def aclose(self) -> None:
        await self._client.aclose()
