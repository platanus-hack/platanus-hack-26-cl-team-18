"""A tunnel/LAN station with open sockets or switches warns (never fails), naming no secret."""

from __future__ import annotations

import pytest

from app.bootstrap.readiness import exposure_warnings
from app.config.settings import AppSettings

TUNNEL = "https://demo-station.trycloudflare.com"
STRONG = "s" * 32


def settings(**values: object) -> AppSettings:
    return AppSettings(_env_file=None, app_env="local", **values)  # type: ignore[arg-type]


def closed(**values: object) -> AppSettings:
    """A station with every token and the demo key set."""
    return settings(
        **{
            "agent_token": "a" * 32,
            "screen_token": STRONG,
            "console_token": "c" * 32,
            "demo_key": "d" * 32,
            **values,
        }
    )


def test_unset_tokens_on_a_tunnel_warn_by_name() -> None:
    warnings = exposure_warnings(settings(public_base_url=TUNNEL))
    assert any("AGENT_TOKEN and SCREEN_TOKEN unset" in warning for warning in warnings)
    assert all("demo-station.trycloudflare.com" in warning for warning in warnings)


def test_one_missing_token_is_named_alone_and_values_are_never_printed() -> None:
    secret = "agent-0123456789abcdef0123456789"
    (warning,) = exposure_warnings(
        closed(public_base_url=TUNNEL, agent_token=None, demo_key=secret)
    )
    assert "AGENT_TOKEN unset" in warning and "SCREEN_TOKEN" not in warning
    assert secret not in warning


def test_an_open_console_and_live_switch_on_a_tunnel_warn() -> None:
    (console,) = exposure_warnings(closed(public_base_url=TUNNEL, console_token=None))
    assert "CONSOLE_TOKEN unset" in console
    (switch,) = exposure_warnings(closed(public_base_url=TUNNEL, demo_key=None))
    assert "DEMO_KEY unset" in switch and "/api/live" in switch


def test_a_web_host_open_to_the_lan_warns_even_on_a_localhost_url() -> None:
    station = settings(demo_key="d" * 32)  # the real station: DEMO_KEY only
    assert exposure_warnings(station, "http://127.0.0.1:5188") == []
    warnings = exposure_warnings(station, "http://127.0.0.1:5188", web_host="0.0.0.0")
    assert any("CONSOLE_TOKEN unset while the web host is 0.0.0.0" in w for w in warnings)
    assert exposure_warnings(station, "http://127.0.0.1:5188", web_host="127.0.0.1") == []


@pytest.mark.parametrize(
    "url", ["http://localhost:5173", "http://127.0.0.1:5188", "http://station.localhost"]
)
def test_localhost_never_warns(url: str) -> None:
    assert exposure_warnings(settings(public_base_url=url)) == []


def test_everything_set_never_warns() -> None:
    assert exposure_warnings(closed(public_base_url=TUNNEL)) == []


def test_prod_warns_only_about_an_unset_console_token() -> None:
    prod = AppSettings(_env_file=None, app_env="prod", public_base_url=TUNNEL)  # type: ignore[call-arg]
    (warning,) = exposure_warnings(prod)
    assert "CONSOLE_TOKEN unset in prod" in warning
    secured = AppSettings(_env_file=None, app_env="prod", console_token="c" * 32)  # type: ignore[call-arg]
    assert exposure_warnings(secured) == []


def test_the_render_placeholder_counts_as_unset() -> None:
    placeholder = "set-in-render-dashboard"
    prod = AppSettings(_env_file=None, app_env="prod", console_token=placeholder)  # type: ignore[call-arg]
    assert prod.console_token is None
    assert exposure_warnings(prod)


def test_the_station_url_overrides_the_configured_one() -> None:
    local = settings()  # default http://localhost:5173
    assert exposure_warnings(local) == []
    assert exposure_warnings(local, TUNNEL)
