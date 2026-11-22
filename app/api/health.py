from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.bootstrap.container import Container
from app.bootstrap.readiness import live_readiness, records_status
from app.contract.messages import CONTRACT_VERSION

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Liveness for Render plus a cheap live view of the realtime layer."""
    c: Container = request.app.state.container
    return {
        "status": "ok",
        "env": c.settings.app_env,
        "contract_version": CONTRACT_VERSION,
        "ads": live_readiness(c),
        "screens_connected": c.hub.connected_screens(),
        "inputs": c.input_activity.snapshot(
            at=c.clock.now_utc(),
            speech_screens=c.settings.speech_mode_screens,
            players=c.hub.connected_screens(),
        ),
        "consoles_connected": c.hub.console_count(),
        "live": {
            "starts_on": c.settings.live_starts_on,
            "dropped_in_standby": c.live.dropped,
            "screens": {s.screen_id: s.model_dump(mode="json") for s in c.live.statuses()},
        },
        "warm": {
            "submitted": c.warm.stats.submitted,
            "deduped": c.warm.stats.deduped,
            "failed": c.warm.stats.failed,
            "in_flight": c.warm.stats.in_flight,
        },
        "records": records_status(c),
        "hub": {
            "sent": c.hub.stats.sent,
            "dropped_sinks": c.hub.stats.dropped_sinks,
            "dropped_no_loop": c.hub.stats.dropped_no_loop,
        },
    }
