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

import uvicorn
from pydantic import TypeAdapter
from websockets.sync.client import ClientConnection, connect

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import CONSOLE_OUTBOUND, PLAYER_OUTBOUND
from app.main import create_app

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
def live_backend(*, media_dir: Path | None = None) -> Iterator[LiveBackend]:
    """Boot a fresh simulator backend (fresh in-memory state) and tear it down."""
    with tempfile.TemporaryDirectory(prefix="evals-media-") as tmp:
        settings = AppSettings(
            app_env="test", demo_simulator=True, media_dir=media_dir or Path(tmp)
        )
        app = create_app(container=build_container(settings))
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
                raise RuntimeError("eval backend failed to start")
            time.sleep(0.01)
        try:
            yield LiveBackend(http_url=f"http://127.0.0.1:{port}", ws_url=f"ws://127.0.0.1:{port}")
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            sock.close()


PLAYER_MESSAGES: TypeAdapter = PLAYER_OUTBOUND
CONSOLE_MESSAGES: TypeAdapter = CONSOLE_OUTBOUND
