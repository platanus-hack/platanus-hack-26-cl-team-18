"""Composition root: the only place concrete adapters are chosen and wired.

`build_container` builds the process-wide singletons; `register_contexts`
is where each bounded context plugs its handlers into the realtime router.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.bootstrap.demo import register_demo
from app.config.settings import AppSettings
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


def build_container(settings: AppSettings, *, clock: BaseClock | None = None) -> Container:
    container = Container(
        settings=settings,
        clock=clock or SystemClock(),
        hub=ConnectionHub(),
        router=InboundRouter(),
        warm=WarmWorker(settings.warm_concurrency),
    )
    register_contexts(container)
    return container


def register_contexts(container: Container) -> None:
    """Wire bounded contexts (screens, audience, campaigns, auction, creative, delivery)."""

    if container.settings.demo_simulator and container.settings.app_env != "prod":
        register_demo(
            router=container.router,
            hub=container.hub,
            clock=container.clock,
            ids=IdGenerator(),
        )
