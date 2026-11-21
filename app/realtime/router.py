"""Routes validated inbound messages to the handlers bounded contexts register.

Handlers are synchronous (the kernel is sync) and run in a worker thread via
`anyio.to_thread.run_sync`, so a handler never blocks the event loop. A failing
handler is logged and isolated: it can't take down the socket or its siblings.
Hot-path rule: a handler must not await a network model call; enqueue warm work
instead (docs/adr/0001).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import anyio

from app.contract.messages import (
    AudienceKeyframe,
    AudienceSnapshot,
    AudienceUtterance,
    PlaybackReport,
)
from app.realtime.hub import Sink

logger = logging.getLogger(__name__)

Inbound = AudienceSnapshot | AudienceKeyframe | AudienceUtterance | PlaybackReport


@dataclass(frozen=True, slots=True)
class Received:
    """Server-side facts about one inbound message."""

    received_at: datetime
    payload: bytes | None = None  # keyframe JPEG, in memory only: never stored or logged


type MessageHandler[M] = Callable[[M, Received], None]
type PlayerConnectionHandler = Callable[[str, Sink], None]
type ConnectionHandler = Callable[[str], None]


class InboundRouter:
    def __init__(self) -> None:
        self._handlers: dict[type, list[MessageHandler[Any]]] = defaultdict(list)
        self._on_player_connected: list[PlayerConnectionHandler] = []
        self._on_player_disconnected: list[ConnectionHandler] = []
        self._on_console_connected: list[Callable[[Sink], None]] = []

    def on[M: Inbound](self, message_type: type[M], handler: MessageHandler[M]) -> None:
        self._handlers[message_type].append(handler)

    def on_player_connected(self, handler: PlayerConnectionHandler) -> None:
        """E.g. the creative context pushes the house ad / last good creative."""
        self._on_player_connected.append(handler)

    def on_player_disconnected(self, handler: ConnectionHandler) -> None:
        self._on_player_disconnected.append(handler)

    def on_console_connected(self, handler: Callable[[Sink], None]) -> None:
        self._on_console_connected.append(handler)

    def has_handlers(self, message_type: type[Inbound]) -> bool:
        return bool(self._handlers.get(message_type))

    async def dispatch(self, message: Inbound, received: Received) -> None:
        handlers = self._handlers.get(type(message), [])
        if not handlers:
            logger.debug("no handler for %s", message.type)
            return
        for handler in handlers:
            await self._run(handler, message, received, label=message.type)

    async def player_connected(self, screen_id: str, sink: Sink) -> None:
        for handler in self._on_player_connected:
            await self._run(handler, screen_id, sink, label="player.connected")

    async def player_disconnected(self, screen_id: str) -> None:
        for handler in self._on_player_disconnected:
            await self._run(
                cast(Callable[..., None], handler), screen_id, label="player.disconnected"
            )

    async def console_connected(self, sink: Sink) -> None:
        for handler in self._on_console_connected:
            await self._run(handler, sink, label="console.connected")

    @staticmethod
    async def _run(handler: Callable[..., None], *args: object, label: str) -> None:
        try:
            await anyio.to_thread.run_sync(handler, *args)
        except Exception:
            logger.exception(
                "inbound handler failed",
                extra={"event_type": label, "handler": getattr(handler, "__qualname__", "?")},
            )
