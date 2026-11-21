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
    dropped: int = 0
    failures: int = 0
    last_error: str | None = field(default=None)


class WriteBehindWriter:
    def __init__(
        self, connect: Connect, *, name: str, capacity: int = 10_000, max_batch: int = 200
    ) -> None:
        self._connect = connect
        self._queue: queue.Queue[tuple[Statement, ...] | None] = queue.Queue(capacity)
        self._max_batch = max_batch
        self.stats = WriteBehindStats()
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
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout)

    def _run(self) -> None:
        conn = None
        backoff = 0.5
        while True:
            unit = self._queue.get()
            if unit is None:
                self._queue.task_done()
                break
            batch = [unit]
            while len(batch) < self._max_batch:
                try:
                    more = self._queue.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    self._queue.put_nowait(None)
                    self._queue.task_done()
                    break
                batch.append(more)
            while True:
                try:
                    if conn is None:
                        conn = self._connect()
                    with conn, conn.cursor() as cur:
                        for statements in batch:
                            for sql, params in statements:
                                cur.execute(sql, params)
                    self.stats.written += sum(len(item) for item in batch)
                    backoff = 0.5
                    break
                except Exception as exc:  # reconnect and retry the same batch
                    self.stats.failures += 1
                    self.stats.last_error = type(exc).__name__
                    logger.warning("write-behind %s failed: %s", self._thread.name, type(exc).__name__)
                    try:
                        if conn is not None:
                            conn.close()
                    except Exception:  # pragma: no cover - best effort
                        pass
                    conn = None
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)
            for _ in batch:
                self._queue.task_done()
        if conn is not None:
            conn.close()
