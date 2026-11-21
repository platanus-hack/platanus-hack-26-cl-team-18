"""Composition root: the only place concrete adapters are chosen and wired.

`build_container` builds the process-wide singletons; `register_contexts`
is where each bounded context plugs its handlers into the realtime router.
"""

from __future__ import annotations

from dataclasses import dataclass

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
from app.realtime.hub import ConnectionHub
from app.realtime.router import InboundRouter
from app.realtime.warm_worker import WarmWorker


@dataclass(frozen=True)
class Container:
    settings: AppSettings
    clock: BaseClock
    hub: ConnectionHub
    router: InboundRouter
    warm: WarmWorker
    recognition: RecognitionStore
    vlm: RecognitionVlm | None


def build_container(settings: AppSettings, *, clock: BaseClock | None = None) -> Container:
    recognition = RecognitionStore()
    warm = WarmWorker(settings.warm_concurrency)
    router = InboundRouter()
    vlm = None
    if settings.vlm_enabled and settings.app_env != "prod":
        try:
            provider = build_llm_provider()
        except LLMNotConfigured:
            pass  # Recognition remains available when no provider is configured.
        else:
            profile = getattr(provider, "profile", None)
            vlm = RecognitionVlm(
                AIService(provider),
                profile.describe() if profile else "unknown",
                warm,
                recognition,
                router,
            )
    container = Container(
        settings=settings,
        clock=clock or SystemClock(),
        hub=ConnectionHub(),
        router=router,
        warm=warm,
        recognition=recognition,
        vlm=vlm,
    )
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
        )
