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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

type WarmJob = Callable[[], Awaitable[None]]

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

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def submit(self, provider: str, key: str, job: WarmJob) -> bool:
        """Schedule `job` on the running loop. Returns False if deduped or closed."""
        if self._closed:
            self.stats.rejected += 1
            return False
        dedupe_key = f"{provider}:{key}"
        if dedupe_key in self._in_flight:
            self.stats.deduped += 1
            return False
        self.stats.submitted += 1
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
        try:
            async with self._semaphore(provider):
                self.stats.in_flight[provider] = self.stats.in_flight.get(provider, 0) + 1
                try:
                    await job()
                    self.stats.completed += 1
                finally:
                    self.stats.in_flight[provider] -= 1
        except asyncio.CancelledError:
            raise
        except Exception:
            self.stats.failed += 1
            logger.exception("warm job failed", extra={"source": provider})
        finally:
            self._in_flight.pop(dedupe_key, None)


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
            return
        self._inner.submit_threadsafe(provider, key, job)
