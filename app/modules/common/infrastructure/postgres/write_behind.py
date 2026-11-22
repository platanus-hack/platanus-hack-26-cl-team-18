"""Write-behind Postgres sink: append rows from the hot path without ever waiting on the DB.

`submit` enqueues and returns at once. One daemon thread owns one connection and
commits each batch in a single transaction. If the queue is full, units spill
into a bounded overflow (`overflow_capacity`) that the writer drains in order
after the queue; only past both are rows dropped, counted and logged at ERROR
(the in-memory record still has them, and the console keeps working). Money
writers (charges) must be built with an overflow. `close` (app shutdown) flushes
both before stopping. A lost connection is reopened with backoff. Rows are
appended with ON CONFLICT DO NOTHING on their natural key, so a replay after a
reconnect never duplicates.

With a `spill` file (spill.py), a unit `durable` says must survive (charges) is
appended there instead of dropped: past a full overflow, at shutdown with the
database unreachable, and whatever is still queued when `close` gives up. The next
start replays the file before serving (`replay_spill`).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.modules.common.infrastructure.postgres.spill import SpillFile

logger = logging.getLogger(__name__)

type Statement = tuple[str, Sequence[Any]]
type Connect = Callable[[], Any]  # returns a DB-API connection (psycopg2)


@dataclass
class WriteBehindStats:
    written: int = 0
    dropped: int = 0  # queue and overflow full: never written
    overflowed: int = 0  # queue full: spilled to the overflow (still written)
    rejected: int = 0  # the database refused the data (bad row): skipped, logged
    spilled: int = 0  # durable rows kept in the spill file instead of dropped
    failures: int = 0
    last_error: str | None = field(default=None)


def _transient_by_default(exc: BaseException) -> bool:
    return isinstance(exc, ConnectionError | TimeoutError | OSError)


class WriteBehindWriter:
    """See the module docstring. `transient` says which errors mean "reconnect and
    retry"; any other error is the data's fault, and only the offending unit is
    skipped so one bad row can never block every write after it."""

    def __init__(
        self,
        connect: Connect,
        *,
        name: str,
        capacity: int = 10_000,
        max_batch: int = 200,
        overflow_capacity: int = 0,
        transient: Callable[[BaseException], bool] = _transient_by_default,
        spill: SpillFile | None = None,
        durable: Callable[[tuple[Statement, ...]], bool] = lambda _unit: True,
    ) -> None:
        self._connect = connect
        self._spill = spill
        self._durable = durable
        self._transient = transient
        self._queue: queue.Queue[tuple[Statement, ...] | None] = queue.Queue(capacity)
        self._max_batch = max_batch
        # Units that found the queue full, oldest first. While it holds anything, new
        # units join it too, so the database still sees them in submission order.
        self._overflow: deque[tuple[Statement, ...]] = deque()
        self._overflow_capacity = overflow_capacity
        self._overflow_lock = threading.Lock()
        self._overflow_in_flight = 0
        self._last_drop_log = 0.0
        self._closing = False
        self.stats = WriteBehindStats()
        self._conn: Any = None
        # The batch the writer thread is committing right now (close spills it too).
        self._writing: list[tuple[Statement, ...]] = []
        self._thread = threading.Thread(target=self._run, name=f"write-behind-{name}", daemon=True)
        self._thread.start()

    def submit(self, statements: tuple[Statement, ...]) -> None:
        """Never blocks (beyond a short lock). One call is one unit (one transaction)."""
        with self._overflow_lock:
            if not self._overflow:
                try:
                    self._queue.put_nowait(statements)
                    return
                except queue.Full:
                    pass
            if len(self._overflow) < self._overflow_capacity:
                self._overflow.append(statements)
                self.stats.overflowed += len(statements)
                return
        if self._spill_or_drop([statements]):
            self._log_drop()

    def _spill_or_drop(self, units: list[tuple[Statement, ...]]) -> int:
        """Spill the durable units (if a spill file is set), count the rest as dropped.
        Returns the rows dropped."""
        keep = [unit for unit in units if self._spill is not None and self._durable(unit)]
        spilled = 0
        if keep and self._spill is not None:
            try:
                spilled = self._spill.append(keep)
            except OSError as exc:
                logger.error("write-behind %s could not spill: %s", self._thread.name, exc)
        dropped = sum(len(unit) for unit in units) - spilled
        with self._overflow_lock:
            self.stats.spilled += spilled
            self.stats.dropped += dropped
        return dropped

    def _log_drop(self) -> None:
        now = time.monotonic()
        if now - self._last_drop_log < 5.0:
            return  # one line per 5 s: a full queue must not also flood the log
        self._last_drop_log = now
        logger.error(
            "write-behind %s is full: rows dropped (dropped=%d, last_error=%s)",
            self._thread.name,
            self.stats.dropped,
            self.stats.last_error,
        )

    def _pending(self) -> bool:
        with self._overflow_lock:
            spilled = bool(self._overflow) or self._overflow_in_flight > 0
        return spilled or self._queue.unfinished_tasks > 0

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until everything submitted so far is written (tests, shutdown)."""
        deadline = time.monotonic() + timeout
        while self._pending() and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self._pending()

    def close(self, timeout: float = 5.0) -> None:
        if not self.flush(timeout):
            logger.error(
                "write-behind %s closed before every row was written (last_error=%s)",
                self._thread.name,
                self.stats.last_error,
            )
        self._closing = True  # stop retrying a dead database at shutdown
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout)
        if self._thread.is_alive() and self._spill is not None:
            # Still stuck on a dead database: keep what never reached it. A unit the
            # thread also writes later lands once (ON CONFLICT DO NOTHING on replay).
            self._spill_or_drop(list(self._writing) + self._drain())

    def _drain(self) -> list[tuple[Statement, ...]]:
        units: list[tuple[Statement, ...]] = []
        while True:
            try:
                unit = self._queue.get_nowait()
            except queue.Empty:
                break
            self._queue.task_done()
            if unit is not None:
                units.append(unit)
        with self._overflow_lock:
            units.extend(self._overflow)
            self._overflow.clear()
        return units

    def _take_overflow(self) -> list[tuple[Statement, ...]]:
        """Overflowed units, once the queue (all older than them) is empty."""
        with self._overflow_lock:
            if not self._overflow or not self._queue.empty():
                return []
            batch = [
                self._overflow.popleft() for _ in range(min(self._max_batch, len(self._overflow)))
            ]
            self._overflow_in_flight = len(batch)
            return batch

    def _write_isolating(self, batch: list[tuple[Statement, ...]]) -> None:
        self._writing = batch
        try:
            self._write_or_isolate(batch)
        finally:
            self._writing = []

    def _write_or_isolate(self, batch: list[tuple[Statement, ...]]) -> None:
        if not self._write(batch):
            for single in batch:  # find and skip only the unit the DB refuses
                if not self._write([single]):
                    self.stats.rejected += len(single)
                    logger.error(
                        "write-behind %s rejected a unit: %s",
                        self._thread.name,
                        self.stats.last_error,
                    )

    def _run(self) -> None:
        while True:
            if spilled := self._take_overflow():
                self._write_isolating(spilled)
                with self._overflow_lock:
                    self._overflow_in_flight = 0
                continue
            try:
                # Overflow only grows while the queue is full, so a blocked get is
                # always woken; the timeout is a safety net, not the mechanism.
                unit = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if unit is None:
                self._queue.task_done()
                break
            batch = [unit]
            stop = False
            while len(batch) < self._max_batch:
                try:
                    more = self._queue.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    self._queue.task_done()
                    stop = True
                    break
                batch.append(more)
            self._write_isolating(batch)
            for _ in batch:
                self._queue.task_done()
            if stop:
                break
        if self._conn is not None:
            self._conn.close()

    def _write(self, batch: list[tuple[Statement, ...]]) -> bool:
        """Commit `batch` in one transaction. False if the data is refused (not retried)."""
        backoff = 0.5
        while True:
            try:
                if self._conn is None:
                    self._conn = self._connect()
                with self._conn, self._conn.cursor() as cur:
                    for statements in batch:
                        for sql, params in statements:
                            cur.execute(sql, params)
                self.stats.written += sum(len(item) for item in batch)
                return True
            except Exception as exc:
                self.stats.failures += 1
                self.stats.last_error = type(exc).__name__
                if not self._transient(exc):
                    return False  # rolled back as a whole; the caller isolates the bad unit
                logger.warning(
                    "write-behind %s lost the database: %s", self._thread.name, type(exc).__name__
                )
                try:
                    if self._conn is not None:
                        self._conn.close()
                except Exception:  # pragma: no cover - best effort
                    pass
                self._conn = None
                if self._closing:
                    dropped = self._spill_or_drop(batch)
                    logger.error(
                        "write-behind %s gave up on %d rows at shutdown: database "
                        "unreachable (%d dropped, the rest spilled)",
                        self._thread.name,
                        sum(len(item) for item in batch),
                        dropped,
                    )
                    return True  # shutting down: give up instead of hanging
                time.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
