"""The console's "En vivo" switch: GET the position, POST a change.

Thin transport: validate the contract message -> the switchboard (a worker thread,
since listeners re-run the screen's decision synchronously) -> the new status.
Like the other operator inputs, production rejects it (no operator auth yet).
"""

from __future__ import annotations

from typing import Annotated

import anyio
from fastapi import APIRouter, Body, HTTPException, Query, Request

from app.bootstrap.container import Container
from app.contract.messages import LiveSet, LiveStatus

router = APIRouter(prefix="/api", tags=["live"])


def _container(request: Request) -> Container:
    container: Container = request.app.state.container
    if container.settings.app_env == "prod":
        raise HTTPException(status_code=404, detail="the live switch is a station control")
    return container


@router.get("/live")
async def live_status(
    request: Request,
    screen_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")] = "demo-01",
) -> LiveStatus:
    return _container(request).live.status(screen_id)


@router.post("/live")
async def live_set(request: Request, message: Annotated[LiveSet, Body()]) -> LiveStatus:
    container = _container(request)
    return await anyio.to_thread.run_sync(container.live.set, message)
