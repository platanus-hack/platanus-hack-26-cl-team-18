"""Dev-only push: send any outbound contract message to a screen / the consoles.

Exercises the player and console before the bounded contexts exist, and is the
seed of the simulator (demo insurance). Disabled in prod (404). Transport only:
no business logic lives here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import Field

from app.bootstrap.container import Container
from app.contract.messages import (
    ConsoleAuction,
    ConsoleDegradation,
    ConsoleLatency,
    ConsoleMoment,
    CreativeReady,
    CreativeUpgraded,
)

router = APIRouter(prefix="/dev", tags=["dev"])

Pushable = Annotated[
    CreativeReady
    | CreativeUpgraded
    | ConsoleMoment
    | ConsoleAuction
    | ConsoleLatency
    | ConsoleDegradation,
    Field(discriminator="type"),
]


@router.post("/push", status_code=202)
async def push(request: Request, message: Annotated[Pushable, Body()]) -> dict[str, str]:
    c: Container = request.app.state.container
    if c.settings.app_env == "prod":
        raise HTTPException(status_code=404)
    if isinstance(message, CreativeReady | CreativeUpgraded):
        await c.hub.send_to_screen(message)
    await c.hub.broadcast_console(message)
    return {"status": "pushed", "type": message.type}
