"""A single-use ElevenLabs Scribe token for the browser sensor in `/screen` (ADR 0010).

The browser streams the room mic straight to Scribe Realtime; the API key never
leaves the backend. The token is minted only for a screen in speech mode
(`SPEECH_MODE_SCREENS`, ADR 0005) and only for a caller holding the SCREEN_TOKEN
(`Authorization: Bearer`). Transcripts come back to the backend as ephemeral
`audience.utterance` frames on `/ws/agent`; nothing is stored here.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.device_auth import token_allowed
from app.bootstrap.container import Container
from app.contract.speech_matching import DEFAULT_KEYTERMS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/speech", tags=["speech"])

SCRIBE_MODEL_ID = "scribe_v3_live"  # what the edge agent uses (agent.speech.scribe)
TOKEN_TIMEOUT_S = 5.0


class ScribeSession(BaseModel):
    token: str
    model_id: str
    language_code: str
    keyterms: list[str]


def _bearer(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


@router.post("/token/{screen_id}", response_model=ScribeSession)
async def scribe_token(screen_id: str, request: Request) -> ScribeSession:
    c: Container = request.app.state.container
    settings = c.settings
    if not token_allowed(settings, settings.screen_token, _bearer(request)):
        raise HTTPException(status_code=403, detail="screen token required")
    if screen_id not in settings.speech_mode_screens:
        raise HTTPException(status_code=409, detail="speech mode is off for this screen")
    key = settings.elevenlabs_api_key.get_secret_value() if settings.elevenlabs_api_key else ""
    if not key:
        raise HTTPException(status_code=503, detail="speech provider not configured")
    url = f"{settings.elevenlabs_base_url.rstrip('/')}/v1/single-use-token/realtime_scribe"
    try:
        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT_S) as client:
            response = await client.post(url, headers={"xi-api-key": key})
        response.raise_for_status()
        token = str(response.json()["token"])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("scribe token unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="speech provider unavailable") from exc
    return ScribeSession(
        token=token,
        model_id=SCRIBE_MODEL_ID,
        language_code=settings.elevenlabs_language,
        keyterms=list(DEFAULT_KEYTERMS.prompt_terms()),
    )
