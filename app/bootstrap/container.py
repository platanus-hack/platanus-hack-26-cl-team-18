"""Composition root: the only place concrete adapters are chosen and wired.

`build_container` builds the process-wide singletons; `register_contexts`
is where each bounded context plugs its handlers into the realtime router.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

from app.bootstrap.demo import DemoControls, register_demo
from app.config.settings import AppSettings
from app.contract.messages import AudienceKeyframe
from app.modules.ai.application.service.llm import AIService
from app.modules.ai.infrastructure.adapters.llm.factory import LLMNotConfigured, build_llm_provider
from app.modules.auction.application.ports.auction_log import AuctionLog
from app.modules.auction.infrastructure.auction_log import InMemoryAuctionLog, PostgresAuctionLog
from app.modules.audience.infrastructure.recognition_store import RecognitionStore
from app.modules.audience.infrastructure.recognition_vlm import RecognitionVlm
from app.modules.common.application.clock import BaseClock
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.infrastructure.clock.system_clock import SystemClock
from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter
from app.modules.creative.application.ports.copywriter import CopyWriter
from app.modules.creative.application.ports.voice import VoiceSynthesizer
from app.modules.creative.application.voiceover import VoiceoverService, VoiceProfile
from app.modules.creative.infrastructure.character_budget import FileCharacterBudget
from app.modules.creative.infrastructure.elevenlabs_tts import ElevenLabsVoiceSynthesizer
from app.modules.creative.infrastructure.media_store import FileMediaStore
from app.modules.delivery.application.ports.ledger import ChargeLedger
from app.modules.delivery.infrastructure.ledger import InMemoryChargeLedger, PostgresChargeLedger
from app.realtime.hub import ConnectionHub
from app.realtime.router import InboundRouter
from app.realtime.scheduler import Scheduler, thread_timer
from app.realtime.warm_worker import WarmWorker

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: AppSettings
    clock: BaseClock
    hub: ConnectionHub
    router: InboundRouter
    warm: WarmWorker
    voiceovers: VoiceoverService | None = None
    copywriter: CopyWriter | None = None
    scheduler: Scheduler = thread_timer
    recognition: RecognitionStore = field(default_factory=RecognitionStore)
    # Warm, opt-in VLM enrichment of recognition tracklets (never in prod).
    vlm: RecognitionVlm | None = None
    demo: DemoControls | None = None
    auction_log: AuctionLog = field(default_factory=InMemoryAuctionLog)
    ledger: ChargeLedger = field(default_factory=InMemoryChargeLedger)
    # Async hooks run once at app startup, in the background (connection warm-up,
    # voice-over pre-generation). They never block serving.
    startup: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    writer: WriteBehindWriter | None = None
    # Async cleanups run at shutdown (HTTP clients, ...).
    closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    async def aclose(self) -> None:
        for close in reversed(self.closers):
            await close()


def build_container(
    settings: AppSettings,
    *,
    clock: BaseClock | None = None,
    voice_synthesizer: VoiceSynthesizer | None = None,
    copywriter: CopyWriter | None = None,
    scheduler: Scheduler | None = None,
) -> Container:
    container = Container(
        settings=settings,
        clock=clock or SystemClock(),
        hub=ConnectionHub(),
        router=InboundRouter(),
        warm=WarmWorker(settings.warm_concurrency),
        scheduler=scheduler or thread_timer,
    )
    build_voiceovers(container, voice_synthesizer)
    build_recognition_vlm(container)
    build_copywriter(container, copywriter)
    build_records(container)
    register_contexts(container)
    return container


def register_contexts(container: Container) -> None:
    """Wire bounded contexts (screens, audience, campaigns, auction, creative, delivery)."""

    if container.vlm is not None:
        container.router.on(AudienceKeyframe, container.vlm.accept)
    if container.settings.demo_simulator and container.settings.app_env != "prod":
        container.demo = register_demo(
            router=container.router,
            hub=container.hub,
            clock=container.clock,
            ids=IdGenerator(),
            warm=container.warm,
            voiceovers=container.voiceovers,
            narrator_voice_id=container.settings.elevenlabs_narrator_voice_id,
            speech_screens=frozenset(container.settings.speech_mode_screens),
            speech_ttl=timedelta(seconds=container.settings.speech_topic_ttl_s),
            swap_holds=container.settings.swap_holds_enabled,
            scheduler=container.scheduler,
            auction_log=container.auction_log,
            ledger=container.ledger,
            budget_clp=container.settings.demo_budget_clp,
            copywriter=container.copywriter,
            copy_timeout_s=container.settings.creative_copy_timeout_s,
            hourly_budget_usd=container.settings.creative_hourly_budget_usd,
        )


def build_copywriter(container: Container, copywriter: CopyWriter | None = None) -> None:
    """Claude copy behind CopyWriter (docs/creative-generation.md); off means targeted stock copy.

    `copywriter` overrides the Claude adapter (tests, offline rehearsal with recordings).
    """
    if copywriter is not None:
        container.copywriter = copywriter
        return
    if not container.settings.creative_ai_copy:
        return
    from dotenv import dotenv_values

    from app.modules.ai.domain.model_capabilities import DEFAULT_EFFORT, ModelProfile, parse_effort
    from app.modules.ai.infrastructure.adapters.llm.anthropic import DEFAULT_MODEL
    from app.modules.ai.infrastructure.adapters.llm.factory import (
        LLMNotConfigured,
        build_llm_provider,
    )
    from app.modules.creative.infrastructure.claude_copywriter import ClaudeCopywriter

    # The factory reads the environment only; merge `.env` so local runs match settings.
    env = {k: v for k, v in dotenv_values(".env").items() if v is not None} | dict(os.environ)
    try:
        provider = build_llm_provider(env)
    except LLMNotConfigured:
        logger.warning("CREATIVE_AI_COPY is on but ANTHROPIC_API_KEY is unset: stock copy only")
        return
    container.closers.append(provider.aclose)
    model = env.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
    effort = env.get("ANTHROPIC_EFFORT", "").strip()
    profile = ModelProfile(model, parse_effort(effort) if effort else DEFAULT_EFFORT)
    container.copywriter = ClaudeCopywriter(provider, profile)


def build_records(container: Container) -> None:
    """Auctions and charges: always in memory; also to Postgres when DATABASE_URL is set."""
    url = container.settings.database_url
    if url is None or not url.get_secret_value() or container.settings.app_env == "test":
        return  # unit tests never touch a database, even with a developer's .env
    import psycopg2  # infrastructure only: the hot path never touches the DB

    dsn = url.get_secret_value()
    writer = WriteBehindWriter(
        lambda: psycopg2.connect(dsn, connect_timeout=5),
        name="records",
        # Lost connections retry; a refused row (constraint, bad data) is skipped alone.
        transient=lambda exc: isinstance(exc, psycopg2.OperationalError | psycopg2.InterfaceError),
    )
    container.writer = writer
    container.auction_log = PostgresAuctionLog(writer, mirror=InMemoryAuctionLog())
    container.ledger = PostgresChargeLedger(writer, mirror=InMemoryChargeLedger())

    async def close_writer() -> None:
        writer.close()

    container.closers.append(close_writer)


def build_voiceovers(container: Container, synthesizer: VoiceSynthesizer | None = None) -> None:
    """ElevenLabs TTS behind VoiceSynthesizer; absent config means 'creatives play silent'.

    `synthesizer` overrides the ElevenLabs adapter (tests, offline rehearsal).
    """
    settings = container.settings
    if synthesizer is None:
        if not settings.voiceovers_configured or settings.elevenlabs_api_key is None:
            return
        adapter = ElevenLabsVoiceSynthesizer(
            settings.elevenlabs_api_key.get_secret_value(), base_url=settings.elevenlabs_base_url
        )
        container.closers.append(adapter.aclose)
        container.startup.append(adapter.warm)
        synthesizer = adapter
    elif not (settings.voiceovers_enabled and settings.elevenlabs_narrator_voice_id):
        return
    container.voiceovers = VoiceoverService(
        synthesizer,
        FileMediaStore(settings.media_dir),
        VoiceProfile(
            model_id=settings.elevenlabs_tts_model,
            language_code=settings.elevenlabs_language,
            output_format=settings.elevenlabs_output_format,
        ),
        FileCharacterBudget(settings.elevenlabs_budget_file, limit=settings.elevenlabs_char_budget),
    )


def build_recognition_vlm(container: Container) -> None:
    """Opt-in VLM enrichment of head-redacted tracklet crops; absent config means off."""
    settings = container.settings
    if not settings.vlm_enabled or settings.app_env == "prod":
        return
    try:
        provider = build_llm_provider()
    except LLMNotConfigured:
        return  # Recognition stays available without a provider.
    profile = getattr(provider, "profile", None)
    ai = AIService(provider)
    container.closers.append(ai.aclose)
    container.vlm = RecognitionVlm(
        ai,
        profile.describe() if profile else "unknown",
        container.warm,
        container.recognition,
        container.router,
    )
