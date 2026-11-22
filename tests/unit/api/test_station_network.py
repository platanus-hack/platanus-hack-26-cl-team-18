"""The station's web app must work from the second laptop and a judge's phone (ADR 0008 D3/D7)."""

from __future__ import annotations

from scripts.station import agent_geometry_args, exit_hint, public_origin, web_environment

ORIGIN = "http://127.0.0.1:5188"


def test_qr_origin_uses_the_lan_even_if_old_configuration_names_localhost() -> None:
    assert public_origin(None, port=5188, lan_host="192.168.1.20") == "http://192.168.1.20:5188"
    assert (
        public_origin("http://localhost:5173", port=5188, lan_host="192.168.1.20")
        == "http://192.168.1.20:5188"
    )
    assert (
        public_origin("https://demo.example.org", port=5188, lan_host=None)
        == "https://demo.example.org"
    )


def test_no_reachable_qr_origin_is_an_actionable_startup_error() -> None:
    import pytest

    with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
        public_origin(None, port=5188, lan_host=None)


def test_the_web_app_uses_its_own_origin_not_the_station_loopback() -> None:
    # VITE_BACKEND_URL is inlined into the page: 127.0.0.1 there sends the second
    # laptop's console and a judge's phone to their own machine.
    env = web_environment(
        {"VITE_BACKEND_URL": "http://elsewhere:8000", "A": "1"},
        public_base_url="https://abc.trycloudflare.com",
    )
    assert "VITE_BACKEND_URL" not in env and env["A"] == "1"


def test_the_tunnel_host_is_allowed_by_vite() -> None:
    env = web_environment({}, public_base_url="https://demo.example.org")
    hosts = env["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"].split(",")
    assert "demo.example.org" in hosts and ".trycloudflare.com" in hosts
    assert env["PUBLIC_BASE_URL"] == "https://demo.example.org"


def test_an_ip_qr_origin_needs_no_extra_allowed_host() -> None:
    env = web_environment({}, public_base_url="http://192.168.1.20:5188")
    assert env["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] == ".trycloudflare.com"


def test_a_crashed_component_shows_its_last_log_line(tmp_path) -> None:
    log = tmp_path / "camera.log"
    log.write_bytes(b"starting\nRuntimeError: camera 0 could not be opened\n\n")
    assert exit_hint(log) == "RuntimeError: camera 0 could not be opened"
    assert exit_hint(tmp_path / "missing.log") == ""


def test_the_station_tells_the_agent_its_lens_so_distance_bands_are_metres() -> None:
    # A wider venue webcam shows a 2 m judge smaller; without its FOV the agent would call
    # them "far" and bill their looks $0 (`make station ARGS="--camera-hfov 110"`).
    assert agent_geometry_args(camera_hfov=90.0) == ["--camera-hfov", "90"]
    assert agent_geometry_args(camera_hfov=110.5) == ["--camera-hfov", "110.5"]
