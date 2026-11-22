"""Who may flip "En vivo" (app/api/live.py): this machine or the demo key, never the tunnel."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.main import create_app

KEY = "demo-key-for-tests"
STANDBY = {"screen_id": "demo-01", "live": False}
# What cloudflared adds to every request it forwards to the station.
TUNNEL = {"cf-connecting-ip": "203.0.113.9", "cf-ray": "8f0c-SCL", "x-forwarded-for": "203.0.113.9"}


def client(tmp_path: Path, *, host: str = "127.0.0.1", **settings: object) -> TestClient:
    values = {
        "_env_file": None,
        "app_env": "local",
        "media_dir": tmp_path,
        "live_ads_enabled": False,
        **settings,
    }
    container = build_container(AppSettings(**values))  # type: ignore[arg-type]
    return TestClient(create_app(container=container), client=(host, 50_000))


def test_the_station_console_on_this_machine_needs_no_key(tmp_path: Path) -> None:
    # Station laptop or the second laptop over the LAN: the Vite proxy -> 127.0.0.1.
    with client(tmp_path, demo_key=KEY) as c:
        assert c.post("/api/live", json=STANDBY).status_code == 200
    with client(tmp_path, demo_key=KEY, host="::1") as c:
        assert c.post("/api/live", json=STANDBY).status_code == 200


def test_through_the_tunnel_the_switch_needs_the_demo_key(tmp_path: Path) -> None:
    with client(tmp_path, demo_key=KEY) as c:
        assert c.post("/api/live", json=STANDBY, headers=TUNNEL).status_code == 403
        wrong = {**TUNNEL, "X-Demo-Key": "nope"}
        assert c.post("/api/live", json=STANDBY, headers=wrong).status_code == 403
        keyed = {**TUNNEL, "X-Demo-Key": KEY}
        assert c.post("/api/live", json=STANDBY, headers=keyed).status_code == 200
        assert c.get("/api/live", headers=TUNNEL).status_code == 200  # reading stays open


def test_a_remote_client_needs_the_key(tmp_path: Path) -> None:
    with client(tmp_path, demo_key=KEY, host="192.0.2.7") as c:
        assert c.post("/api/live", json=STANDBY).status_code == 403
        assert c.post("/api/live", json=STANDBY, headers={"X-Demo-Key": KEY}).status_code == 200


def test_without_a_demo_key_the_switch_is_open_as_before(tmp_path: Path) -> None:
    with client(tmp_path, host="192.0.2.7") as c:
        assert c.post("/api/live", json=STANDBY, headers=TUNNEL).status_code == 200
