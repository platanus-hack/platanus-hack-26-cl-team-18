"""Non-secret configuration status. Configured does not mean a provider was contacted."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, TypedDict

from app.modules.creative.infrastructure.campaign_assets import missing_campaign_photos

if TYPE_CHECKING:
    from app.bootstrap.container import Container


class ProviderStatus(TypedDict):
    enabled: bool
    configured: bool
    missing: list[str]


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
