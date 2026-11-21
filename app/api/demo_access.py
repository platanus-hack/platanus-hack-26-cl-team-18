"""The demo surface's key: bids, stock, campaign drafts, `/dev` QA and `/dev/push`.

The real station is `/screen`, `/console` and `/r/{code}`; everything built to
*show* the product sits behind `DEMO_KEY` (the web app's hidden `/demo/<key>`
hub). The key travels as the `X-Demo-Key` header (`?key=` on the `/ws/dev`
socket, which can't send headers) and is compared in constant time.

A wrong or missing key answers 404, not 401: the surface doesn't exist for
anyone without it. Production never serves it, whatever the key. Tests and
evals (`app_env=test`) run in-process and need no key.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from app.config.settings import AppSettings

DEMO_KEY_HEADER = "X-Demo-Key"


def demo_allowed(settings: AppSettings, presented: str | None) -> bool:
    if settings.app_env == "prod":
        return False
    if settings.app_env == "test":
        return True
    expected = settings.demo_key.get_secret_value() if settings.demo_key else ""
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


async def require_demo_key(request: Request) -> None:
    """FastAPI dependency for every demo router."""
    if not demo_allowed(request.app.state.container.settings, request.headers.get(DEMO_KEY_HEADER)):
        raise HTTPException(status_code=404)
