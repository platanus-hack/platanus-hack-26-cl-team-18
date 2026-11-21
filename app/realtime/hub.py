"""Live WebSocket connections and outbound fan-out.

Players subscribe per `screen_id`; consoles get every console message. Each
batch is serialized once and sent concurrently across connections, with ordered,
bounded writes per connection. A slow client cannot stall another client or
accumulate unlimited pending decisions; it is closed so it can reconnect fresh.

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
from dataclasses import dataclass, field
from typing import Protocol

from app.contract.messages import (
    ConsoleAuction,
    ConsoleBook,
    ConsoleDegradation,
    ConsoleLatency,
    ConsoleMoment,
    ConsoleReceipt,
    ConsoleRedemption,
    CreativeReady,
    CreativeUpgraded,
    ErrorMessage,
    ScreenAttention,
)

logger = logging.getLogger(__name__)

PlayerMessage = CreativeReady | CreativeUpgraded | ErrorMessage | ScreenAttention
ConsoleMessage = (
    ConsoleMoment
    | ConsoleAuction
    | ConsoleLatency
    | ConsoleDegradation
    | CreativeReady
    | CreativeUpgraded
    | ErrorMessage
    | ConsoleBook
    | ConsoleReceipt
    | ConsoleRedemption
)

SEND_TIMEOUT_S = 1.0
MAX_PENDING_BATCHES = 32


class Sink(Protocol):
    async def send_text(self, data: str) -> None: ...


@dataclass
class HubStats:
    sent: int = 0
    dropped_sinks: int = 0
    dropped_no_loop: int = 0


@dataclass
class _SendState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: int = 0
    stopped: bool = False
    active: asyncio.Task[None] | None = None


class ConnectionHub:
    def __init__(self, *, max_pending_batches: int = MAX_PENDING_BATCHES) -> None:
        if max_pending_batches < 1:
            raise ValueError("outbound capacity must be positive")
        self._max_pending_batches = max_pending_batches
        self._send_states: dict[Sink, _SendState] = {}
        self._players: dict[str, set[Sink]] = defaultdict(set)
        # Players that asked for `screen.attention` (?attention=1); others never see it,
        # so consumers reading the creative stream in order are unaffected.
        self._attention: set[Sink] = set()
        self._consoles: set[Sink] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.stats = HubStats()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ----------------------------------------------------------- registration

    def add_player(self, screen_id: str, sink: Sink, *, attention: bool = False) -> None:
        self._send_states.setdefault(sink, _SendState())
        self._players[screen_id].add(sink)
        if attention:
            self._attention.add(sink)

    def remove_player(self, screen_id: str, sink: Sink) -> None:
        self._attention.discard(sink)
        sinks = self._players.get(screen_id)
        if sinks is not None:
            sinks.discard(sink)
            if not sinks:
                del self._players[screen_id]
        self._retire_if_unused(sink)

    def add_console(self, sink: Sink) -> None:
        self._send_states.setdefault(sink, _SendState())
        self._consoles.add(sink)

    def remove_console(self, sink: Sink) -> None:
        self._consoles.discard(sink)
        self._retire_if_unused(sink)

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
        if isinstance(message, ScreenAttention):
            subscribers = {s for s in subscribers if s in self._attention}
        recipients = subscribers if sink is None else (sink,) if sink in subscribers else ()
        await self._fan_out(recipients, (message.model_dump_json(),))

    async def broadcast_console(self, *messages: ConsoleMessage, sink: Sink | None = None) -> None:
        # Serialize once and preserve the whole decision's order per connection.
        # A slow console must never hold a global lock over healthy clients.
        recipients = self._consoles if sink is None else self._consoles & {sink}
        await self._fan_out(recipients, tuple(message.model_dump_json() for message in messages))

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

    def _retire_if_unused(self, sink: Sink) -> None:
        if sink in self._consoles or any(sink in group for group in self._players.values()):
            return
        state = self._send_states.pop(sink, None)
        if state is not None:
            state.stopped = True
            if state.active is not None and state.active is not asyncio.current_task():
                state.active.cancel()

    def _drop(self, sink: Sink, state: _SendState) -> None:
        if state.stopped:
            return
        self.stats.dropped_sinks += 1
        self._consoles.discard(sink)
        for screen_id in list(self._players):
            self.remove_player(screen_id, sink)
        self._retire_if_unused(sink)

    async def _close_slow_sink(self, sink: Sink) -> None:
        # Starlette WebSockets support close; lightweight application/test sinks
        # may only implement send_text. Closing asks a real player to reconnect
        # and receive the current creative, instead of retaining an obsolete queue.
        close = getattr(sink, "close", None)
        if close is not None:
            try:
                await asyncio.wait_for(
                    close(code=1013, reason="outbound backpressure"), SEND_TIMEOUT_S
                )
            except Exception:
                pass

    async def _send_batch(self, sink: Sink, data: tuple[str, ...]) -> None:
        state = self._send_states.get(sink)
        if state is None or state.stopped or not data:
            return
        if state.pending >= self._max_pending_batches:
            self._drop(sink, state)
            await self._close_slow_sink(sink)
            return
        state.pending += 1
        try:
            async with state.lock:
                if state.stopped:
                    return
                state.active = asyncio.current_task()
                try:
                    for text in data:
                        await asyncio.wait_for(sink.send_text(text), SEND_TIMEOUT_S)
                        self.stats.sent += 1
                finally:
                    state.active = None
        except asyncio.CancelledError:
            if not state.stopped:
                raise
        except Exception:
            self._drop(sink, state)
            await self._close_slow_sink(sink)
        finally:
            state.pending -= 1

    async def _fan_out(self, sinks: Iterable[Sink], data: tuple[str, ...]) -> None:
        await asyncio.gather(*(self._send_batch(sink, data) for sink in list(sinks)))
