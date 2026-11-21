"""Read the latest ephemeral recognition snapshot for a classifier."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.bootstrap.container import Container
from app.contract.messages import RecognitionSnapshot

router = APIRouter(prefix="/recognition", tags=["recognition"])


class RecognitionRead(BaseModel):
    received_at: datetime
    snapshot: RecognitionSnapshot


@router.get("/{screen_id}", response_model=RecognitionRead)
def latest(screen_id: str, request: Request) -> RecognitionRead:
    container: Container = request.app.state.container
    if container.settings.app_env == "prod":
        # The existing gateway has no classifier authentication yet. In production,
        # classifiers subscribe to the in-process router instead of exposing profiles.
        raise HTTPException(status_code=404)
    item = container.recognition.get(screen_id, container.clock.now_utc())
    if item is None:
        raise HTTPException(status_code=404, detail="No current recognition snapshot")
    return RecognitionRead(snapshot=item[0], received_at=item[1])
