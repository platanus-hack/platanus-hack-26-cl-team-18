from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

import psycopg2

logger = logging.getLogger(__name__)

# Postgres concurrency errors that are safely retryable when the retried callable
# runs a full transaction from scratch (open UoW -> work -> commit). On one of
# these, the transaction rolled back and nothing was persisted, so re-running the
# whole unit is correct.
#   - SerializationFailure (40001): SERIALIZABLE pivot abort or read/write cycle
#   - DeadlockDetected     (40P01): deadlock victim
RETRYABLE_DB_ERRORS: tuple[type[BaseException], ...] = (
    psycopg2.errors.SerializationFailure,
    psycopg2.errors.DeadlockDetected,
)

# One retry budget for every transaction runner, in and out of the MessageBus
# (which imports these). The standalone helpers used to get 3 attempts inside a
# flat 10-50ms window while the bus got 5 with exponential backoff; on 2026-09-22
# the idempotency helpers exhausted that shorter budget 34 times in one hour.
DEFAULT_RETRY_ATTEMPTS = 5
DEFAULT_BACKOFF_MIN_SECONDS = 0.025
DEFAULT_BACKOFF_MAX_SECONDS = 0.250

T = TypeVar("T")


def run_with_serialization_retry(
    fn: Callable[[], T],
    *,
    label: str,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    backoff_min_seconds: float = DEFAULT_BACKOFF_MIN_SECONDS,
    backoff_max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS,
) -> T:
    """Retry ``fn`` on transient Postgres concurrency errors with jittered exponential backoff.

    Only safe to use when each invocation of ``fn`` runs a self-contained
    transaction (opens its own UnitOfWork and commits). A SerializationFailure /
    DeadlockDetected means the transaction rolled back and nothing was persisted,
    so re-running ``fn`` from scratch is correct.

    Mirrors ``MessageBus._run_with_serialization_retry`` so callers that run
    transactions OUTSIDE the bus (e.g. the HTTP idempotency helpers) get the same
    robustness instead of surfacing a 40001 as a 500.

    After exhausting attempts, the last exception propagates unchanged.
    """
    total = max(1, attempts)
    last_exc: BaseException | None = None
    for attempt in range(1, total + 1):
        try:
            return fn()
        except RETRYABLE_DB_ERRORS as exc:
            last_exc = exc
            if attempt >= total:
                logger.warning(
                    "serialization_retry.exhausted label=%s attempts=%d error=%s",
                    label,
                    attempt,
                    type(exc).__name__,
                )
                raise
            backoff_ceiling = min(
                backoff_max_seconds,
                backoff_min_seconds * (2 ** (attempt - 1)),
            )
            backoff = random.uniform(backoff_min_seconds, backoff_ceiling)
            logger.info(
                "serialization_retry.retrying label=%s attempt=%d/%d error=%s backoff_s=%.4f",
                label,
                attempt,
                total,
                type(exc).__name__,
                backoff,
            )
            time.sleep(backoff)
    # Unreachable: loop either returns or raises.
    if last_exc is None:
        raise RuntimeError("serialization retry loop exited without capturing an exception")
    raise last_exc
