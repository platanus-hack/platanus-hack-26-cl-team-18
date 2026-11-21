"""The agent's single connection to the backend (`/ws/agent`, docs/architecture.md §10).

- Reconnects forever with capped exponential backoff + jitter.
- Latest pending state per (type, screen), with a monotonic freshness deadline.
  Events keep their order; a keyframe header and its binary stay one item.
- Bounded outbox: when full, drop the oldest pending item.
- Rejected frames come back as `system.error`; they are logged and counted,
  never retried (a bad frame stays bad).
- `hello` (an `agent.hello`) is sent first on every connection, ahead of the
  outbox; the backend answers with `live.status`, handed to `on_status`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import websockets
from pydantic import BaseModel, ValidationError

from agent.contract import LiveStatus

logger = logging.getLogger(__name__)


class _Socket(Protocol):
    async def send(self, message: str | bytes) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


type Connect = Callable[[str], Awaitable[_Socket]]


async def _default_connect(url: str) -> _Socket:
    return await websockets.connect(url, max_size=2**20, ping_interval=10, ping_timeout=10)  # type: ignore[return-value]


@dataclass(eq=False)
class _Pending:
    text: str
    binary: bytes | None
    key: tuple[str, str] | None
    queued_at: float


@dataclass
class LinkStats:
    sent: int = 0
    dropped: int = 0
    rejected: int = 0
    reconnects: int = 0


class BackendLink:
    def __init__(
        self,
        url: str,
        *,
        connect: Connect = _default_connect,
        max_pending: int = 64,
        backoff_base_s: float = 0.25,
        backoff_max_s: float = 5.0,
        state_max_age_s: float = 1.5,
        monotonic: Callable[[], float] = time.monotonic,
        hello: BaseModel | None = None,
        on_status: Callable[[LiveStatus], None] | None = None,
    ) -> None:
        self._url = url
        self._hello = hello
        self._on_status = on_status
        self._connect = connect
        self._pending: deque[_Pending] = deque()
        self._now = monotonic
        self._state_max_age_s = state_max_age_s
        if max_pending < 1 or state_max_age_s <= 0:
            raise ValueError("outbox capacity and state lifetime must be positive")
        self._max_pending = max_pending
        self._wakeup = asyncio.Event()
        self._backoff = (backoff_base_s, backoff_max_s)
        self._connected = asyncio.Event()
        self.stats = LinkStats()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def send(self, message: BaseModel, *, binary: bytes | None = None) -> None:
        """Queue a contract message (and an optional binary frame right after it)."""
        kind = getattr(message, "type", "")
        key = (
            (kind, str(getattr(message, "screen_id", "")))
            if kind in {"audience.snapshot", "audience.keyframe", "recognition.snapshot"}
            else None
        )
        if key is not None:
            for old in list(self._pending):
                if old.key == key:
                    self._pending.remove(old)
                    self.stats.dropped += 1
        if len(self._pending) >= self._max_pending:
            self._pending.popleft()
            self.stats.dropped += 1
        self._pending.append(_Pending(message.model_dump_json(), binary, key, self._now()))
        self._wakeup.set()

    async def wait_connected(self, timeout: float) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                socket = await self._connect(self._url)
            except (OSError, websockets.WebSocketException) as exc:
                attempt += 1
                await self._sleep_backoff(attempt, exc)
                continue
            attempt = 0
            try:
                if self._hello is not None:
                    await socket.send(self._hello.model_dump_json())
                self._connected.set()
                await self._pump(socket)
            except (OSError, websockets.WebSocketException) as exc:
                logger.warning("backend link dropped: %s", exc)
            finally:
                self._connected.clear()
                await _quiet_close(socket)
            self.stats.reconnects += 1
            attempt += 1
            await self._sleep_backoff(attempt, None)

    async def _pump(self, socket: _Socket) -> None:
        reader = asyncio.create_task(self._read(socket))
        try:
            while True:
                if not self._pending:
                    self._wakeup.clear()
                    waiter = asyncio.create_task(self._wakeup.wait())
                    done, _ = await asyncio.wait(
                        {waiter, reader}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if reader in done:
                        waiter.cancel()
                        reader.result()  # re-raise the disconnect
                        return
                    continue
                # Remove before yielding: concurrent send() may replace pending state,
                # but must never evict a different message after this send completes.
                item = self._pending.popleft()
                if self._expired(item):
                    self.stats.dropped += 1
                    continue
                try:
                    await socket.send(item.text)
                    if item.binary is not None:
                        await socket.send(item.binary)
                except (OSError, websockets.WebSocketException):
                    superseded = item.key is not None and any(
                        p.key == item.key for p in self._pending
                    )
                    if (
                        not superseded
                        and not self._expired(item)
                        and len(self._pending) < self._max_pending
                    ):
                        self._pending.appendleft(item)
                    else:
                        self.stats.dropped += 1
                    raise
                self.stats.sent += 1
        finally:
            reader.cancel()

    def _expired(self, item: _Pending) -> bool:
        return item.key is not None and self._now() - item.queued_at > self._state_max_age_s

    async def _read(self, socket: _Socket) -> None:
        while True:
            frame = await socket.recv()
            if isinstance(frame, bytes):
                continue
            try:
                payload: Any = json.loads(frame)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("type") == "live.status":
                try:
                    status = LiveStatus.model_validate(payload)
                except ValidationError:
                    logger.warning("backend sent an invalid live.status; ignored")
                    continue
                if self._on_status is not None:
                    self._on_status(status)
            elif isinstance(payload, dict) and payload.get("type") == "system.error":
                self.stats.rejected += 1
                logger.warning(
                    "backend rejected %s: %s %s",
                    payload.get("rejected_type"),
                    payload.get("code"),
                    payload.get("message"),
                )

    async def _sleep_backoff(self, attempt: int, exc: BaseException | None) -> None:
        base, cap = self._backoff
        ceiling = min(cap, base * 2 ** max(0, attempt - 1))
        delay = ceiling / 2 + random.random() * ceiling / 2
        if exc is not None:
            logger.info("backend unreachable (%s); retrying in %.2fs", exc, delay)
        await asyncio.sleep(delay)


async def _quiet_close(socket: _Socket) -> None:
    try:
        await socket.close()
    except Exception:  # closing a dead socket must never raise
        pass
