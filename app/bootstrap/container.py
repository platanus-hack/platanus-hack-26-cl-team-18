"""Composition root: the only place concrete adapters are chosen and wired.

`build_container` builds the process-wide singletons; `register_contexts`
is where each bounded context plugs its handlers into the realtime router.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.bootstrap.demo import DEMO_SCREEN, DemoControls
from app.bootstrap.live import register_live_market
from app.config.settings import AppSettings
from app.contract.messages import AudienceKeyframe, LiveStatus
from app.modules.ai.application.service.llm import AIService
from app.modules.ai.infrastructure.adapters.llm.factory import LLMNotConfigured, build_llm_provider
from app.modules.auction.application.ports.auction_log import AuctionLog
from app.modules.auction.infrastructure.auction_log import InMemoryAuctionLog, PostgresAuctionLog
from app.modules.audience.application.ports import AudienceTagger
from app.modules.audience.application.ports.topics import TopicExtractor
from app.modules.audience.infrastructure.catalog.loader import load_catalog
from app.modules.audience.infrastructure.recognition_store import RecognitionStore
from app.modules.audience.infrastructure.recognition_vlm import RecognitionVlm
from app.modules.audience.infrastructure.tagging.claude import ClaudeAudienceTagger
from app.modules.common.application.clock import BaseClock
from app.modules.common.infrastructure.clock.system_clock import SystemClock
from app.modules.common.infrastructure.postgres.write_behind import WriteBehindWriter
from app.modules.creative.application.campaigns import CampaignGenerator
from app.modules.creative.application.ports.copywriter import CopyWriter
from app.modules.creative.application.ports.voice import VoiceSynthesizer
from app.modules.creative.application.voiceover import VoiceoverService, VoiceProfile
from app.modules.creative.infrastructure.character_budget import FileCharacterBudget
from app.modules.creative.infrastructure.elevenlabs_tts import ElevenLabsVoiceSynthesizer
from app.modules.creative.infrastructure.media_store import FileMediaStore
from app.modules.delivery.application.ports.ledger import ChargeLedger
from app.modules.delivery.infrastructure.ledger import InMemoryChargeLedger, PostgresChargeLedger
from app.realtime.freshness import FreshnessGate
from app.realtime.hub import ConnectionHub, Sink
from app.realtime.input_activity import InputActivity
from app.realtime.live_switch import LiveSwitchboard
from app.realtime.router import InboundRouter
from app.realtime.scheduler import Scheduler, thread_timer
from app.realtime.warm_worker import WarmEvent, WarmWorker

logger = logging.getLogger(__name__)


def _llm_environment(settings: AppSettings) -> dict[str, str]:
    from dotenv import dotenv_values

    env = {k: v for k, v in dotenv_values(".env").items() if v is not None} | dict(os.environ)
    # Respect explicitly supplied settings as well as the normal .env/env load.
    env["ANTHROPIC_API_KEY"] = (
        settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    )
    return env


@dataclass
class Container:
    settings: AppSettings
    clock: BaseClock
    hub: ConnectionHub
    router: InboundRouter
    warm: WarmWorker
    live: LiveSwitchboard
    voiceovers: VoiceoverService | None = None
    copywriter: CopyWriter | None = None
    scheduler: Scheduler = thread_timer
    recognition: RecognitionStore = field(default_factory=RecognitionStore)
    input_activity: InputActivity = field(default_factory=InputActivity)
    # Per screen, across agent connections: a reconnect's first reading may itself be
    # the backlog of a stalled link, so the lag the last connection ran at is kept.
    freshness: FreshnessGate = field(default_factory=FreshnessGate)
    # Warm, opt-in VLM enrichment of recognition tracklets (never in prod).
    vlm: RecognitionVlm | None = None
    # Claude vision behind AudienceTagger: keyframes -> IAB segments (AUDIENCE_AI, warm).
    tagger: AudienceTagger | None = None
    demo: DemoControls | None = None
    auction_log: AuctionLog = field(default_factory=InMemoryAuctionLog)
    ledger: ChargeLedger = field(default_factory=InMemoryChargeLedger)
    # Async hooks run once at app startup, in the background (connection warm-up,
    # voice-over pre-generation). They never block serving.
    startup: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    # Loops that run for the app's whole life (the live heartbeat); cancelled at shutdown.
    background: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    writer: WriteBehindWriter | None = None
    redemption_ledger: Any = None  # RedemptionLedger; in memory unless DATABASE_URL is set
    # CampaignRegistry: stable campaign ids, budgets and live bids (Postgres when set).
    campaign_registry: Any = None
    # Where budgets and bids live this run: "memory", "postgres", or "memory_fallback"
    # (DATABASE_URL set but unreachable at boot: spend from before is not restored).
    campaign_store: str = "memory"
    topic_extractor: TopicExtractor | None = None  # speech topics (AUDIENCE_AI)
    loop_keys: tuple[str, ...] = ()
    campaign_generator: CampaignGenerator | None = None
    # May a player socket attach to this screen id (may it create a new runtime)?
    # "ok" | "unauthorized" | "full" | "invalid"; the live market sets it (live.py).
    admit_screen: Callable[[str, bool], str] = lambda _screen_id, _may_create: "ok"
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
    audience_tagger: AudienceTagger | None = None,
    topic_extractor: TopicExtractor | None = None,
) -> Container:
    clock = clock or SystemClock()
    container = Container(
        settings=settings,
        clock=clock,
        hub=ConnectionHub(),
        router=InboundRouter(),
        warm=WarmWorker(settings.warm_concurrency),
        live=LiveSwitchboard(
            clock=clock,
            starts_live=settings.live_starts_on,
            speech_screens=settings.speech_mode_screens,
        ),
        scheduler=scheduler or thread_timer,
    )
    build_voiceovers(container, voice_synthesizer)
    build_recognition_vlm(container)
    build_copywriter(container, copywriter)
    build_audience_ai(container, tagger=audience_tagger, topics=topic_extractor)
    build_records(container)
    register_contexts(container)
    return container


def register_contexts(container: Container) -> None:
    """Wire bounded contexts (screens, audience, campaigns, auction, creative, delivery)."""

    register_dev_stream(container)
    if container.vlm is not None:
        container.router.on(AudienceKeyframe, container.vlm.accept)
    register_live_switch(container)
    if container.settings.live_ads_enabled:
        container.demo = register_live_market(container)


def build_copywriter(container: Container, copywriter: CopyWriter | None = None) -> None:
    """Claude copy behind CopyWriter (docs/creative-generation.md); off means targeted stock copy.

    `copywriter` overrides the Claude adapter (tests, offline rehearsal with recordings).
    """
    if copywriter is not None:
        container.copywriter = copywriter
        return
    if not container.settings.creative_ai_copy or container.settings.app_env == "test":
        return
    from app.modules.ai.domain.model_capabilities import DEFAULT_EFFORT, ModelProfile, parse_effort
    from app.modules.ai.infrastructure.adapters.llm.anthropic import DEFAULT_MODEL
    from app.modules.ai.infrastructure.adapters.llm.factory import (
        LLMNotConfigured,
        build_llm_provider,
    )
    from app.modules.creative.infrastructure.claude_copywriter import ClaudeCopywriter

    # The factory reads the environment only; merge `.env` so local runs match settings.
    env = _llm_environment(container.settings)
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


def build_audience_ai(
    container: Container,
    *,
    tagger: AudienceTagger | None = None,
    topics: TopicExtractor | None = None,
) -> None:
    """Warm audience AI (AUDIENCE_AI): keyframe tagging and speech topics, one Claude client.

    The tagger feeds the AudienceClassifier (IAB segments, held products, minor flag);
    without it the console shows the VLM as degraded and markers/cues still work.
    `tagger`/`topics` override the Claude adapters (tests, offline rehearsal).
    """
    if tagger is not None or topics is not None:
        container.tagger, container.topic_extractor = tagger, topics
        return
    settings = container.settings
    if not settings.audience_ai or settings.app_env == "test":
        return  # unit tests never call live providers
    from app.modules.ai.domain.model_capabilities import DEFAULT_EFFORT, ModelProfile, parse_effort
    from app.modules.ai.infrastructure.adapters.llm.anthropic import DEFAULT_MODEL
    from app.modules.audience.infrastructure.claude_topics import ClaudeTopicExtractor

    # AppSettings reads `.env` into its own fields only; the LLM factory reads a
    # mapping, so give it `.env` overlaid by the real environment.
    env = _llm_environment(settings)
    try:
        provider = build_llm_provider(env)
    except LLMNotConfigured:
        logger.warning("AUDIENCE_AI is on but ANTHROPIC_API_KEY is unset: markers/keyterms only")
        return
    container.closers.append(provider.aclose)
    model = env.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
    effort = env.get("ANTHROPIC_EFFORT", "").strip()
    profile = ModelProfile(model, parse_effort(effort) if effort else DEFAULT_EFFORT)
    container.tagger = ClaudeAudienceTagger(
        ai=AIService(provider),
        catalog=load_catalog().catalog,
        model_version=profile.describe(),
    )
    container.topic_extractor = ClaudeTopicExtractor(provider, profile)


# A long-running process with Postgres keeps only recent charges in memory.
MIRROR_MAX_CHARGES = 20_000


def build_records(container: Container) -> None:
    """Auctions and charges: always in memory; also to Postgres when DATABASE_URL is set."""
    url = container.settings.database_url
    if url is None or not url.get_secret_value() or container.settings.app_env == "test":
        return  # unit tests never touch a database, even with a developer's .env
    import psycopg2  # infrastructure only: the hot path never touches the DB

    from app.modules.common.infrastructure.postgres.spill import SpillFile, replay_spill
    from app.modules.delivery.infrastructure.ledger import INSERT_CHARGE

    dsn = url.get_secret_value()

    def transient(exc: BaseException) -> bool:
        return isinstance(exc, psycopg2.OperationalError | psycopg2.InterfaceError)

    # Charges a previous run could not write (database down at shutdown or a full
    # overflow): replayed before this process serves, idempotent per charge.
    spill = SpillFile(container.settings.charge_spill_file)
    replay_spill(spill, lambda: psycopg2.connect(dsn, connect_timeout=5), transient=transient)
    writer = WriteBehindWriter(
        lambda: psycopg2.connect(dsn, connect_timeout=5),
        name="records",
        # Charges are money: a full queue spills to a bounded overflow instead of dropping.
        overflow_capacity=50_000,
        # Lost connections retry; a refused row (constraint, bad data) is skipped alone.
        transient=transient,
        # Past the overflow, or a database still down at shutdown: charges go to a local
        # file instead of vanishing (ids, amounts and instants only: no personal data).
        spill=spill,
        durable=lambda unit: all(sql == INSERT_CHARGE for sql, _ in unit),
    )
    container.writer = writer
    container.auction_log = PostgresAuctionLog(writer, mirror=InMemoryAuctionLog())
    # Postgres is the record here; the in-memory mirror only serves this process's reads.
    container.ledger = PostgresChargeLedger(
        writer, mirror=InMemoryChargeLedger(max_charges=MIRROR_MAX_CHARGES)
    )
    from app.modules.delivery.infrastructure.redemptions import PostgresRedemptionLedger

    container.redemption_ledger = PostgresRedemptionLedger(writer)
    from app.modules.campaigns.infrastructure.registry import PostgresCampaignRegistry
    from app.modules.common.application.id_generator import IdGenerator

    container.campaign_registry = PostgresCampaignRegistry(
        lambda: psycopg2.connect(dsn, connect_timeout=5),
        writer,
        ids=IdGenerator(),
        clock=container.clock,
    )
    container.campaign_store = "postgres"

    async def close_writer() -> None:
        writer.close()

    container.closers.append(close_writer)


def build_voiceovers(container: Container, synthesizer: VoiceSynthesizer | None = None) -> None:
    """ElevenLabs TTS behind VoiceSynthesizer; absent config means 'creatives play silent'.

    `synthesizer` overrides the ElevenLabs adapter (tests, offline rehearsal).
    """
    settings = container.settings
    if synthesizer is None:
        if settings.app_env == "test":
            return  # tests inject a synthesizer; ambient credentials must never spend
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
    if not settings.vlm_enabled or settings.app_env in {"test", "prod"}:
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


def register_dev_stream(container: Container) -> None:
    """Warm-job lifecycle to `/dev` (docs/dev-console-plan.md); never in prod."""
    if not container.settings.dev_console_enabled:
        return
    from app.bootstrap import dev_stream  # composition-root helper, imported lazily

    hub, clock = container.hub, container.clock

    def observe(event: WarmEvent) -> None:
        if hub.dev_count():
            hub.broadcast_dev_threadsafe(dev_stream.job(event, at=clock.now_utc()))

    container.warm.observe(observe)


# Pages treat ~3 missed heartbeats as a dead link (web/src/lib/ws/typed-socket.ts).
LIVE_HEARTBEAT_S = 5.0


def register_live_switch(container: Container) -> None:
    """Push the "En vivo" switch to every party that acts on it (live_switch.py).

    Agents start or pause sensing, the player shows its listening indicator, and
    every console redraws the button. Each new player or console gets the current
    position on connect; agents get it in reply to their `agent.hello`.
    """
    live, hub = container.live, container.hub
    live.bind_agents(hub.agent_roles)

    def announce(_before: LiveStatus, after: LiveStatus) -> None:
        hub.send_to_agents_threadsafe(after)
        hub.send_to_screen_threadsafe(after)
        hub.broadcast_live_threadsafe(after)

    def player_connected(screen_id: str, sink: Sink) -> None:
        hub.send_to_screen_threadsafe(live.status(screen_id), sink=sink)

    def console_connected(sink: Sink) -> None:
        known = {status.screen_id for status in live.statuses()}
        screens = sorted(known | set(hub.connected_screens()))
        if screens:
            hub.broadcast_live_threadsafe(*map(live.status, screens), sink=sink)

    async def heartbeat() -> None:
        # A browser can't see a link that silently died (no client-side pings): the
        # TV kept airing a frozen ad and the console said "En vivo" over frozen
        # numbers. The current status every few seconds lets each page notice the
        # silence (TypedSocket `idleTimeoutMs`) and reconnect for a full catch-up;
        # it also repairs a status a stalled link lost (the mic indicator).
        while True:
            await asyncio.sleep(LIVE_HEARTBEAT_S)
            try:
                known = {status.screen_id for status in live.statuses()}
                screens = sorted(known | set(hub.connected_screens())) or [DEMO_SCREEN]
                statuses = [live.status(screen_id) for screen_id in screens]
                await asyncio.gather(
                    hub.broadcast_live(*statuses),
                    *(hub.send_to_screen(status) for status in statuses),
                )
            except Exception:
                logger.exception("live heartbeat failed")

    live.on_change(announce)
    container.router.on_player_connected(player_connected)
    container.router.on_console_connected(console_connected)
    container.background.append(heartbeat)
