"""The demo surface exists only for the demo key; the real station never needs it."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.main import create_app

KEY = "s3cret-demo-key"
DEMO_GETS = ["/api/book?screen_id=demo-01", "/api/hooks", "/api/campaigns/catalog"]
PUSH = {
    "type": "console.degradation",
    "v": 1,
    "screen_id": "demo-01",
    "flags": [],
}


def client(tmp_path: Path, **settings: object) -> TestClient:
    values = {"_env_file": None, "app_env": "local", "media_dir": tmp_path, **settings}
    return TestClient(create_app(container=build_container(AppSettings(**values))))  # type: ignore[arg-type]


@pytest.mark.parametrize("path", DEMO_GETS)
def test_demo_routes_are_hidden_without_the_key(tmp_path: Path, path: str) -> None:
    with client(tmp_path, demo_key=KEY) as c:
        assert c.get(path).status_code == 404
        assert c.get(path, headers={"X-Demo-Key": "wrong"}).status_code == 404
        assert c.get(path, headers={"X-Demo-Key": KEY}).status_code == 200


def test_writes_and_dev_push_need_the_key_too(tmp_path: Path) -> None:
    stock = {
        "type": "stock.set",
        "v": 1,
        "screen_id": "demo-01",
        "brand_id": "x",
        "in_stock": False,
    }
    with client(tmp_path, demo_key=KEY) as c:
        assert c.post("/api/clerk/stock", json=stock).status_code == 404
        assert c.post("/dev/push", json=PUSH).status_code == 404
        assert c.post("/dev/push", json=PUSH, headers={"X-Demo-Key": KEY}).status_code == 202


def test_no_configured_key_means_no_demo_surface(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        assert c.get("/api/book", headers={"X-Demo-Key": ""}).status_code == 404
        assert c.get("/api/book", headers={"X-Demo-Key": "anything"}).status_code == 404


PROD_KEY = "0123456789abcdef0123456789abcdef"


def test_production_serves_the_market_controls_only_with_a_strong_key(tmp_path: Path) -> None:
    with client(tmp_path, app_env="prod", demo_key=PROD_KEY) as c:
        assert c.get("/api/campaigns/catalog").status_code == 404
        assert c.get("/api/campaigns/catalog", headers={"X-Demo-Key": "x"}).status_code == 404
        assert c.get("/api/campaigns/catalog", headers={"X-Demo-Key": PROD_KEY}).status_code == 200
        assert (
            c.get("/api/book?screen_id=demo-01", headers={"X-Demo-Key": PROD_KEY}).status_code
            == 200
        )


def test_production_refuses_a_short_key(tmp_path: Path) -> None:
    with client(tmp_path, app_env="prod", demo_key=KEY) as c:
        assert c.get("/api/campaigns/catalog", headers={"X-Demo-Key": KEY}).status_code == 404


def test_production_never_serves_dev_even_with_the_key(tmp_path: Path) -> None:
    with client(tmp_path, app_env="prod", demo_key=PROD_KEY) as c:
        headers = {"X-Demo-Key": PROD_KEY}
        assert c.post("/dev/push", json=PUSH, headers=headers).status_code == 404
        with (
            c.websocket_connect(f"/ws/dev?key={PROD_KEY}") as ws,
            pytest.raises(WebSocketDisconnect),
        ):
            ws.receive_json()


def test_the_real_station_needs_no_key(tmp_path: Path) -> None:
    with client(tmp_path, demo_key=KEY) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/api/live").status_code == 200
        with c.websocket_connect("/ws/screen/demo-01") as player:
            assert player.receive_json()["type"] == "creative.ready"


def test_the_dev_socket_needs_the_key_as_a_query_parameter(tmp_path: Path) -> None:
    with client(tmp_path, demo_key=KEY) as c:
        with c.websocket_connect("/ws/dev") as ws, pytest.raises(WebSocketDisconnect):
            ws.receive_json()
        with c.websocket_connect(f"/ws/dev?key={KEY}") as ws:
            ws.send_text("{}")
            assert ws.receive_json()["code"] == "dev.read_only"
