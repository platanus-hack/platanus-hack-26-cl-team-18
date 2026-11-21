"""ASGI entrypoint: `uvicorn app.main:app` (Render start command and `make dev`)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import dev, health, ws
from app.bootstrap.container import Container, build_container
from app.config.logging_setup import configure_logging
from app.config.settings import AppSettings


def create_app(
    settings: AppSettings | None = None, *, container: Container | None = None
) -> FastAPI:
    settings = settings or (container.settings if container else AppSettings())
    built = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.app_env != "test":
            configure_logging(settings.log_level)
        loop = asyncio.get_running_loop()
        built.hub.bind_loop(loop)
        built.warm.bind_loop(loop)
        try:
            yield
        finally:
            await built.warm.aclose()

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
    if settings.app_env != "prod":
        app.include_router(dev.router)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
    return app


app = create_app()
