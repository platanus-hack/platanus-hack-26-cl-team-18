"""ASGI entrypoint: `uvicorn app.main:app` (Render start command and `make dev`)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import campaigns, demo_controls, dev, health, live, recognition, redeem, speech, ws
from app.bootstrap.container import Container, build_container
from app.bootstrap.keep_alive import keep_alive
from app.bootstrap.readiness import exposure_warnings
from app.config.logging_setup import configure_logging, install_url_redaction
from app.config.settings import AppSettings
from app.modules.creative.infrastructure.campaign_assets import CAMPAIGN_ASSETS

logger = logging.getLogger(__name__)


def create_app(
    settings: AppSettings | None = None, *, container: Container | None = None
) -> FastAPI:
    settings = settings or (container.settings if container else AppSettings())
    built = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # uvicorn configured its loggers before importing the app; keys never reach them.
        install_url_redaction()
        if settings.app_env != "test":
            configure_logging(settings.log_level)
            for problem in settings.misconfigurations() + exposure_warnings(settings):
                logger.warning("config: %s", problem)
        loop = asyncio.get_running_loop()
        built.hub.bind_loop(loop)
        built.warm.bind_loop(loop)

        async def expire_recognition() -> None:
            while True:
                await asyncio.sleep(5)
                try:
                    built.recognition.prune(built.clock.now_utc())
                except Exception:
                    # One bad prune must not end expiry for the process's lifetime.
                    logger.exception("recognition prune failed")

        expiry_task = asyncio.create_task(expire_recognition())
        startup_tasks: list[asyncio.Future[None]] = [
            asyncio.ensure_future(hook()) for hook in (*built.startup, *built.background)
        ]
        if settings.keep_alive_url and settings.app_env != "test":
            startup_tasks.append(
                asyncio.ensure_future(
                    keep_alive(settings.keep_alive_url, settings.keep_alive_interval_s)
                )
            )
        try:
            yield
        finally:
            expiry_task.cancel()
            for task in startup_tasks:
                task.cancel()
            try:
                await expiry_task
            except asyncio.CancelledError:
                pass
            await built.warm.aclose()
            await built.aclose()

    app = FastAPI(
        title="team18",
        lifespan=lifespan,
        docs_url=None if settings.app_env == "prod" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.app_env == "prod" else "/openapi.json",
    )
    app.state.container = built
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(ws.router)
    app.include_router(recognition.router)
    app.include_router(demo_controls.router)
    app.include_router(live.router)
    app.include_router(redeem.router)
    app.include_router(speech.router)
    app.include_router(campaigns.router)
    if settings.app_env != "prod":
        app.include_router(dev.router)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
    app.mount("/campaign-assets", StaticFiles(directory=CAMPAIGN_ASSETS), name="campaign-assets")
    return app


app = create_app()
