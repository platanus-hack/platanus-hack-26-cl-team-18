"""Bounded request-stage observations, propagated through AnyIO thread offloads.

Durations are inclusive and may overlap: SQL inside hydration is not additional
elapsed time. No SQL, values, identities or arbitrary metric keys are retained.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from functools import wraps
from threading import Lock


class RequestStage(StrEnum):
    DEPENDENCY_THREAD_WAIT = "dependency_thread_wait"
    AUTH = "auth"
    AUTH_THREAD_WAIT = "auth_thread_wait"
    HANDLER_ADMISSION = "handler_admission"
    HANDLER_EXECUTION = "handler_execution"
    DB_COMMIT = "db_commit"
    DB_ROLLBACK = "db_rollback"
    DB_TRANSACTION_EXIT = "db_transaction_exit"
    DB_RELEASE = "db_release"
    DB_CHECKOUT = "db_checkout"
    DB_EXECUTE = "db_execute"
    DB_FETCH = "db_fetch"
    HYDRATION = "hydration"
    ROW_FACTS = "row_facts"
    DTO_MAPPING = "dto_mapping"
    RESPONSE_PREPARATION = "response_preparation"


@dataclass(slots=True)
class RequestPerformance:
    _durations: dict[RequestStage, float] = field(default_factory=dict)
    _counts: dict[RequestStage, int] = field(default_factory=dict)
    _marks: dict[RequestStage, float] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def record(self, stage: RequestStage, elapsed: float) -> None:
        with self._lock:
            self._durations[stage] = self._durations.get(stage, 0) + max(0, elapsed)
            self._counts[stage] = self._counts.get(stage, 0) + 1

    def mark(self, stage: RequestStage) -> None:
        with self._lock:
            self._marks[stage] = time.perf_counter()

    def finish(self, stage: RequestStage) -> None:
        with self._lock:
            started = self._marks.pop(stage, None)
        if started is not None:
            self.record(stage, time.perf_counter() - started)

    def fields(self) -> dict[str, object]:
        with self._lock:
            return {
                "stage_ms": {
                    stage.value: round(value * 1000, 3) for stage, value in self._durations.items()
                },
                "stage_calls": {stage.value: count for stage, count in self._counts.items()},
            }


_current: ContextVar[RequestPerformance | None] = ContextVar("request_performance", default=None)


@contextmanager
def observe_request() -> Iterator[RequestPerformance]:
    performance = RequestPerformance()
    token = _current.set(performance)
    try:
        yield performance
    finally:
        _current.reset(token)


def record_stage(stage: RequestStage, elapsed: float) -> None:
    if (performance := _current.get()) is not None:
        performance.record(stage, elapsed)


def mark_stage(stage: RequestStage) -> None:
    if (performance := _current.get()) is not None:
        performance.mark(stage)


def finish_stage(stage: RequestStage) -> None:
    if (performance := _current.get()) is not None:
        performance.finish(stage)


def timed_stage[**P, R](stage: RequestStage) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def measured(*args: P.args, **kwargs: P.kwargs) -> R:
            if _current.get() is None:
                return function(*args, **kwargs)
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                record_stage(stage, time.perf_counter() - started)

        return measured

    return decorate


@contextmanager
def measure_stage(stage: RequestStage) -> Iterator[None]:
    if _current.get() is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        record_stage(stage, time.perf_counter() - started)
