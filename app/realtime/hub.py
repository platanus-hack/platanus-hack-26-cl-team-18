"""Live WebSocket connections and outbound fan-out.

Players subscribe per `screen_id`; consoles get every console message. Each
message is serialized once and sent to every sink concurrently with a timeout,
so one slow or dead client is dropped instead of stalling the others.

Handlers run in worker threads (see `router.py`), so they publish with the
`*_threadsafe` methods, which schedule the send on the hub's event loop and
return immediately (fire and forget: the hot path never waits on a socket).
Bounded contexts reach the hub through their own application ports, with an
adapter in their infrastructure layer; application code never imports this.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.contract.messages import (
    ConsoleAuction,
    ConsoleDegradation,
    ConsoleLatency,
    ConsoleMoment,
    CreativeReady,
    CreativeUpgraded,
    ErrorMessage,
)

logger = logging.getLogger(__name__)

PlayerMessage = CreativeReady | CreativeUpgraded | ErrorMessage
ConsoleMessage = (
    ConsoleMoment
    | ConsoleAuction
    | ConsoleLatency
    | ConsoleDegradation
    | CreativeReady
    | CreativeUpgraded
    | ErrorMessage
)

SEND_TIMEOUT_S = 1.0


class Sink(Protocol):
    async def send_text(self, data: str) -> None: ...


@dataclass
class HubStats:
    sent: int = 0
    dropped_sinks: int = 0
    dropped_no_loop: int = 0


class ConnectionHub:
    def __init__(self) -> None:
        self._players: dict[str, set[Sink]] = defaultdict(set)
        self._consoles: set[Sink] = set()
        self._console_send_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.stats = HubStats()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ----------------------------------------------------------- registration

    def add_player(self, screen_id: str, sink: Sink) -> None:
        self._players[screen_id].add(sink)

    def remove_player(self, screen_id: str, sink: Sink) -> None:
        sinks = self._players.get(screen_id)
        if sinks is not None:
            sinks.discard(sink)
            if not sinks:
                del self._players[screen_id]

    def add_console(self, sink: Sink) -> None:
        self._consoles.add(sink)

    def remove_console(self, sink: Sink) -> None:
        self._consoles.discard(sink)

    def connected_screens(self) -> list[str]:
        return sorted(self._players)

    def console_count(self) -> int:
        return len(self._consoles)

    # ----------------------------------------------------------- async send

    async def send_to_screen(
        self,
        message: PlayerMessage,
        *,
        screen_id: str | None = None,
        sink: Sink | None = None,
    ) -> None:
        target = screen_id or getattr(message, "screen_id", None)
        if target is None:
            raise ValueError("send_to_screen needs a screen_id")
        subscribers = self._players.get(target, ())
        recipients = subscribers if sink is None else (sink,) if sink in subscribers else ()
        dead = await self._fan_out(recipients, message.model_dump_json())
        for disconnected in dead:
            self.remove_player(target, disconnected)

    async def broadcast_console(self, *messages: ConsoleMessage, sink: Sink | None = None) -> None:
        # Deliver each decision's moment, auction and latency in order.
        async with self._console_send_lock:
            for message in messages:
                recipients = self._consoles if sink is None else self._consoles & {sink}
                dead = await self._fan_out(recipients, message.model_dump_json())
                for disconnected in dead:
                    self.remove_console(disconnected)

    # ----------------------------------------------------------- from worker threads

    def send_to_screen_threadsafe(
        self, message: PlayerMessage, *, sink: Sink | None = None
    ) -> None:
        self._schedule(self.send_to_screen(message, sink=sink))

    def broadcast_console_threadsafe(
        self, *messages: ConsoleMessage, sink: Sink | None = None
    ) -> None:
        self._schedule(self.broadcast_console(*messages, sink=sink))

    def _schedule(self, coro: object) -> None:
        if self._loop is None or self._loop.is_closed():
            self.stats.dropped_no_loop += 1
            if hasattr(coro, "close"):
                coro.close()
            logger.warning("hub has no running loop; outbound message dropped")
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]

    # ----------------------------------------------------------- internals

    async def _fan_out(self, sinks: Iterable[Sink], data: str) -> list[Sink]:
        sinks = list(sinks)
        if not sinks:
            return []
        results = await asyncio.gather(
            *(asyncio.wait_for(sink.send_text(data), SEND_TIMEOUT_S) for sink in sinks),
            return_exceptions=True,
        )
        dead = [sink for sink, result in zip(sinks, results, strict=True) if result is not None]
        self.stats.sent += len(sinks) - len(dead)
        self.stats.dropped_sinks += len(dead)
        return dead
