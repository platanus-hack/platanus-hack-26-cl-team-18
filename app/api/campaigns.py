"""Read-only campaign drafting from the approved catalog; no activation or budget writes.

Demo surface: behind the demo key (app/api/demo_access.py).
"""

from typing import Annotated

import anyio
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.demo_access import require_demo_key
from app.bootstrap.container import Container
from app.contract.messages import CampaignCatalog, CampaignGenerate, CampaignGenerated
from app.modules.creative.application.campaigns import CampaignGenerator

router = APIRouter(
    prefix="/api/campaigns", tags=["campaigns"], dependencies=[Depends(require_demo_key)]
)


def _generator(request: Request) -> CampaignGenerator:
    container: Container = request.app.state.container
    if container.campaign_generator is None:
        raise HTTPException(503, "El catálogo de campañas no está disponible")
    return container.campaign_generator


@router.get("/catalog")
async def catalog(request: Request) -> CampaignCatalog:
    return await anyio.to_thread.run_sync(_generator(request).catalog)


@router.post("/generate")
async def generate(
    request: Request, message: Annotated[CampaignGenerate, Body()]
) -> CampaignGenerated:
    try:
        return await anyio.to_thread.run_sync(_generator(request).generate, message)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/upgrade")
async def upgrade(
    request: Request, message: Annotated[CampaignGenerate, Body()]
) -> CampaignGenerated:
    """The same draft with guarded AI copy (Claude), or the stock draft if AI is off or lost."""
    try:
        return await _generator(request).upgrade(message)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
