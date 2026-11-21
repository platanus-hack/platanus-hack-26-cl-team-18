"""Framework admission observations; core timings live in the application layer."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps

import anyio.to_thread

from app.modules.common.application.request_performance import (
    RequestPerformance,
    RequestStage,
    finish_stage,
    mark_stage,
    observe_request,
    record_stage,
    timed_stage,
)

__all__ = [
    "RequestPerformance",
    "RequestStage",
    "finish_stage",
    "mark_stage",
    "observe_request",
    "observed_handler",
    "record_stage",
    "run_auth_sync",
    "run_dependency_sync",
    "timed_stage",
]


async def run_auth_sync[R](function: Callable[[], R]) -> R:
    """Observe auth admission without replacing AnyIO's shared limiter."""
    queued = time.perf_counter()

    def admitted() -> R:
        record_stage(RequestStage.AUTH_THREAD_WAIT, time.perf_counter() - queued)
        return function()

    return await anyio.to_thread.run_sync(admitted)


async def run_dependency_sync[R](function: Callable[[], R]) -> R:
    """Separate dependency worker admission from work inside the dependency.

    Keep AnyIO's shared limiter and ContextVar propagation, like normal sync
    FastAPI dependencies. This does not change thread or request capacity.
    """
    queued = time.perf_counter()

    def admitted() -> R:
        record_stage(RequestStage.DEPENDENCY_THREAD_WAIT, time.perf_counter() - queued)
        return function()

    return await anyio.to_thread.run_sync(admitted)


def observed_handler[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Attribute the auth-handoff/dependency gap and the synchronous handler.

    Admission includes validation, dependency resolution and worker scheduling;
    it must not be interpreted as pure AnyIO queue wait. Streaming is untouched.
    """

    @wraps(function)
    def measured(*args: P.args, **kwargs: P.kwargs) -> R:
        finish_stage(RequestStage.HANDLER_ADMISSION)
        started = time.perf_counter()
        try:
            result = function(*args, **kwargs)
        finally:
            record_stage(RequestStage.HANDLER_EXECUTION, time.perf_counter() - started)
        mark_stage(RequestStage.RESPONSE_PREPARATION)
        return result

    return measured
