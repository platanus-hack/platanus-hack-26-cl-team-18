"""Bounded driver-call attribution without SQL, parameters or extra DB queries."""

from __future__ import annotations

import time
from functools import lru_cache, wraps
from typing import Any, cast

from psycopg2.extensions import connection, cursor

from app.modules.common.infrastructure.postgres.pool_activity import PoolActivity
from app.modules.common.infrastructure.request_performance import (
    RequestStage,
    record_stage,
    timed_stage,
)


def _observed(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        conn = self if isinstance(self, connection) else self.connection
        activity = getattr(conn, "_activity", None)
        if activity is None:
            return method(self, *args, **kwargs)
        operation = "context_exit" if method.__name__ == "__exit__" else method.__name__
        if not isinstance(self, connection):
            operation = ("autocommit_" if conn.autocommit else "transaction_") + operation
        started = time.monotonic()
        failed = True
        try:
            result = method(self, *args, **kwargs)
            failed = False
            return result
        finally:
            elapsed = time.monotonic() - started
            activity.record_call(operation, elapsed=elapsed, failed=failed)
            if not isinstance(self, connection):
                record_stage(RequestStage.DB_EXECUTE, elapsed)
            else:
                stage = {
                    "commit": RequestStage.DB_COMMIT,
                    "rollback": RequestStage.DB_ROLLBACK,
                    "context_exit": RequestStage.DB_TRANSACTION_EXIT,
                }[operation]
                record_stage(stage, elapsed)

    return call


class _ObservedCursor(cursor):
    @timed_stage(RequestStage.DB_FETCH)
    def fetchall(self, *args, **kwargs):
        return super().fetchall(*args, **kwargs)

    @timed_stage(RequestStage.DB_FETCH)
    def fetchone(self, *args, **kwargs):
        return super().fetchone(*args, **kwargs)

    @timed_stage(RequestStage.DB_FETCH)
    def fetchmany(self, *args, **kwargs):
        return super().fetchmany(*args, **kwargs)

    @_observed
    def execute(self, *args, **kwargs):
        return super().execute(*args, **kwargs)

    @_observed
    def executemany(self, *args, **kwargs):
        return super().executemany(*args, **kwargs)

    @_observed
    def callproc(self, *args, **kwargs):
        return super().callproc(*args, **kwargs)

    @_observed
    def copy_expert(self, *args, **kwargs):
        return super().copy_expert(*args, **kwargs)

    @_observed
    def copy_from(self, *args, **kwargs):
        return super().copy_from(*args, **kwargs)

    @_observed
    def copy_to(self, *args, **kwargs):
        return super().copy_to(*args, **kwargs)


@lru_cache(maxsize=16)
def _cursor_type(factory: type[cursor]) -> type[cursor]:
    # Preserve RealDictCursor/DictCursor/named-cursor behavior. Do not replace
    # a caller's row factory with the base tuple cursor to gather telemetry.
    return type(f"Observed{factory.__name__}", (_ObservedCursor, factory), {})


class ObservedConnection(connection):
    def __init__(self, *args, activity: PoolActivity, **kwargs):
        self._activity = activity
        super().__init__(*args, **kwargs)

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        factory = args[1] if len(args) > 1 else kwargs.get("cursor_factory")
        factory = factory or self.cursor_factory or cursor
        if (
            isinstance(factory, type)
            and issubclass(factory, cursor)
            and not issubclass(factory, _ObservedCursor)
        ):
            observed = _cursor_type(cast(Any, factory))
            if len(args) > 1:
                args = (args[0], observed, *args[2:])
            else:
                kwargs["cursor_factory"] = observed
        # Callable factories remain supported; their calls are not observed.
        return super().cursor(*args, **kwargs)

    @_observed
    def commit(self):
        return super().commit()

    @_observed
    def rollback(self):
        return super().rollback()

    @_observed
    def __exit__(self, *args):
        # Observe the context boundary separately. The driver may call our
        # commit/rollback override inside it, so these inclusive stages can
        # overlap; context exits are not extra transaction round trips.
        return super().__exit__(*args)
