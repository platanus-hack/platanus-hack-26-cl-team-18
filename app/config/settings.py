"""Typed process configuration, read once at startup from the environment / `.env`."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    app_env: Literal["local", "test", "prod"] = "local"
    log_level: str = "INFO"
    demo_simulator: bool = False
    vlm_enabled: bool = False
    # Postgres is the record (auctions, charges), written behind the hot path.
    # Unset: everything stays in memory (simulator, tests, offline rehearsal).
    database_url: SecretStr | None = None
    # Each seeded demo campaign's budget (CLP). Small values exercise budget exhaustion.
    demo_budget_clp: int = Field(default=50_000, gt=0)
    # Minimum ad seconds (docs/auction-plan.md D5): hold a swap so ads aren't strobed.
    # None = on, except app_env=test (fixed-clock tests get immediate swaps unless
    # they opt in and inject a scheduler).
    auction_swap_holds: bool | None = None

    @property
    def swap_holds_enabled(self) -> bool:
        if self.auction_swap_holds is None:
            return self.app_env != "test"
        return self.auction_swap_holds

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

    # ------------------------------------------------------------ ElevenLabs (ADR 0005)
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    # Flash v2.5: ~100 ms model latency, Spanish, 550k chars/month on Creator.
    elevenlabs_tts_model: str = "eleven_flash_v2_5"
    # mp3_44100_128 is the best MP3 below Creator's mp3_44100_192 (which costs more bytes).
    elevenlabs_output_format: str = "mp3_44100_128"
    elevenlabs_language: str = "es"
    # Neutral "screen narrator" for co-brand lines (a brand voice would own the line).
    # Pick one with `uv run python -m scripts.elevenlabs_voices`.
    elevenlabs_narrator_voice_id: str | None = None
    voiceovers_enabled: bool = True
    # Hard cap on TTS characters this install may ever spend (persisted across restarts).
    # The account has ~10k credits: leave headroom for rehearsal and live tests.
    elevenlabs_char_budget: int = Field(default=2000, ge=0)
    # Outside media_dir on purpose: /media is publicly served.
    elevenlabs_budget_file: Path = Path("var/elevenlabs-budget.json")
    # ------------------------------------------------------------ creative (docs/creative-generation.md)
    # Warm AI copy (Claude Haiku 5.5) upgrades targeted stock copy in place. Needs
    # ANTHROPIC_API_KEY; without it every creative airs on stock copy.
    creative_ai_copy: bool = False
    creative_copy_timeout_s: float = Field(default=2.5, gt=0, le=20)
    # Generation spend cap per screen-hour, USD (the ROI rule applies under it).
    creative_hourly_budget_usd: Decimal = Field(default=Decimal(2), gt=0, le=1000)
    # Speech topics from opted-in screens (ADR 0005: off by default, per screen).
    speech_mode_screens: list[str] = Field(default_factory=list)
    speech_topic_ttl_s: int = Field(default=60, gt=0, le=600)

    @property
    def voiceovers_configured(self) -> bool:
        return bool(
            self.voiceovers_enabled
            and self.elevenlabs_api_key
            and self.elevenlabs_api_key.get_secret_value()
            and self.elevenlabs_narrator_voice_id
        )
