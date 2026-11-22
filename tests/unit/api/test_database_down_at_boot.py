"""DATABASE_URL set but Postgres down at boot: the station airs ads, and says so."""

from __future__ import annotations

import asyncio
import socket
import time

from fastapi.testclient import TestClient

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.main import create_app


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])  # nothing listens once the probe closes


def test_postgres_down_at_boot_falls_back_to_memory_and_health_reports_it(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="local",
        database_url=f"postgresql://u:p@127.0.0.1:{_closed_port()}/db",
        media_dir=tmp_path,
        audience_ai=False,
        creative_ai_copy=False,
        voiceovers_enabled=False,
        vlm_enabled=False,
        dev_console=False,
    )
    started = time.monotonic()
    container = build_container(settings)
    try:
        assert time.monotonic() - started < 10, "boot must not hang on a dead database"
        assert container.demo is not None, "the market still runs: the screen is never blank"
        assert container.campaign_store == "memory_fallback"
        with TestClient(create_app(container=container)) as client:
            records = client.get("/health").json()["records"]
        assert records["campaigns"] == "memory_fallback"
        assert set(records["writer"]) >= {"written", "dropped", "failures"}
    finally:
        asyncio.run(container.aclose())


def test_without_a_database_records_are_plainly_in_memory(tmp_path) -> None:
    container = build_container(
        AppSettings(_env_file=None, app_env="test", media_dir=tmp_path)  # type: ignore[call-arg]
    )
    with TestClient(create_app(container=container)) as client:
        assert client.get("/health").json()["records"] == {"campaigns": "memory", "writer": None}
