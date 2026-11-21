"""Write-behind Postgres sink: append rows from the hot path without ever waiting on the DB.

`submit` enqueues and returns at once. One daemon thread owns one connection and
commits each batch in a single transaction. If the queue is full the rows are
dropped and counted (the in-memory record still has them, and the console
keeps working). A lost connection is reopened with backoff. Rows are
appended with ON CONFLICT DO NOTHING on their natural key, so a replay after a
reconnect never duplicates.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

type Statement = tuple[str, Sequence[Any]]
type Connect = Callable[[], Any]  # returns a DB-API connection (psycopg2)


@dataclass
class WriteBehindStats:
    written: int = 0
    dropped: int = 0  # queue full: never written
    rejected: int = 0  # the database refused the data (bad row): skipped, logged
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
        transient: Callable[[BaseException], bool] = _transient_by_default,
    ) -> None:
        self._connect = connect
        self._transient = transient
        self._queue: queue.Queue[tuple[Statement, ...] | None] = queue.Queue(capacity)
        self._max_batch = max_batch
        self._closing = False
        self.stats = WriteBehindStats()
        self._conn: Any = None
        self._thread = threading.Thread(target=self._run, name=f"write-behind-{name}", daemon=True)
        self._thread.start()

    def submit(self, statements: tuple[Statement, ...]) -> None:
        """Never blocks. One call is one unit (written in one transaction)."""
        try:
            self._queue.put_nowait(statements)
        except queue.Full:
            self.stats.dropped += len(statements)

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until everything submitted so far is written (tests, shutdown)."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self._queue.unfinished_tasks

    def close(self, timeout: float = 5.0) -> None:
        self.flush(timeout)
        self._closing = True  # stop retrying a dead database at shutdown
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            unit = self._queue.get()
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
            if not self._write(batch):
                for single in batch:  # find and skip only the unit the DB refuses
                    if not self._write([single]):
                        self.stats.rejected += len(single)
                        logger.error(
                            "write-behind %s rejected a unit: %s",
                            self._thread.name,
                            self.stats.last_error,
                        )
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
                    self.stats.dropped += sum(len(item) for item in batch)
                    return True  # shutting down: give up instead of hanging
                time.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
