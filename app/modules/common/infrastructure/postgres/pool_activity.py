"""Low-volume pool traffic attribution; counters never contain SQL or tenant data."""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from typing import Literal

PoolOperation = Literal["checkout", "reset", "health_check", "cleanup_rollback", "discard"]
_TIMING_BUCKETS_MS = (1, 5, 25, 100, 500, 2000, 10000)


class PoolActivity:
    """One log per active pool per minute, including otherwise silent idle polls.

    These count pool operations, not business transactions or SQL statements.
    In autocommit mode RESET ALL and SELECT 1 each create a database transaction;
    grouping them separately prevents mistaking hygiene traffic for real work.
    """

    def __init__(self, *, role: str, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._role = role
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._counts: Counter[str] = Counter()
        self._calls: dict[str, dict[str, int | float]] = {}
        self._checked_out: dict[int, float] = {}
        self._high_water = 0
        self._timing_buckets: dict[str, Counter[str]] = {}
        self._lock = threading.Lock()

    def acquired(self, connection_id: int, *, waited_seconds: float) -> None:
        self._record(acquired=connection_id, call="pool_wait", elapsed=waited_seconds)

    def returned(self, connection_id: int) -> None:
        self._record(returned=connection_id)

    def record(self, operation: PoolOperation) -> None:
        self._record(operation=operation)

    def record_call(self, operation: str, *, elapsed: float, failed: bool) -> None:
        """Driver calls, not SQL statements or server transaction counts.

        A multi-statement execute/executemany can create several transactions;
        a commit/rollback on an idle connection can create none. Keep those
        counts distinct from pool hygiene and never retain query text/values.
        """
        self._record(call=operation, elapsed=elapsed, failed=failed)

    def _record(
        self,
        *,
        operation: str | None = None,
        call: str | None = None,
        elapsed: float = 0,
        failed: bool = False,
        acquired: int | None = None,
        returned: int | None = None,
    ) -> None:
        now = self._monotonic()
        with self._lock:
            if acquired is not None:
                self._checked_out[acquired] = now
                self._high_water = max(self._high_water, len(self._checked_out))
            if returned is not None:
                started = self._checked_out.pop(returned, None)
                if started is not None:
                    call, elapsed = "connection_hold", now - started
            if operation is not None:
                self._counts[operation] += 1
            if call is not None:
                item = self._calls.setdefault(
                    call, {"count": 0, "failed": 0, "total_ms": 0.0, "max_ms": 0.0}
                )
                item["count"] += 1
                item["failed"] += int(failed)
                item["total_ms"] += elapsed * 1000
                item["max_ms"] = max(item["max_ms"], elapsed * 1000)
                if call in {"pool_wait", "connection_hold"}:
                    bucket = next(
                        (str(limit) for limit in _TIMING_BUCKETS_MS if elapsed * 1000 <= limit),
                        "+Inf",
                    )
                    self._timing_buckets.setdefault(call, Counter())[bucket] += 1
            elapsed = now - self._started_at
            if elapsed < 60:
                return
            counts = dict(self._counts)
            calls = self._calls
            occupancy = {"checked_out": len(self._checked_out), "high_water": self._high_water}
            timing_buckets = {name: dict(counts) for name, counts in self._timing_buckets.items()}
            self._timing_buckets.clear()
            self._high_water = len(self._checked_out)
            self._counts.clear()
            self._calls = {}
            self._started_at = now
        logging.getLogger(__name__).info(
            "postgres_pool_activity",
            extra={
                "pool_role": self._role,
                "interval_seconds": round(elapsed, 3),
                "operations": counts,
                "calls": calls,
                "pool_occupancy": occupancy,
                # Non-cumulative bucket counts, upper bounds in milliseconds.
                "pool_timing_buckets": timing_buckets,
            },
        )
