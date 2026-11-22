"""Static media with cache headers the browser and Render's CDN can act on.

Campaign assets (logos, photos) and generated media (bundle loops) were served with no
Cache-Control: Cloudflare passed every request through to the single free-plan instance
in Virginia (cf-cache-status: DYNAMIC, 0.2-0.7 s for a loop from Santiago) and the
player re-validated them on every creative swap. The player decodes the logos before it
swaps, so that round trip sat on the glass-to-glass path.

The policy is short enough that a redeploy's new campaign art shows within minutes, and
lets the edge keep serving a cached copy while it refreshes in the background.
"""

from __future__ import annotations

import os
from typing import Any

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

MEDIA_CACHE_CONTROL = "public, max-age=300, s-maxage=600, stale-while-revalidate=86400"


class CachedStaticFiles(StaticFiles):
    """StaticFiles that sets one Cache-Control policy on every file it serves."""

    def __init__(self, *args: Any, cache_control: str = MEDIA_CACHE_CONTROL, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.cache_control = cache_control

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers.setdefault("Cache-Control", self.cache_control)
        return response
