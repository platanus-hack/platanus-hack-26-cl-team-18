"""The console's "En vivo" switch: GET the position, POST a change.

Thin transport: validate the contract message -> the switchboard (a worker thread,
since listeners re-run the screen's decision synchronously) -> the new status.
Like the other operator inputs, production rejects it (no operator auth yet).

Who may flip it: with `DEMO_KEY` unset, anyone who reaches the station (as before).
With `DEMO_KEY` set, a POST needs the key (`X-Demo-Key`, which the console sends once
this browser has opened `/demo/<key>`) or must come straight from this machine: the
station's backend listens on 127.0.0.1 behind the Vite proxy (which adds no
forwarding headers), so the station laptop and the second laptop over the LAN
(`--web-host 0.0.0.0`) keep working. A request through the cloudflared tunnel
carries Cloudflare's `cf-connecting-ip`/`cf-ray` (and `x-forwarded-for`), so a
stranger on the public URL can't put the station in standby. Reading stays open.
"""

from __future__ import annotations

from ipaddress import ip_address
from typing import Annotated

import anyio
from fastapi import APIRouter, Body, HTTPException, Query, Request

from app.api.demo_access import DEMO_KEY_HEADER, demo_allowed
from app.bootstrap.container import Container
from app.contract.messages import LiveSet, LiveStatus

router = APIRouter(prefix="/api", tags=["live"])

# Set by a tunnel, CDN or forwarding proxy: the request did not start on this machine.
FORWARDING_HEADERS = (
    "cf-connecting-ip",
    "cf-ray",
    "cdn-loop",
    "true-client-ip",
    "x-forwarded-for",
    "x-real-ip",
    "forwarded",
)


def _container(request: Request) -> Container:
    container: Container = request.app.state.container
    if container.settings.app_env == "prod":
        raise HTTPException(status_code=404, detail="the live switch is a station control")
    return container


def _loopback(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def from_this_machine(request: Request) -> bool:
    """A direct loopback request that came through no tunnel or forwarding proxy."""
    client = request.client.host if request.client else ""
    return _loopback(client) and not any(name in request.headers for name in FORWARDING_HEADERS)


def may_switch(container: Container, request: Request) -> bool:
    settings = container.settings
    if settings.demo_key is None:
        return True  # no key configured: the station as it always was
    if demo_allowed(settings, request.headers.get(DEMO_KEY_HEADER)):
        return True
    return from_this_machine(request)


@router.get("/live")
async def live_status(
    request: Request,
    screen_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")] = "demo-01",
) -> LiveStatus:
    return _container(request).live.status(screen_id)


@router.post("/live")
async def live_set(request: Request, message: Annotated[LiveSet, Body()]) -> LiveStatus:
    container = _container(request)
    if not may_switch(container, request):
        raise HTTPException(
            status_code=403, detail="the live switch needs the demo key from outside the station"
        )
    return await anyio.to_thread.run_sync(container.live.set, message)
