"""Operator inputs for the demo: `/advertiser` (bidding war) and `/clerk` (stock).

Thin transport: validate the contract message → call the demo controls in a
worker thread (the kernel is synchronous) → map domain errors to HTTP. No
business logic here. The live market supplies controls; production rejects operator calls.
"""

from __future__ import annotations

from typing import Annotated

import anyio
from fastapi import APIRouter, Body, HTTPException, Query, Request

from app.bootstrap.container import Container
from app.bootstrap.demo import DemoControls, UnknownBrand
from app.contract.messages import AdvertiserBidMultiplier, ConsoleBook, StockSet
from app.modules.campaigns.application.live_book import (
    ConflictingMultiplierSeq,
    UnknownCampaign,
)

router = APIRouter(prefix="/api", tags=["demo"])


def _controls(request: Request) -> DemoControls:
    container: Container = request.app.state.container
    if container.demo is None or container.settings.app_env == "prod":
        raise HTTPException(status_code=404, detail="the demo market is not running")
    return container.demo


@router.get("/book")
async def book(
    request: Request,
    screen_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")] = "demo-01",
) -> ConsoleBook:
    controls = _controls(request)
    if controls.book_for is not None:
        return await anyio.to_thread.run_sync(controls.book_for, screen_id)
    return await anyio.to_thread.run_sync(controls.book_view)


@router.post("/advertiser/bid-multiplier")
async def bid_multiplier(
    request: Request, message: Annotated[AdvertiserBidMultiplier, Body()]
) -> dict[str, str]:
    controls = _controls(request)
    try:
        outcome = await anyio.to_thread.run_sync(controls.set_multiplier, message)
    except UnknownCampaign as exc:
        raise HTTPException(status_code=404, detail=exc.msg) from exc
    except ConflictingMultiplierSeq as exc:
        raise HTTPException(status_code=409, detail=exc.msg) from exc
    except ValueError as exc:  # outside [0.2, 5.0]
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"outcome": outcome.value}


@router.post("/clerk/stock")
async def clerk_stock(request: Request, message: Annotated[StockSet, Body()]) -> dict[str, bool]:
    controls = _controls(request)
    try:
        changed = await anyio.to_thread.run_sync(controls.set_stock, message)
    except UnknownBrand as exc:
        raise HTTPException(status_code=404, detail="no such brand at this screen") from exc
    return {"changed": changed}
