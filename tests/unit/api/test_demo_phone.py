from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from scripts.demo import phone as demo_phone
from starlette.applications import Starlette
from starlette.testclient import TestClient


def test_phone_relay_forwards_only_offer_reads_and_bounded_redemptions(monkeypatch) -> None:
    received: list[tuple[str, str]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        received.append((request.method, request.url.path))
        if request.method == "HEAD":
            return httpx.Response(405)
        if request.method == "POST":
            return httpx.Response(410, json={"detail": "expired"})
        return httpx.Response(200, text="Trío $8.490", headers={"content-type": "text/html"})

    app = demo_phone.create_relay("http://localhost:8018")

    @asynccontextmanager
    async def lifespan(relay: Starlette) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            base_url="http://localhost:8018",
            transport=httpx.MockTransport(upstream),
        ) as client:
            relay.state.backend = client
            yield

    monkeypatch.setattr(app.router, "lifespan_context", lifespan)
    with TestClient(app) as phone:
        assert phone.get("/").status_code == 404
        assert phone.get("/api/campaigns").status_code == 404
        assert phone.get("/r/A").status_code == 404
        assert phone.post("/r/ABCDEFGH/redeem", data={"nonce": "example"}).status_code == 415
        assert phone.post("/r/ABCDEFGH/redeem", json={"nonce": "a" * 1025}).status_code == 413
        head = phone.head("/r/ABCDEFGH")
        assert head.status_code == 200 and head.content == b""
        assert head.headers["cache-control"] == "no-store"
        assert phone.get("/r/ABCDEFGH").text == "Trío $8.490"
        assert phone.post("/r/ABCDEFGH/redeem", json={"nonce": "example"}).status_code == 410
    assert received == [
        ("GET", "/r/ABCDEFGH"),
        ("GET", "/r/ABCDEFGH"),
        ("POST", "/r/ABCDEFGH/redeem"),
    ]
