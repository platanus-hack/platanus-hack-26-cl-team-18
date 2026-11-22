"""Keep a free-plan Render service awake by pinging its own public URL.

Render's free plan spins a web service down after 15 min without inbound
traffic (~50 s cold start, and the in-memory auctions/spend reset). A GET to
the service's public URL goes through Render's edge, so it counts as traffic.
GitHub's scheduled keep-warm workflow is best-effort (runs are delayed or
skipped), so the process keeps itself warm while it lives. Opt-in via
KEEP_ALIVE_URL; never on the hot path.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

PING_TIMEOUT_S = 10.0


async def keep_alive(
    url: str,
    interval_s: float,
    *,
    get: Callable[[str], Awaitable[int]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Ping `url` every `interval_s` until cancelled. A failed ping is logged, never raised."""
    async with httpx.AsyncClient(timeout=PING_TIMEOUT_S) as client:

        async def http_get(target: str) -> int:
            return (await client.get(target)).status_code

        ping = get or http_get
        while True:
            await sleep(interval_s)
            try:
                status = await ping(url)
                if status >= 400:
                    logger.warning("keep-alive ping got HTTP %s", status)
            except Exception as exc:  # one bad ping must not end the loop
                logger.warning("keep-alive ping failed: %s", type(exc).__name__)
