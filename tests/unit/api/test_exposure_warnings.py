"""A tunnel/LAN station with open device sockets warns (never fails), naming no secret."""

from __future__ import annotations

import pytest

from app.bootstrap.readiness import exposure_warnings
from app.config.settings import AppSettings

TUNNEL = "https://demo-station.trycloudflare.com"


def settings(**values: object) -> AppSettings:
    return AppSettings(_env_file=None, app_env="local", **values)  # type: ignore[arg-type]


def test_unset_tokens_on_a_tunnel_warn_by_name() -> None:
    (warning,) = exposure_warnings(settings(public_base_url=TUNNEL))
    assert "AGENT_TOKEN and SCREEN_TOKEN unset" in warning
    assert "demo-station.trycloudflare.com" in warning


def test_one_missing_token_is_named_alone_and_values_are_never_printed() -> None:
    secret = "agent-0123456789abcdef0123456789"
    (warning,) = exposure_warnings(settings(public_base_url=TUNNEL, agent_token=secret))
    assert "SCREEN_TOKEN unset" in warning and "AGENT_TOKEN" not in warning
    assert secret not in warning


@pytest.mark.parametrize(
    "url", ["http://localhost:5173", "http://127.0.0.1:5188", "http://station.localhost"]
)
def test_localhost_never_warns(url: str) -> None:
    assert exposure_warnings(settings(public_base_url=url)) == []


def test_both_tokens_set_or_prod_never_warn() -> None:
    both = settings(public_base_url=TUNNEL, agent_token="a" * 32, screen_token="s" * 32)
    assert exposure_warnings(both) == []
    prod = AppSettings(_env_file=None, app_env="prod", public_base_url=TUNNEL)  # type: ignore[call-arg]
    assert exposure_warnings(prod) == []


def test_the_station_url_overrides_the_configured_one() -> None:
    local = settings()  # default http://localhost:5173
    assert exposure_warnings(local) == []
    assert exposure_warnings(local, TUNNEL)
