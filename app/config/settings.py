"""Typed process configuration, read once at startup from the environment / `.env`."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    app_env: Literal["local", "test", "prod"] = "local"
    log_level: str = "INFO"
    demo_simulator: bool = False
    vlm_enabled: bool = False
    # JSON list in the env, e.g. CORS_ORIGINS='["http://localhost:5173"]'
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    # Largest accepted WS frame; a keyframe JPEG is the biggest legitimate one.
    ws_max_frame_bytes: int = Field(default=2_000_000, gt=0)
    # Pending agent messages per connection before the oldest is dropped.
    ws_agent_mailbox_size: int = Field(default=32, gt=0)
    # Local media cache (voice-overs, video loops, hero images), served at /media.
    media_dir: Path = Path("var/media")
    # Max concurrent warm calls per provider (WarmWorker); JSON object in the env.
    warm_concurrency: dict[str, int] = Field(
        default_factory=lambda: {"claude": 4, "elevenlabs": 4, "fal": 2, "vlm": 2}
    )
