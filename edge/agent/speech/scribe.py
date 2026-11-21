"""ElevenLabs Scribe Realtime client (raw WebSocket; ADR 0005, docs/stack.md §3).

Protocol (wss://api.elevenlabs.io/v1/speech-to-text/realtime):
- client -> `{"message_type":"input_audio_chunk","audio_base_64":..,"commit":bool,"sample_rate":16000}`
- server -> `session_started`, `partial_transcript`, `committed_transcript[_with_timestamps]`,
  or an error `{"message_type": <kind>, "error": ...}`.

Design:
- **One long-lived session** per agent. Our VAD decides what is speech; we
  commit manually at the end of each speech segment (`commit_strategy=manual`).
- **Keepalive**: the server closes after 15 s without a client message, so a
  short silence chunk goes out after `keepalive_s` of no traffic.
- **Chunking**: 32 ms VAD frames are batched into ≥96 ms chunks (the API wants
  0.1–1 s); a commit flushes whatever is buffered.
- **Errors are classified**: fatal ones (bad key, quota, terms) stop the
  client and mark speech degraded; transient ones reconnect with backoff;
  protocol bugs are logged once.
- **Privacy**: transcript text is handed to the caller and never logged here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlencode

import websockets

from agent.speech.vad import FRAME_BYTES, SAMPLE_RATE

logger = logging.getLogger(__name__)

DEFAULT_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
MIN_CHUNK_BYTES = 3 * FRAME_BYTES  # 96 ms
MAX_CHUNK_BYTES = SAMPLE_RATE * 2  # 1 s
KEEPALIVE_S = 5.0
# Close the session after this long without speech: realtime STT bills session time.
IDLE_CLOSE_S = 60.0
KEEPALIVE_CHUNK = bytes(MIN_CHUNK_BYTES)


@dataclass(frozen=True)
class ScribeConfig:
    api_key: str = field(repr=False)
    model_id: str = "scribe_v2_realtime"
    language_code: str = "es"
    keyterms: tuple[str, ...] = ()
    url: str = DEFAULT_URL

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY is required for speech mode")
        if len(self.keyterms) > 50 or sum(map(len, self.keyterms)) > 1000:
            raise ValueError("Scribe realtime accepts at most 50 keyterms / 1000 characters")

    def connect_url(self) -> str:
        params: list[tuple[str, str]] = [
            ("model_id", self.model_id),
            ("audio_format", f"pcm_{SAMPLE_RATE}"),
            ("language_code", self.language_code),
            ("commit_strategy", "manual"),
        ]
        params += [("keyterms", term) for term in self.keyterms]
        return f"{self.url}?{urlencode(params)}"

    def headers(self) -> dict[str, str]:
        return {"xi-api-key": self.api_key}


# ------------------------------------------------------------------ events


@dataclass(frozen=True)
class SessionStarted:
    session_id: str


@dataclass(frozen=True)
class PartialTranscript:
    text: str


@dataclass(frozen=True)
class CommittedTranscript:
    text: str
    language_code: str | None = None


class ErrorClass(StrEnum):
    FATAL = "fatal"  # stop: nothing will work until a human acts
    TRANSIENT = "transient"  # reconnect with backoff
    IGNORABLE = "ignorable"  # informational, keep the session


_ERROR_CLASS: dict[str, ErrorClass] = {
    "auth_error": ErrorClass.FATAL,
    "quota_exceeded": ErrorClass.FATAL,
    "unaccepted_terms": ErrorClass.FATAL,
    "rate_limited": ErrorClass.TRANSIENT,
    "queue_overflow": ErrorClass.TRANSIENT,
    "resource_exhausted": ErrorClass.TRANSIENT,
    "session_time_limit_exceeded": ErrorClass.TRANSIENT,
    "transcriber_error": ErrorClass.TRANSIENT,
    "error": ErrorClass.TRANSIENT,
    "commit_throttled": ErrorClass.IGNORABLE,
    "insufficient_audio_activity": ErrorClass.IGNORABLE,
    # Our bug if these happen; keep the session but say so loudly.
    "input_error": ErrorClass.IGNORABLE,
    "invalid_request": ErrorClass.IGNORABLE,
    "chunk_size_exceeded": ErrorClass.IGNORABLE,
}


@dataclass(frozen=True)
class ScribeError:
    kind: str
    detail: str
    error_class: ErrorClass


ScribeEvent = SessionStarted | PartialTranscript | CommittedTranscript | ScribeError


def parse_event(raw: str | bytes) -> ScribeEvent | None:
    """Anti-corruption layer: provider JSON -> our typed events. Unknown types are ignored."""
    try:
        data: Any = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ScribeError("malformed", "non-JSON frame", ErrorClass.IGNORABLE)
    if not isinstance(data, dict):
        return None
    kind = data.get("message_type")
    if kind == "session_started":
        return SessionStarted(session_id=str(data.get("session_id", "")))
    if kind == "partial_transcript":
        return PartialTranscript(text=str(data.get("text", "")))
    if kind in ("committed_transcript", "committed_transcript_with_timestamps"):
        lang = data.get("language_code")
        return CommittedTranscript(
            text=str(data.get("text", "")), language_code=lang if isinstance(lang, str) else None
        )
    if isinstance(kind, str) and (kind in _ERROR_CLASS or "error" in data):
        detail = data.get("error") or data.get("message") or ""
        return ScribeError(kind, str(detail)[:300], _ERROR_CLASS.get(kind, ErrorClass.TRANSIENT))
    return None


def audio_chunk(pcm: bytes, *, commit: bool = False) -> str:
    return json.dumps(
        {
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm).decode("ascii"),
            "commit": commit,
            "sample_rate": SAMPLE_RATE,
        }
    )


# ------------------------------------------------------------------ client


class _Socket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


type Connect = Callable[[str, dict[str, str]], Awaitable[_Socket]]
type EventHandler = Callable[[ScribeEvent], None]


async def _default_connect(url: str, headers: dict[str, str]) -> _Socket:
    return await websockets.connect(url, additional_headers=headers, max_size=2**20)  # type: ignore[return-value]


class ScribeFatalError(RuntimeError):
    pass


@dataclass
class ScribeStats:
    sessions: int = 0
    chunks_sent: int = 0
    commits: int = 0
    keepalives: int = 0
    transient_errors: int = 0
    dropped_audio_bytes: int = 0
    idle_closes: int = 0


class ScribeRealtime:
    """Keeps one Scribe session alive; `send_audio`/`commit` never block on the network."""

    def __init__(
        self,
        config: ScribeConfig,
        on_event: EventHandler,
        *,
        connect: Connect = _default_connect,
        keepalive_s: float = KEEPALIVE_S,
        idle_close_s: float | None = IDLE_CLOSE_S,
        backoff: Sequence[float] = (0.5, 1, 2, 4, 8),
        max_buffer_bytes: int = 10 * SAMPLE_RATE * 2,  # 10 s
    ) -> None:
        self._config = config
        self._on_event = on_event
        self._connect = connect
        self._keepalive_s = keepalive_s
        self._idle_close_s = idle_close_s
        self._last_audio = time.monotonic()
        self._idle_closed = False
        self._backoff = tuple(backoff)
        self._max_buffer = max_buffer_bytes
        self._outbox: deque[str] = deque()
        self._wakeup = asyncio.Event()
        self._pending = bytearray()
        self._connected = asyncio.Event()
        self._fatal: ScribeError | None = None
        self.stats = ScribeStats()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def fatal(self) -> ScribeError | None:
        return self._fatal

    async def wait_connected(self, timeout: float) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    def send_audio(self, pcm: bytes) -> None:
        if self._fatal is not None:
            return
        self._last_audio = time.monotonic()
        self._pending += pcm
        while len(self._pending) >= MIN_CHUNK_BYTES:
            take = min(len(self._pending), MAX_CHUNK_BYTES)
            self._enqueue(audio_chunk(bytes(self._pending[:take])))
            del self._pending[:take]

    def commit(self) -> None:
        """End of a speech segment: flush the tail and ask for a committed transcript."""
        if self._fatal is not None:
            return
        self._last_audio = time.monotonic()
        tail = bytes(self._pending) or KEEPALIVE_CHUNK
        self._pending.clear()
        self._enqueue(audio_chunk(tail, commit=True))
        self.stats.commits += 1

    def _enqueue(self, message: str) -> None:
        # Bound memory while offline: drop the oldest audio, never block the mic.
        while len(self._outbox) * MIN_CHUNK_BYTES >= self._max_buffer:
            self._outbox.popleft()
            self.stats.dropped_audio_bytes += MIN_CHUNK_BYTES
        self._outbox.append(message)
        self._wakeup.set()

    async def run(self) -> None:
        """Run until a fatal error (raised) or cancellation.

        Lazy: no session is open (and nothing is billed) until there is audio
        to send; after `idle_close_s` without speech the session closes again.
        """
        attempt = 0
        while True:
            if self._idle_close_s is not None and not self._outbox:
                await self._wait_for_audio()
            try:
                socket = await self._connect(self._config.connect_url(), self._config.headers())
            except (OSError, websockets.WebSocketException) as exc:
                await self._sleep(attempt, f"connect failed: {type(exc).__name__}")
                attempt += 1
                continue
            self.stats.sessions += 1
            try:
                attempt = 0 if await self._session(socket) else attempt + 1
            except (OSError, websockets.WebSocketException) as exc:
                logger.warning("scribe session dropped: %s", type(exc).__name__)
                attempt += 1
            finally:
                self._connected.clear()
                try:
                    await socket.close()
                except Exception:  # closing a dead socket must never raise
                    pass
            if self._fatal is not None:
                raise ScribeFatalError(f"{self._fatal.kind}: {self._fatal.detail}")
            if self._idle_closed:
                self._idle_closed = False
                attempt = 0
                continue  # a deliberate close: wait for speech, no backoff
            await self._sleep(attempt, "reconnecting")

    async def _wait_for_audio(self) -> None:
        while not self._outbox:
            self._wakeup.clear()
            await self._wakeup.wait()

    async def _session(self, socket: _Socket) -> bool:
        """Returns True if the session was healthy (reset the backoff)."""
        reader = asyncio.create_task(self._read(socket))
        healthy = False
        try:
            while True:
                if not self._outbox:
                    self._wakeup.clear()
                    waiter = asyncio.create_task(self._wakeup.wait())
                    done, _ = await asyncio.wait(
                        {waiter, reader},
                        timeout=self._keepalive_s,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    waiter.cancel()
                    if reader in done:
                        return (reader.result() is not False) and healthy
                    if not done:
                        idle_for = time.monotonic() - self._last_audio
                        if self._idle_close_s is not None and idle_for >= self._idle_close_s:
                            self._idle_closed = True
                            self.stats.idle_closes += 1
                            return True  # nobody is talking: stop paying for the session
                        await socket.send(audio_chunk(KEEPALIVE_CHUNK))  # keep it alive
                        self.stats.keepalives += 1
                    continue
                if reader.done():
                    return (reader.result() is not False) and healthy
                await socket.send(self._outbox[0])
                self._outbox.popleft()  # only after a successful send
                self.stats.chunks_sent += 1
                healthy = healthy or self.connected
        finally:
            reader.cancel()

    async def _read(self, socket: _Socket) -> bool:
        while True:
            event = parse_event(await socket.recv())
            if event is None:
                continue
            if isinstance(event, SessionStarted):
                self._connected.set()
            elif isinstance(event, ScribeError):
                if event.error_class is ErrorClass.FATAL:
                    self._fatal = event
                    logger.error("scribe fatal error: %s", event.kind)
                    self._on_event(event)
                    return False
                if event.error_class is ErrorClass.TRANSIENT:
                    self.stats.transient_errors += 1
                    logger.warning("scribe transient error: %s", event.kind)
                    self._on_event(event)
                    return False
                logger.info("scribe notice: %s", event.kind)
            self._on_event(event)

    async def _sleep(self, attempt: int, why: str) -> None:
        ceiling = self._backoff[min(attempt, len(self._backoff) - 1)]
        delay = ceiling / 2 + random.random() * ceiling / 2
        logger.info("scribe %s; retry in %.2fs", why, delay)
        await asyncio.sleep(delay)
