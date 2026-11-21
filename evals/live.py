"""A real backend on a loopback port: uvicorn, the ASGI app and the realtime hub.

Evals talk to it over real WebSockets exactly as the agent, player and console
do, so a passing eval exercises serialization, the event loop, the worker
thread hop, the lock and the hub. Nothing in the loop is mocked.
"""

from __future__ import annotations

import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import TypeAdapter
from websockets.sync.client import ClientConnection, connect

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import CONSOLE_OUTBOUND, PLAYER_OUTBOUND
from app.main import create_app
from app.modules.creative.application.ports.copywriter import CopyWriter
from app.modules.creative.application.ports.voice import VoiceSynthesizer

START_TIMEOUT_S = 10.0


@dataclass(frozen=True, slots=True)
class LiveBackend:
    http_url: str
    ws_url: str

    def player(self, screen_id: str) -> ClientConnection:
        return connect(f"{self.ws_url}/ws/screen/{screen_id}", open_timeout=5)

    def agent(self) -> ClientConnection:
        return connect(f"{self.ws_url}/ws/agent", open_timeout=5)

    def console(self) -> ClientConnection:
        return connect(f"{self.ws_url}/ws/console", open_timeout=5)


@contextmanager
def serve(app: Any) -> Iterator[str]:
    """Run any ASGI app with uvicorn on a free loopback port; yields ``host:port``."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", lifespan="on", ws_ping_interval=None)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + START_TIMEOUT_S
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("eval server failed to start")
        time.sleep(0.01)
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


@contextmanager
def live_backend(
    *,
    media_dir: Path | None = None,
    copywriter: CopyWriter | None = None,
    voice_synthesizer: VoiceSynthesizer | None = None,
    **settings_overrides: Any,
) -> Iterator[LiveBackend]:
    """Boot a fresh simulator backend (fresh in-memory state) and tear it down.

    ``copywriter`` / ``voice_synthesizer`` plug real or replayed providers into
    the composition root's own override points, so the full generation stack
    (studio, guard, cache, voice-over service, warm worker) runs unmodified.
    """
    with tempfile.TemporaryDirectory(prefix="evals-media-") as tmp:
        options = {
            "_env_file": None,
            "voiceovers_enabled": voice_synthesizer is not None,
            "voiceover_prewarm": False,
            **settings_overrides,
            # Offline evals use explicitly injected adapters, never ambient developer keys.
            "anthropic_api_key": None,
            "elevenlabs_api_key": None,
            "fal_key": None,
            "vlm_enabled": False,
        }
        settings = AppSettings(
            app_env="test",
            demo_simulator=True,
            media_dir=media_dir or Path(tmp) / "media",
            elevenlabs_budget_file=Path(tmp) / "elevenlabs-budget.json",
            **options,
        )
        container = build_container(
            settings, copywriter=copywriter, voice_synthesizer=voice_synthesizer
        )
        with serve(create_app(container=container)) as host:
            yield LiveBackend(http_url=f"http://{host}", ws_url=f"ws://{host}")


PLAYER_MESSAGES: TypeAdapter = PLAYER_OUTBOUND
CONSOLE_MESSAGES: TypeAdapter = CONSOLE_OUTBOUND
