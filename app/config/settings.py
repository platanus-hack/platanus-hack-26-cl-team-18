"""Typed process configuration, read once at startup from the environment / `.env`."""

from __future__ import annotations

import logging
from decimal import Decimal
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Hosts a judge's phone can never open: a QR pointing here is dead on arrival.
_LOOPBACK_HOSTS = {"localhost", "0.0.0.0", "::"}
# app/api/demo_access.py and device_auth.py refuse shorter secrets in prod.
_PROD_MIN_SECRET_LENGTH = 24


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_HOSTS or host.endswith(".localhost"):
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, validate_by_name=True
    )

    app_env: Literal["local", "test", "prod"] = "local"
    log_level: str = "INFO"
    demo_simulator: bool = False
    # The real sensing-to-ad loop runs independently of simulator/operator tooling.
    live_ads_enabled: bool = True
    # The console's "En vivo" switch position at startup (app/realtime/live_switch.py).
    # `make demo-live` starts in standby (false): nothing is sensed or generated until
    # the operator presses the button. Tests, evals and replays keep the loop live.
    live_starts_on: bool = True
    vlm_enabled: bool = False
    # Postgres is the record (auctions, charges), written behind the hot path.
    # Unset: everything stays in memory (simulator, tests, offline rehearsal).
    database_url: SecretStr | None = None
    # Each seeded demo campaign's budget (CLP). Small values exercise budget exhaustion.
    demo_budget_clp: int = Field(default=50_000, gt=0)
    # Which brand catalog the market runs (app/bootstrap/demo_catalog.py): "fictional"
    # (tests, evals, the public URL) or "chile" (real brands at a Pronto Copec, for the
    # local station; real trademarks, so keep it off public deploys).
    demo_catalog: Literal["fictional", "chile"] = "fictional"
    # Where phones reach the backend through the tunnel (QR → {public_base_url}/r/{code}).
    # Unset on Render: the service's own URL (RENDER_EXTERNAL_URL), which serves /r/{code}.
    public_base_url: str = Field(
        default="http://localhost:5173",
        validation_alias=AliasChoices("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL"),
    )
    # Warm audience AI (Claude): keyframe tags (objects/held products only) and speech
    # topics for utterances the keyterm matcher missed. Off: keyterms and markers only.
    audience_ai: bool = True
    anthropic_api_key: SecretStr | None = None  # readiness only; the AI factory owns the client
    # Minimum ad seconds (docs/auction-plan.md D5): hold a swap so ads aren't strobed.
    # None = on, except app_env=test (fixed-clock tests get immediate swaps unless
    # they opt in and inject a scheduler).
    auction_swap_holds: bool | None = None

    # `/dev` QA console (docs/dev-console-plan.md): `/ws/dev` and its `dev.*` stream.
    # Never in prod, whatever this says: the stream has no auth.
    # The demo surface (app/api/demo_access.py): bids, stock, campaign drafts, `/dev`
    # QA and `/dev/push`, reached from the hidden `/demo/<key>` web hub. Unset: off.
    # In prod only with a key of 24+ chars, and never `/dev`; app_env=test needs no key.
    demo_key: SecretStr | None = None
    # Device tokens for the live loop (app/api/device_auth.py). AGENT_TOKEN: the vision /
    # speech agent's bearer token on /ws/agent. SCREEN_TOKEN: lets a player's reports
    # (playback.report → charges) count; without it a player is view-only. Unset: open
    # outside prod, closed in prod (where each needs 24+ chars).
    agent_token: SecretStr | None = None
    screen_token: SecretStr | None = None
    dev_console: bool = True
    # Opt-in: relay the face-blurred keyframe the VLM gets to `/dev` (in memory, never
    # stored). Off by default; refused in prod.
    dev_keyframe_preview: bool = False
    # Demo only: the console's camera panel shows a face-blurred, downscaled camera
    # view under the boxes (in memory, never stored). Always off in prod.
    demo_camera_preview: bool = True

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if not isinstance(logging.getLevelNamesMapping().get(level), int):
            raise ValueError(f"unknown log level {value!r} (DEBUG, INFO, WARNING, ERROR)")
        return level

    @field_validator("public_base_url")
    @classmethod
    def _http_origin(cls, value: str) -> str:
        url = urlsplit(value.strip())
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError(
                f"{value!r} is not an http(s) URL; phones open {{PUBLIC_BASE_URL}}/r/<code>"
            )
        return value.strip().rstrip("/")

    @field_validator("warm_concurrency")
    @classmethod
    def _positive_limits(cls, value: dict[str, int]) -> dict[str, int]:
        # A limit of 0 parks every job of that provider forever (no error, no upgrade).
        bad = {name: limit for name, limit in value.items() if limit < 1}
        if bad:
            raise ValueError(f"every provider limit must be >= 1, got {bad}")
        return value

    def misconfigurations(self) -> list[str]:
        """Valid but almost certainly unintended settings, for the boot log and preflight."""
        problems = []
        host = urlsplit(self.public_base_url).hostname or ""
        if _is_loopback(host):
            problems.append(
                f"PUBLIC_BASE_URL is {self.public_base_url}: the QR's /r/<code> link only "
                "opens on this machine; set it to the tunnel or LAN URL for judges' phones"
            )
        if self.app_env == "prod":
            for name, secret in (
                ("DEMO_KEY", self.demo_key),
                ("AGENT_TOKEN", self.agent_token),
                ("SCREEN_TOKEN", self.screen_token),
            ):
                value = secret.get_secret_value() if secret else ""
                if value and len(value) < _PROD_MIN_SECRET_LENGTH:
                    problems.append(
                        f"{name} has {len(value)} chars; prod needs {_PROD_MIN_SECRET_LENGTH}+, "
                        "so it unlocks nothing (openssl rand -hex 16)"
                    )
        return problems

    @property
    def demo_camera_preview_enabled(self) -> bool:
        return self.demo_camera_preview and self.app_env != "prod"

    @property
    def dev_console_enabled(self) -> bool:
        return self.dev_console and self.app_env != "prod"

    @property
    def dev_keyframe_preview_enabled(self) -> bool:
        return self.dev_keyframe_preview and self.dev_console_enabled

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
    # Synthesize every approved offer line at startup (cold path) so plays are cache hits.
    # Off by default because it spends credits (~281 for the demo's 7 lines, once; then
    # cached on disk). Start once with VOICEOVER_PREWARM=true before a rehearsal.
    voiceover_prewarm: bool = False
    # Outside media_dir on purpose: /media is publicly served.
    elevenlabs_budget_file: Path = Path("var/elevenlabs-budget.json")
    # ------------------------------------------------------------ creative (docs/creative-generation.md)
    # Warm AI copy (Claude Haiku 5.5) upgrades targeted stock copy in place. Needs
    # ANTHROPIC_API_KEY; without it every creative airs on stock copy.
    creative_ai_copy: bool = True
    creative_copy_timeout_s: float = Field(default=6.0, gt=0, le=20)
    # Generation spend cap per screen-hour, USD (the ROI rule applies under it).
    creative_hourly_budget_usd: Decimal = Field(default=Decimal(2), gt=0, le=1000)
    # Speech topics from opted-in screens (ADR 0005: off by default, per screen).
    speech_mode_screens: list[str] = Field(default_factory=list)
    speech_topic_ttl_s: int = Field(default=60, gt=0, le=600)
    # Cold bundle-loop generation is an explicit CLI job, never an automatic live call.
    fal_key: SecretStr | None = None
    # Open-weights LTX-2 19B distilled on fal (~US$0.10 per 6 s 16:9 clip); any fal
    # text-to-video id works, see fal_video.request_body.
    fal_video_model: str | None = "fal-ai/ltx-2-19b/distilled/text-to-video"

    @property
    def voiceovers_configured(self) -> bool:
        return bool(
            self.voiceovers_enabled
            and self.elevenlabs_api_key
            and self.elevenlabs_api_key.get_secret_value()
            and self.elevenlabs_narrator_voice_id
        )
