"""Non-secret configuration status. Configured does not mean a provider was contacted."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, TypedDict
from urllib.parse import urlsplit

from pydantic import SecretStr

from app.config.settings import AppSettings, _is_loopback
from app.modules.creative.infrastructure.campaign_assets import missing_campaign_photos

if TYPE_CHECKING:
    from app.bootstrap.container import Container


class ProviderStatus(TypedDict):
    enabled: bool
    configured: bool
    missing: list[str]


def _set(secret: SecretStr | None) -> bool:
    return bool(secret and secret.get_secret_value())


def _listens_beyond_loopback(web_host: str | None) -> bool:
    if web_host is None:
        return False
    return web_host in {"0.0.0.0", "::"} or not _is_loopback(web_host)


def exposure_warnings(
    settings: AppSettings, public_base_url: str | None = None, *, web_host: str | None = None
) -> list[str]:
    """Open sockets and switches on a station reachable from outside (a tunnel or the LAN).

    Outside prod an unset AGENT_TOKEN/SCREEN_TOKEN leaves its socket open (device_auth.py),
    which is fine on localhost but lets anyone on the tunnel feed the loop or bill. An
    unset CONSOLE_TOKEN leaves `/ws/console` (every bid, charge and budget) open, and
    without DEMO_KEY the "En vivo" switch (`POST /api/live`, app/api/live.py) answers
    anyone who reaches the station. Prod refuses unset device tokens on its own, but its
    console stays open without CONSOLE_TOKEN. Warnings only, never a failure: the demo
    must still start. Names only, never values. `web_host`: where the player/console
    listen (0.0.0.0 is the second laptop's LAN setup), when the caller knows it.
    """
    if settings.app_env == "prod":
        if _set(settings.console_token):
            return []
        return [
            "CONSOLE_TOKEN unset in prod: /ws/console streams every bid, charge and budget "
            "to anyone with the URL; set it in the Render dashboard (openssl rand -hex 16)"
        ]
    host = urlsplit(public_base_url or settings.public_base_url).hostname or ""
    public = bool(host) and not _is_loopback(host)
    if not public and not _listens_beyond_loopback(web_host):
        return []
    where = f"PUBLIC_BASE_URL is {host}" if public else f"the web host is {web_host}"
    warnings = []
    unset = [
        name
        for name, secret in (
            ("AGENT_TOKEN", settings.agent_token),
            ("SCREEN_TOKEN", settings.screen_token),
        )
        if not _set(secret)
    ]
    if unset:
        warnings.append(
            f"{' and '.join(unset)} unset while {where}: anyone who reaches "
            "it can feed the camera loop or bill plays; set them in .env (openssl rand -hex 16)"
        )
    if not _set(settings.console_token):
        warnings.append(
            f"CONSOLE_TOKEN unset while {where}: anyone who reaches it can read the console "
            "stream (bids, charges, budgets); set it in .env to close /ws/console"
        )
    if not _set(settings.demo_key):
        warnings.append(
            f"DEMO_KEY unset while {where}: anyone who reaches it can flip the console's "
            "live switch (POST /api/live); with DEMO_KEY set only this machine or the key can"
        )
    return warnings


def records_status(container: Container) -> dict[str, object]:
    """Where this run's budgets and records go. Counts and an exception class name
    only: never a DSN or row content."""
    writer = container.writer
    return {
        "campaigns": container.campaign_store,
        "writer": None
        if writer is None
        else {
            "written": writer.stats.written,
            "dropped": writer.stats.dropped,
            "rejected": writer.stats.rejected,
            "spilled": writer.stats.spilled,
            "failures": writer.stats.failures,
            "last_error": writer.stats.last_error,
        },
    }


def live_readiness(container: Container) -> dict[str, object]:
    s = container.settings
    ai_missing = (
        []
        if s.anthropic_api_key and s.anthropic_api_key.get_secret_value().strip()
        else ["ANTHROPIC_API_KEY"]
    )
    voice_missing = [
        name
        for name, value in (
            (
                "ELEVENLABS_API_KEY",
                s.elevenlabs_api_key and s.elevenlabs_api_key.get_secret_value().strip(),
            ),
            ("ELEVENLABS_NARRATOR_VOICE_ID", s.elevenlabs_narrator_voice_id),
        )
        if not value
    ]
    video_missing = [
        name
        for name, value in (
            ("FAL_KEY", s.fal_key and s.fal_key.get_secret_value().strip()),
            ("FAL_VIDEO_MODEL", s.fal_video_model),
        )
        if not value
    ]
    providers: dict[str, ProviderStatus] = {
        "audience_tags": {
            "enabled": s.audience_ai,
            "configured": container.tagger is not None,
            "missing": ai_missing,
        },
        "ai_copy": {
            "enabled": s.creative_ai_copy,
            "configured": container.copywriter is not None,
            "missing": ai_missing,
        },
        "voice": {
            "enabled": s.voiceovers_enabled,
            "configured": container.voiceovers is not None,
            "missing": voice_missing,
        },
        "cold_video": {"enabled": True, "configured": not video_missing, "missing": video_missing},
    }
    missing_loops = [
        key for key in container.loop_keys if not (s.media_dir / "loops" / f"{key}.mp4").is_file()
    ]
    return {
        "enabled": s.live_ads_enabled,
        "providers": providers,
        "media": {
            "expected_loops": len(container.loop_keys),
            "missing_loops": missing_loops,
            "generation_tools_missing": [] if shutil.which("ffmpeg") else ["ffmpeg"],
            "missing_campaign_photos": missing_campaign_photos(),
        },
        "fallbacks": {"copy": "approved_stock", "voice": "silent", "video": "template"},
    }
