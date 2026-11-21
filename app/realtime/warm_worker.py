"""Warm-path task runner (docs/stack.md §4, ADR 0001).

Warm work (VLM tags, Claude copy, ElevenLabs voice-overs, fal images/video)
runs as asyncio tasks owned by this worker, never awaited by hot-path code:

- **Bounded concurrency per provider** (a semaphore each), so a burst of
  moments can't fan out 40 parallel calls to one API.
- **Dedupe by cache key while in flight**: submitting the same key twice runs
  the job once (e.g. the same (copy, voice) voice-over).
- **Isolation**: a failing job is logged and counted; it never propagates.
- Sync handlers (worker threads) use `submit_threadsafe`.

Results upgrade the moment or creative *in place* via the job itself (it
publishes `creative.upgraded` through its context's port).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

type WarmJob = Callable[[], Awaitable[None]]
type WarmState = Literal["submitted", "deduped", "running", "done", "failed", "rejected"]


@dataclass(frozen=True, slots=True)
class WarmEvent:
    """One step of a warm job's life, for observers such as the `/dev` console.

    Carries the provider and cache key only: never the job's inputs or outputs,
    and on failure the exception type, never its message.
    """

    provider: str
    key: str
    state: WarmState
    elapsed_ms: float | None = None
    error_type: str | None = None
    in_flight: Mapping[str, int] = field(default_factory=dict)


type WarmObserver = Callable[[WarmEvent], None]

DEFAULT_LIMIT = 2


@dataclass
class WarmStats:
    submitted: int = 0
    deduped: int = 0
    completed: int = 0
    failed: int = 0
    rejected: int = 0
    in_flight: dict[str, int] = field(default_factory=dict)


class WarmWorker:
    def __init__(
        self, limits: Mapping[str, int] | None = None, *, default_limit: int = DEFAULT_LIMIT
    ) -> None:
        self._limits = dict(limits or {})
        self._default_limit = default_limit
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self.stats = WarmStats()
        self._observer: WarmObserver | None = None

    def observe(self, observer: WarmObserver | None) -> None:
        """Report every job transition to `observer` (it must be cheap and never raise)."""
        self._observer = observer

    def note_refused(self, provider: str, key: str) -> None:
        """A gate in front of the worker refused a job (e.g. the live switch is off)."""
        self._emit(provider, key, "rejected")

    def _emit(
        self,
        provider: str,
        key: str,
        state: WarmState,
        *,
        elapsed_ms: float | None = None,
        error_type: str | None = None,
    ) -> None:
        if self._observer is None:
            return
        try:
            self._observer(
                WarmEvent(
                    provider=provider,
                    key=key,
                    state=state,
                    elapsed_ms=elapsed_ms,
                    error_type=error_type,
                    in_flight=dict(self.stats.in_flight),
                )
            )
        except Exception:  # an observer must never break warm work
            logger.exception("warm observer failed")

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def submit(self, provider: str, key: str, job: WarmJob) -> bool:
        """Schedule `job` on the running loop. Returns False if deduped or closed."""
        if self._closed:
            self.stats.rejected += 1
            self._emit(provider, key, "rejected")
            return False
        dedupe_key = f"{provider}:{key}"
        if dedupe_key in self._in_flight:
            self.stats.deduped += 1
            self._emit(provider, key, "deduped")
            return False
        self.stats.submitted += 1
        self._emit(provider, key, "submitted")
        task = asyncio.get_running_loop().create_task(
            self._run(provider, dedupe_key, job), name=f"warm:{dedupe_key}"
        )
        self._in_flight[dedupe_key] = task
        return True

    def submit_threadsafe(self, provider: str, key: str, job: WarmJob) -> None:
        if self._loop is None or self._loop.is_closed():
            self.stats.rejected += 1
            logger.warning("warm worker has no loop; job dropped", extra={"source": provider})
            return
        self._loop.call_soon_threadsafe(self.submit, provider, key, job)

    async def drain(self) -> None:
        """Wait for everything in flight (tests, graceful shutdown)."""
        while self._in_flight:
            await asyncio.gather(*list(self._in_flight.values()), return_exceptions=True)

    async def aclose(self) -> None:
        self._closed = True
        for task in list(self._in_flight.values()):
            task.cancel()
        await asyncio.gather(*list(self._in_flight.values()), return_exceptions=True)

    def _semaphore(self, provider: str) -> asyncio.Semaphore:
        sem = self._semaphores.get(provider)
        if sem is None:
            sem = asyncio.Semaphore(self._limits.get(provider, self._default_limit))
            self._semaphores[provider] = sem
        return sem

    async def _run(self, provider: str, dedupe_key: str, job: WarmJob) -> None:
        key = dedupe_key.removeprefix(f"{provider}:")
        started: float | None = None
        try:
            async with self._semaphore(provider):
                self.stats.in_flight[provider] = self.stats.in_flight.get(provider, 0) + 1
                started = time.monotonic()
                self._emit(provider, key, "running")
                try:
                    await job()
                    self.stats.completed += 1
                finally:
                    self.stats.in_flight[provider] -= 1
            self._emit(provider, key, "done", elapsed_ms=_since(started))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.stats.failed += 1
            logger.exception("warm job failed", extra={"source": provider})
            self._emit(
                provider,
                key,
                "failed",
                elapsed_ms=_since(started),
                error_type=type(exc).__name__,
            )
        finally:
            self._in_flight.pop(dedupe_key, None)


def _since(started: float | None) -> float | None:
    return None if started is None else (time.monotonic() - started) * 1000


class GatedWarm:
    """One screen's view of the worker: jobs are refused while `allowed()` is false.

    The live switch (live_switch.py) gates warm AI per screen: in standby no Claude
    copy, VLM tag, speech topic or voice-over job starts, so nothing is spent. A job
    already in flight finishes, and its in-place upgrade is discarded as stale.
    """

    def __init__(self, inner: WarmWorker, allowed: Callable[[], bool]) -> None:
        self._inner = inner
        self._allowed = allowed
        self.refused = 0

    def submit_threadsafe(self, provider: str, key: str, job: WarmJob) -> None:
        if not self._allowed():
            self.refused += 1
            self._inner.note_refused(provider, key)
            return
        self._inner.submit_threadsafe(provider, key, job)
