"""Composition root: the only place concrete adapters are chosen and wired.

`build_container` builds the process-wide singletons; `register_contexts`
is where each bounded context plugs its handlers into the realtime router.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

from app.bootstrap.demo import register_demo
from app.config.settings import AppSettings
from app.contract.messages import AudienceKeyframe
from app.modules.ai.application.service.llm import AIService
from app.modules.ai.infrastructure.adapters.llm.factory import LLMNotConfigured, build_llm_provider
from app.modules.audience.infrastructure.recognition_store import RecognitionStore
from app.modules.audience.infrastructure.recognition_vlm import RecognitionVlm
from app.modules.common.application.clock import BaseClock
from app.modules.common.application.id_generator import IdGenerator
from app.modules.common.infrastructure.clock.system_clock import SystemClock
from app.modules.creative.application.ports.voice import VoiceSynthesizer
from app.modules.creative.application.voiceover import VoiceoverService, VoiceProfile
from app.modules.creative.infrastructure.elevenlabs_tts import ElevenLabsVoiceSynthesizer
from app.modules.creative.infrastructure.media_store import FileMediaStore
from app.realtime.hub import ConnectionHub
from app.realtime.router import InboundRouter
from app.realtime.scheduler import Scheduler, thread_timer
from app.realtime.warm_worker import WarmWorker


@dataclass
class Container:
    settings: AppSettings
    clock: BaseClock
    hub: ConnectionHub
    router: InboundRouter
    warm: WarmWorker
    voiceovers: VoiceoverService | None = None
    scheduler: Scheduler = thread_timer
    recognition: RecognitionStore = field(default_factory=RecognitionStore)
    # Warm, opt-in VLM enrichment of recognition tracklets (never in prod).
    vlm: RecognitionVlm | None = None
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
    register_contexts(container)
    return container


def register_contexts(container: Container) -> None:
    """Wire bounded contexts (screens, audience, campaigns, auction, creative, delivery)."""

    if container.vlm is not None:
        container.router.on(AudienceKeyframe, container.vlm.accept)
    if container.settings.demo_simulator and container.settings.app_env != "prod":
        register_demo(
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
        )


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
